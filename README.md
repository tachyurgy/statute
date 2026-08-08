# Statute

Multi-tenant public records with grounded answers, where the tenant scope is a capability
rather than a parameter someone has to remember.

Live: **https://statute.levelbrook.com**

Software that serves several agencies from one deployment has one failure mode that
outweighs all the others: agency A reading agency B's records. Everything else is a bug.
That one is a notification letter and a news story.

The usual defence is a convention — "always add `WHERE tenant_id = ?`" — which holds right
up until someone adds a query and forgets. Conventions do not survive contact with a team,
a deadline, or a new hire. So this makes the scope structurally impossible to omit.

## How the scope works

**A `Tenant` is a capability, not a label.** It is not a string you pass alongside your
query; it is an object you must already hold in order to have a query method at all. It is
constructed only by `authenticate()`, so a caller cannot mint a scope for an agency whose
credential it does not have. A malformed id fails closed at construction.

**There is one choke point.** Every read goes through a private `_select` that appends the
tenant predicate itself, *after* the caller's filters. Caller-supplied filters are ANDed
into a parenthesised group, so no filter value can terminate the WHERE clause early.

**The connection is name-mangled and private**, so the repository cannot be casually used
as a way to run arbitrary SQL.

**The HTTP layer inherits it.** Every record-reading route depends on `current_tenant`.
There is no way to obtain the `Tenant` the repository demands except through that
dependency, so a route that reads records without a scope cannot be written by accident.

**Answers are scoped too, then re-checked.** The assistant never touches the database; it
is handed rows that were already scoped and asserts their tenant again before citing them.
That assertion should be unreachable, which is exactly why it is worth having — and it has
a test proving it fires.

## Cross-tenant fetch returns 404, not 403

Asking for another agency's record by id gets `404 no such record`. A `403` would confirm
the id exists to a caller who should not know that. The response is deliberately
indistinguishable from a record that was never there.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
.venv/bin/python -m pytest tests -q     # 24 tests
```

The suite attacks the property rather than describing it: it takes a real id from one agency
and asks another for it, probes the filter arguments with `' OR '1'='1`, `%` and `_`, tries
to steer the assistant by naming another agency in the question, asserts the two tenants'
visible id sets are disjoint and that neither can see the whole corpus, and checks that the
public surface exposes no unscoped read.

The invariant was verified red before being kept. Dropping the tenant predicate from
`_select` — the exact "forgot the WHERE" regression this is built to prevent — turns six
tests failing, and trips the answer layer's defence-in-depth assertion on the way.

## API

```
GET /api/records[?kind=&status=]   this agency's records
GET /api/records/{id}              404 for anyone else's
GET /api/search?q=
GET /api/ask?q=                    grounded answer with citations, or a clean refusal
GET /api/whoami
GET /up                            the only route that needs no key
```

All record routes require `X-API-Key`. Demo keys are in `repository.py` — the point of the
live page is switching between them and re-running the same question.

## Layout

```
statute/repository.py   Tenant, authentication, the scoped choke point
statute/answers.py      retrieval and grounded composition
statute/main.py         async FastAPI surface
statute/seed.py         fixture records for three agencies
```

## Limits

The store is SQLite with an in-process fixture and the "authentication" is a static API-key
map, so there are no sessions, no roles within an agency, and no audit log — real agency
software needs all three, and per-user roles are where a second, finer-grained layer of this
same problem lives. Retrieval is keyword matching, not embeddings, and the composer is
extractive: it states only what it reads off a record. The isolation work is what this
demonstrates, and it is the part that would survive replacing either of those.

MIT.
