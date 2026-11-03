"""Versioned HTTP routes retain legacy behavior and streaming contracts."""
from fastapi.testclient import TestClient
from paperagent import server
from paperagent_cloud import app as cloud


def test_local_versioned_and_legacy_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    with TestClient(server.app) as client:
        for path in ["/projects", "/skills", "/lifecycles", "/tools", "/config", "/today"]:
            old, new = client.get("/api" + path), client.get("/api/v1" + path)
            assert new.status_code == old.status_code == 200
            assert new.json() == old.json()
            assert "Deprecation" in old.headers
            assert "Deprecation" not in new.headers
        created = client.post("/api/v1/projects", json={"name":"version-test"})
        assert created.status_code == 200
        pid = created.json()["project_id"]
        assert client.get(f"/internal/projects/{pid}/context").status_code == 200
        assert client.get(f"/api/projects/{pid}/context").status_code == 200
        assert client.get(f"/api/v1/projects/{pid}/context").status_code == 404
        missing = client.get("/api/v1/runs/missing/events")
        assert missing.status_code == 404
    paths = server.app.openapi()["paths"]
    for path in paths:
        if path.startswith("/api/") and not path.startswith("/api/v1/") and not path.endswith("/context"):
            assert "/api/v1/" + path[len("/api/"):] in paths
    assert not any("/api/v1/api/" in p for p in paths)


def test_cloud_versions_preserve_auth_status_and_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CLOUD_DB", str(tmp_path / "cloud.sqlite"))
    with TestClient(cloud.app) as client:
        for prefix in ("/api", "/api/v1"):
            assert client.get(prefix + "/spaces").status_code == 401
        result = client.post("/api/v1/auth/register", json={"username":"version-user","password":"test-password-123"})
        assert result.status_code == 201
        headers = {"Authorization": "Bearer " + result.json()["token"]}
        result = client.post("/api/v1/spaces", json={"name":"test"}, headers=headers)
        assert result.status_code == 201
        old = client.get("/api/spaces", headers=headers)
        new = client.get("/api/v1/spaces", headers=headers)
        assert old.json() == new.json()
        assert "Deprecation" in old.headers and "Deprecation" not in new.headers
        assert client.get("/api/spaces").headers.get("Deprecation")


def test_sse_legacy_and_v1_preserve_events(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    run = server.RunState("test", "test", "offline")
    run.emit({"type": "test", "value": 1})
    run.done.set()
    server.RUNS[run.run_id] = run
    try:
        with TestClient(server.app) as client:
            old = client.get(f"/api/runs/{run.run_id}/events")
            new = client.get(f"/api/v1/runs/{run.run_id}/events")
            assert old.status_code == new.status_code == 200
            assert old.text == new.text
            assert "data: [DONE]" in new.text
            assert new.headers["content-type"].startswith("text/event-stream")
            assert "Deprecation" in old.headers and "Deprecation" not in new.headers
    finally:
        server.RUNS.pop(run.run_id, None)
