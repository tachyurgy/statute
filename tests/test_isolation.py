"""Tenant isolation, attacked rather than described.

The invariant: no code path reachable by a caller returns a row belonging to a
tenant other than the one whose credential they hold.
"""
from __future__ import annotations

import sqlite3

import pytest

from statute.answers import answer
from statute.repository import AccessError, Repository, Tenant, authenticate
from statute.seed import SEED

CLARK = Tenant("clark-county", "Clark County Permits")
RIVER = Tenant("riverside", "Riverside Public Works")


@pytest.fixture
def repo() -> Repository:
    r = Repository()
    r.seed(SEED)
    return r


# -- authentication -------------------------------------------------------


def test_a_valid_key_yields_its_own_tenant():
    assert authenticate("key-clark-county").id == "clark-county"


def test_an_unknown_key_is_refused():
    with pytest.raises(AccessError):
        authenticate("key-not-real")


def test_a_missing_key_is_refused():
    with pytest.raises(AccessError):
        authenticate(None)


def test_a_malformed_tenant_id_is_refused():
    """A scope object cannot be constructed from junk, so a bug upstream fails
    closed instead of binding something odd into a query."""
    for bad in ["", "UPPER", "with space", "x", "'; DROP TABLE records; --", "a" * 60]:
        with pytest.raises(AccessError):
            Tenant(bad, "whatever")


# -- the invariant --------------------------------------------------------


def test_listing_returns_only_this_tenants_records(repo):
    for tenant in (CLARK, RIVER):
        rows = repo.list_records(tenant)
        assert rows, "fixture should give every tenant some records"
        assert {r["tenant_id"] for r in rows} == {tenant.id}


def test_no_tenant_can_see_every_record(repo):
    total = repo.count_all_tenants()
    for tenant in (CLARK, RIVER):
        assert len(repo.list_records(tenant)) < total


def test_fetching_another_tenants_record_by_id_returns_nothing(repo):
    """The direct attack: take a real id from one agency and ask another for it."""
    other = repo.list_records(RIVER)[0]["id"]
    assert repo.get(RIVER, other) is not None
    assert repo.get(CLARK, other) is None


def test_search_cannot_reach_across_tenants(repo):
    """Search is the query most often hand-written and most often unscoped."""
    river_only = repo.list_records(RIVER)[0]["title"].split()[0]
    hits = repo.search(CLARK, river_only)
    assert all(h["tenant_id"] == CLARK.id for h in hits)


def test_filters_cannot_escape_the_scope(repo):
    """Caller-supplied filters are ANDed *before* the tenant predicate is
    appended, so no filter value can terminate the clause early."""
    for probe in ["' OR '1'='1", "') OR (tenant_id!='x", "%", "_"]:
        for rows in (
            repo.list_records(CLARK, kind=probe),
            repo.list_records(CLARK, status=probe),
            repo.search(CLARK, probe),
        ):
            assert all(r["tenant_id"] == CLARK.id for r in rows)


def test_a_non_tenant_scope_is_refused(repo):
    """Passing a bare string where a Tenant is required must not work."""
    with pytest.raises(AccessError):
        repo._select("clark-county", "", [])  # type: ignore[arg-type]


def test_there_is_no_public_unscoped_read(repo):
    """Every public method that returns rows takes a Tenant as its first
    argument. `count_all_tenants` returns a count, not rows, and is named to be
    obvious in review."""
    public = [
        n
        for n in dir(repo)
        if not n.startswith("_") and callable(getattr(repo, n)) and n not in {"seed"}
    ]
    assert set(public) == {"list_records", "get", "search", "count_all_tenants"}


def test_the_connection_is_not_reachable_from_outside(repo):
    """Name mangling on the connection means a caller cannot casually run their
    own SQL through the repository."""
    assert not hasattr(repo, "conn")
    assert not any(
        isinstance(getattr(repo, n, None), sqlite3.Connection)
        for n in dir(repo)
        if not n.startswith("_Repository__")
    )


# -- grounded answers -----------------------------------------------------


def test_answers_cite_only_this_tenants_records(repo):
    a = answer(repo, CLARK, "permit")
    assert a.grounded
    ids = {c.record_id for c in a.citations}
    mine = {r["id"] for r in repo.list_records(CLARK)}
    assert ids <= mine


def test_answer_refuses_when_nothing_matches(repo):
    a = answer(repo, CLARK, "zzzzquux")
    assert not a.grounded
    assert a.citations == []
    assert a.refused_reason


def test_answer_cannot_be_steered_to_another_tenant(repo):
    """Naming another agency in the question must not retrieve its records."""
    a = answer(repo, CLARK, "Riverside Public Works culvert replacement")
    for c in a.citations:
        assert repo.get(CLARK, c.record_id) is not None


def test_answer_rechecks_rows_before_citing(repo):
    """The defence-in-depth assertion must actually fire if it is ever reached."""
    from statute import answers

    class Leaky(Repository):
        def search(self, tenant, query):  # noqa: ARG002
            return super().search(RIVER, query)

    leaky = Leaky()
    leaky.seed(SEED)
    with pytest.raises(AccessError):
        answers.answer(leaky, CLARK, "culvert")
