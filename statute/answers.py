"""Grounded answers over an agency's own records.

The retrieval is scoped by the same `Tenant` the repository requires, which is
the point: an assistant that answers questions about public records is a very
efficient way to leak them if the retrieval step forgets whose records it is
reading. The answer layer therefore never touches the database itself -- it is
handed rows that were already scoped, and it re-checks them anyway before citing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .repository import AccessError, Repository, Tenant

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Citation:
    record_id: str
    title: str
    excerpt: str


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    grounded: bool
    refused_reason: str | None = None


def _score(query: str, row) -> int:
    terms = set(_WORD.findall(query.lower()))
    if not terms:
        return 0
    haystack = f"{row['title']} {row['body']}".lower()
    return sum(1 for t in terms if t in haystack)


def _excerpt(body: str, query: str, width: int = 180) -> str:
    terms = _WORD.findall(query.lower())
    low = body.lower()
    for t in sorted(terms, key=len, reverse=True):
        i = low.find(t)
        if i >= 0:
            start = max(0, i - width // 3)
            return ("…" if start else "") + body[start : start + width].strip() + "…"
    return body[:width].strip() + "…"


def answer(repo: Repository, tenant: Tenant, question: str, k: int = 3) -> Answer:
    """Retrieve within the tenant, then compose only from what was retrieved."""
    rows = repo.search(tenant, question)
    if not rows:
        # Fall back to the tenant's whole record set, still scoped, and rank.
        rows = repo.list_records(tenant)

    ranked = sorted(rows, key=lambda r: (-_score(question, r), r["id"]))
    hits = [r for r in ranked if _score(question, r) > 0][:k]

    if not hits:
        return Answer(
            text="",
            citations=[],
            grounded=False,
            refused_reason="no record in this agency's holdings matches the question",
        )

    # Defence in depth. These rows came from a scoped read, so this should never
    # fire -- which is exactly why it is worth asserting rather than assuming.
    for row in hits:
        if row["tenant_id"] != tenant.id:
            raise AccessError(
                f"record {row['id']} belongs to {row['tenant_id']}, not {tenant.id}"
            )

    citations = [
        Citation(record_id=r["id"], title=r["title"], excerpt=_excerpt(r["body"], question))
        for r in hits
    ]
    text = " ".join(
        f"{r['title']} ({r['kind']}, filed {r['filed_on']}) is currently {r['status']} "
        f"[{r['id']}]."
        for r in hits
    )
    return Answer(text=text, citations=citations, grounded=True)
