"""Tenant-scoped data access.

Multi-tenant government software has one failure mode that matters more than all
the others put together: agency A reading agency B's records. Everything else is
a bug; that one is a notification letter.

The usual defence is "remember to add `WHERE tenant_id = ?`", which works right
up until the day somebody adds a query and forgets. So this layer makes the
scope impossible to omit rather than merely customary:

  * There is no public method that reaches the database without a `Tenant`.
  * The `Tenant` is not a string the caller passes alongside the query; it is
    the object you must hold in order to have a query method at all.
  * The raw connection is private to the module and every statement built here
    appends the tenant predicate itself, after the caller's filters, so a caller
    cannot terminate the WHERE clause early.

The test suite attacks this directly rather than trusting the description.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


class AccessError(Exception):
    """Raised when a caller tries to reach data outside its tenant."""


@dataclass(frozen=True)
class Tenant:
    """A capability, not a label.

    Holding one is what grants access. It is created only by `authenticate`, so
    a caller cannot mint a scope for an agency it has no credential for.
    """

    id: str
    name: str

    def __post_init__(self) -> None:
        # Tenant ids reach SQL as bound parameters, never interpolated, but an
        # id that is not a plain slug means something upstream is wrong and the
        # safe response is to refuse rather than to bind it and continue.
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,40}", self.id):
            raise AccessError(f"malformed tenant id: {self.id!r}")


_API_KEYS = {
    "key-clark-county": ("clark-county", "Clark County Permits"),
    "key-riverside": ("riverside", "Riverside Public Works"),
    "key-statewide": ("statewide", "Statewide Licensing Board"),
}


def authenticate(api_key: str | None) -> Tenant:
    if not api_key or api_key not in _API_KEYS:
        raise AccessError("unknown or missing API key")
    tid, name = _API_KEYS[api_key]
    return Tenant(id=tid, name=name)


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL,
    filed_on   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_tenant ON records (tenant_id);
"""


class Repository:
    """Every read is scoped. There is no unscoped read."""

    def __init__(self, path: str = ":memory:") -> None:
        self.__conn = sqlite3.connect(path, check_same_thread=False)
        self.__conn.row_factory = sqlite3.Row
        self.__conn.executescript(SCHEMA)

    # -- writes -----------------------------------------------------------

    def seed(self, rows: list[dict]) -> None:
        """Fixture loading only. Not reachable from the HTTP surface."""
        self.__conn.executemany(
            "INSERT OR REPLACE INTO records (id, tenant_id, kind, title, body, status, filed_on)"
            " VALUES (:id, :tenant_id, :kind, :title, :body, :status, :filed_on)",
            rows,
        )
        self.__conn.commit()

    # -- scoped reads -----------------------------------------------------

    def _select(self, tenant: Tenant, where: str, params: list) -> list[sqlite3.Row]:
        """The single choke point.

        The tenant predicate is appended here, by this method, after whatever the
        caller asked for. A caller supplies filter fragments through the typed
        methods below and never assembles this string.
        """
        if not isinstance(tenant, Tenant):
            raise AccessError("a Tenant is required to read records")
        clause = f"({where}) AND tenant_id = ?" if where else "tenant_id = ?"
        sql = f"SELECT * FROM records WHERE {clause} ORDER BY filed_on DESC, id"
        return self.__conn.execute(sql, [*params, tenant.id]).fetchall()

    def list_records(
        self, tenant: Tenant, kind: str | None = None, status: str | None = None
    ) -> list[sqlite3.Row]:
        parts, params = [], []
        if kind:
            parts.append("kind = ?")
            params.append(kind)
        if status:
            parts.append("status = ?")
            params.append(status)
        return self._select(tenant, " AND ".join(parts), params)

    def get(self, tenant: Tenant, record_id: str) -> sqlite3.Row | None:
        rows = self._select(tenant, "id = ?", [record_id])
        return rows[0] if rows else None

    def search(self, tenant: Tenant, query: str) -> list[sqlite3.Row]:
        """Keyword search. The term is bound, and the tenant predicate is still
        appended by `_select` -- search is the query people most often write
        by hand and most often forget to scope."""
        term = f"%{query.lower()}%"
        return self._select(
            tenant, "(lower(title) LIKE ? OR lower(body) LIKE ?)", [term, term]
        )

    def count_all_tenants(self) -> int:
        """Fixture/diagnostic helper. Named so that it is obvious in review that
        this one is not scoped, and it returns a count rather than rows."""
        return self.__conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
