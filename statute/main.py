"""Async FastAPI surface.

Every record-reading route depends on `current_tenant`, so a route cannot be
written that reads records without a scope: there is no way to obtain the
`Tenant` the repository demands except through the dependency.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .answers import answer as compose_answer
from .repository import AccessError, Repository, Tenant, authenticate
from .seed import SEED

app = FastAPI(title="Statute", docs_url="/api/docs")
WEB = Path(__file__).resolve().parent.parent / "web"

repo = Repository()
repo.seed(SEED)


async def current_tenant(x_api_key: str | None = Header(default=None)) -> Tenant:
    try:
        return authenticate(x_api_key)
    except AccessError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _row(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "body": r["body"],
        "status": r["status"],
        "filed_on": r["filed_on"],
    }


@app.get("/up")
async def up() -> dict:
    return {"status": "ok", "records": repo.count_all_tenants()}


@app.get("/api/whoami")
async def whoami(tenant: Tenant = Depends(current_tenant)) -> dict:
    return {"tenant": tenant.id, "name": tenant.name}


@app.get("/api/records")
async def records(
    kind: str | None = None,
    status: str | None = None,
    tenant: Tenant = Depends(current_tenant),
) -> dict:
    rows = repo.list_records(tenant, kind=kind, status=status)
    return {"tenant": tenant.id, "count": len(rows), "records": [_row(r) for r in rows]}


@app.get("/api/records/{record_id}")
async def record(record_id: str, tenant: Tenant = Depends(current_tenant)) -> dict:
    row = repo.get(tenant, record_id)
    if row is None:
        # Deliberately indistinguishable from "does not exist". Saying "exists
        # but not yours" confirms the id to a caller who should not have it.
        raise HTTPException(status_code=404, detail="no such record")
    return _row(row)


@app.get("/api/search")
async def search(q: str, tenant: Tenant = Depends(current_tenant)) -> dict:
    rows = repo.search(tenant, q)
    return {"tenant": tenant.id, "count": len(rows), "records": [_row(r) for r in rows]}


@app.get("/api/ask")
async def ask(q: str, tenant: Tenant = Depends(current_tenant)) -> dict:
    a = compose_answer(repo, tenant, q)
    return {
        "tenant": tenant.id,
        "grounded": a.grounded,
        "answer": a.text,
        "refused_reason": a.refused_reason,
        "citations": [
            {"record_id": c.record_id, "title": c.title, "excerpt": c.excerpt}
            for c in a.citations
        ],
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (WEB / "index.html").read_text()
