"""API-level isolation: the HTTP surface must not leak across tenants either."""
from fastapi.testclient import TestClient

from statute.main import app

client = TestClient(app)
CLARK = {"X-API-Key": "key-clark-county"}
RIVER = {"X-API-Key": "key-riverside"}


def test_up_needs_no_key():
    assert client.get("/up").status_code == 200


def test_records_without_a_key_is_401():
    assert client.get("/api/records").status_code == 401


def test_records_with_a_bad_key_is_401():
    assert client.get("/api/records", headers={"X-API-Key": "nope"}).status_code == 401


def test_each_key_sees_only_its_own_records():
    """The two tenants' visible id sets must be disjoint, and neither may see
    the whole corpus."""
    seen = {}
    for headers, tid in ((CLARK, "clark-county"), (RIVER, "riverside")):
        d = client.get("/api/records", headers=headers).json()
        assert d["tenant"] == tid
        assert d["count"] > 0
        seen[tid] = {r["id"] for r in d["records"]}

    assert seen["clark-county"].isdisjoint(seen["riverside"])
    total = client.get("/up").json()["records"]
    assert len(seen["clark-county"] | seen["riverside"]) < total


def test_cross_tenant_record_fetch_is_404_not_403():
    """403 would confirm the id exists. 404 tells the caller nothing."""
    other = client.get("/api/records", headers=RIVER).json()["records"][0]["id"]
    assert client.get(f"/api/records/{other}", headers=RIVER).status_code == 200
    assert client.get(f"/api/records/{other}", headers=CLARK).status_code == 404


def test_search_is_scoped():
    d = client.get("/api/search", params={"q": "culvert"}, headers=CLARK).json()
    assert d["count"] == 0
    assert client.get("/api/search", params={"q": "culvert"}, headers=RIVER).json()["count"] > 0


def test_ask_is_scoped_and_refuses_cleanly():
    d = client.get("/api/ask", params={"q": "culvert replacement"}, headers=CLARK).json()
    assert d["grounded"] is False
    assert d["citations"] == []

    d2 = client.get("/api/ask", params={"q": "culvert replacement"}, headers=RIVER).json()
    assert d2["grounded"] is True
    assert d2["citations"]


def test_every_record_route_requires_a_key():
    for path, params in [
        ("/api/records", {}),
        ("/api/records/cc-2026-0141", {}),
        ("/api/search", {"q": "permit"}),
        ("/api/ask", {"q": "permit"}),
        ("/api/whoami", {}),
    ]:
        assert client.get(path, params=params).status_code == 401, path
