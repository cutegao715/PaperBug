"""Control Service (FastAPI) end-to-end over HTTP, offline mode."""
import json

from fastapi.testclient import TestClient

from paperagent.server import app


def _drain_sse(client, run_id, timeout_s=30):
    """Consume the SSE stream until [DONE]; return parsed events."""
    events = []
    with client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    break
                events.append(json.loads(payload))
    return events


def test_full_offline_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    # isolate from the developer's real .env (server auto-loads it at import)
    for var in ("PAPERAGENT_LLM_BASE_URL", "PAPERAGENT_LLM_API_KEY",
                "PAPERAGENT_LLM_MODEL"):
        monkeypatch.setenv(var, "")
    # reset any in-memory runs from other tests
    from paperagent import server
    server.RUNS.clear()

    with TestClient(app) as client:
        # config
        cfg = client.get("/api/v1/config").json()
        assert cfg["llm_configured"] is False

        # create project
        r = client.post("/api/v1/projects",
                        json={"name": "mba论文", "goal": "数字化转型研究"})
        assert r.status_code == 200
        pid = r.json()["project_id"]

        # send a message (offline mode), stream events
        r = client.post(f"/api/v1/projects/{pid}/messages",
                        json={"text": "写一份文献综述", "mode": "offline"})
        assert r.status_code == 200
        run_id = r.json()["run_id"]

        events = _drain_sse(client, run_id)
        kinds = [e["type"] for e in events]
        assert "run_started" in kinds
        assert kinds.count("step") >= 5  # search + saves + draft + finish
        final = [e for e in events if e["type"] == "run_finished"][0]
        assert final["status"] == "finished"
        assert final["draft"] == "draft.md"
        assert final["quality_report"]["citation_integrity"]["errors"] == []

        # project detail reflects the run
        detail = client.get(f"/api/v1/projects/{pid}").json()
        assert len(detail["references"]) == 3
        assert detail["drafts"] == ["draft.md"]
        assert len(detail["runs"]) == 1
        assert detail["runs"][0]["status"] == "finished"
        assert detail["runtime"]["status"] == "finished"
        assert detail["permissions"]["write_roots"]
        assert detail["tool_calls"]

        # draft rendering endpoint
        d = client.get(f"/api/v1/projects/{pid}/drafts/draft.md").json()
        assert "{{ref:" in d["raw"]
        assert "[1]" in d["rendered"]
        assert "## References" in d["rendered"]


def test_draft_export_docx_and_wps(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent import server
    from paperagent.workspace.workspace import Workspace
    server.RUNS.clear()

    with TestClient(app) as client:
        pid = client.post("/api/v1/projects",
                          json={"name": "export-test", "goal": "导出测试"}).json()["project_id"]
        ws = Workspace(pid, root=tmp_path).init("导出测试")
        ws.write_draft("draft.md", "# 标题\n\n正文内容。")

        docx_resp = client.get(f"/api/v1/projects/{pid}/drafts/draft.md/export?format=docx")
        assert docx_resp.status_code == 200
        assert docx_resp.content[:2] == b"PK"

        def fake_wps(ws_arg, name):
            path = ws_arg.dir / "exports" / "draft.wps"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"wps-output")
            return path

        monkeypatch.setattr(server.control, "export_draft_wps", fake_wps)
        wps_resp = client.get(f"/api/v1/projects/{pid}/drafts/draft.md/export?format=wps")
        assert wps_resp.status_code == 200
        assert wps_resp.content == b"wps-output"


def test_template_docx_extract_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from docx import Document
    from paperagent import server
    from paperagent.workspace.workspace import Workspace
    server.RUNS.clear()

    template = tmp_path / "template.docx"
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "选题依据"
    table.cell(0, 1).text = ""
    table.cell(1, 0).text = "研究目标"
    table.cell(1, 1).text = ""
    doc.save(template)

    with TestClient(app) as client:
        pid = client.post("/api/v1/projects",
                          json={"name": "template-export", "goal": "模板导出"}).json()["project_id"]
        ws = Workspace(pid, root=tmp_path).init("模板导出")
        ws.authorize_root(template)
        ws.write_draft(
            "draft.md",
            "# 申请书\n\n## 选题依据\n\n这里是选题依据正文。\n\n## 研究目标\n\n这里是研究目标正文。",
        )

        extracted = client.get(
            f"/api/v1/projects/{pid}/templates/extract",
            params={"path": str(template)},
        )
        assert extracted.status_code == 200
        assert any(f["label"] == "选题依据" for f in extracted.json()["fields"])

        resp = client.get(
            f"/api/v1/projects/{pid}/drafts/draft.md/export",
            params={"format": "template_docx", "template_path": str(template)},
        )
        assert resp.status_code == 200
        assert resp.content[:2] == b"PK"

        exported = ws.dir / "exports" / "draft-template.docx"
        filled = Document(str(exported))
        assert "这里是选题依据正文" in filled.tables[0].cell(0, 1).text
        assert "这里是研究目标正文" in filled.tables[0].cell(1, 1).text


def test_notification_dismiss_hides_from_list(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent import server
    server.RUNS.clear()

    with TestClient(app) as client:
        created = client.post("/api/v1/notifications",
                              json={"title": "不感兴趣测试", "body": "body"}).json()
        nid = created["notification_id"]
        assert any(n["notification_id"] == nid
                   for n in client.get("/api/v1/notifications").json()["notifications"])
        assert client.post(f"/api/v1/notifications/{nid}/dismiss").status_code == 200
        assert all(n["notification_id"] != nid
                   for n in client.get("/api/v1/notifications").json()["notifications"])


def test_project_messages_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    for var in ("PAPERAGENT_LLM_BASE_URL", "PAPERAGENT_LLM_API_KEY",
                "PAPERAGENT_LLM_MODEL"):
        monkeypatch.setenv(var, "")
    from paperagent import server
    server.RUNS.clear()

    with TestClient(app) as client:
        pid = client.post("/api/v1/projects",
                          json={"name": "chat-history", "goal": "项目对话"}).json()["project_id"]
        run_id = client.post(f"/api/v1/projects/{pid}/messages",
                             json={"text": "你好", "mode": "offline"}).json()["run_id"]
        events = _drain_sse(client, run_id)
        final = [e for e in events if e["type"] == "run_finished"][0]
        assert final["status"] == "finished"

        detail = client.get(f"/api/v1/projects/{pid}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
        assert detail["messages"][0]["content"] == "你好"


def test_folder_import_reports_unsupported_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path / "workspace"))
    from paperagent import server
    server.RUNS.clear()

    folder = tmp_path / "materials"
    folder.mkdir()
    (folder / "说明.xyz").write_bytes(b"unknown")
    (folder / "承诺书.abc").write_bytes(b"abc")

    with TestClient(app) as client:
        pid = client.post("/api/v1/projects",
                          json={"name": "education", "goal": "教育课题"}).json()["project_id"]
        r = client.post(f"/api/v1/projects/{pid}/imports/folder",
                        json={"folder": str(folder), "recursive": True})
        assert r.status_code == 200
        payload = r.json()
        assert payload["imported"] == []
        assert payload["errors"] == []
        assert payload["unsupported_count"] == 2
        assert {x["suffix"] for x in payload["unsupported"]} == {".xyz", ".abc"}
        assert ".docx" in payload["supported_suffixes"]
        assert ".wps" in payload["supported_suffixes"]
        detail = client.get(f"/api/v1/projects/{pid}").json()
        statuses = {m["suffix"]: m["status"] for m in detail["materials"]}
        assert statuses[".abc"] == "unsupported"
        assert statuses[".xyz"] == "unsupported"
        assert str(folder.resolve()) in detail["permissions"]["allowed_roots"]


def test_tools_endpoint_exposes_runtime_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent import server
    server.RUNS.clear()

    with TestClient(app) as client:
        payload = client.get("/api/v1/tools").json()
        file_import = next(t for t in payload["tools"] if t["tool_id"] == "file_import")
        assert file_import["input_schema"]["required"] == ["path"]
        assert "retry_policy" in file_import
        assert "timeout_s" in file_import


def test_cancel_and_conflict(tmp_path, monkeypatch):
    """HTTP plumbing only: conflict while running, cancel, SSE final event.
    (Real loop-level cancellation is covered in test_loop_and_executor.)
    """
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent import server
    server.RUNS.clear()

    original = server._run_agent

    def stub_run(run):
        run.emit({"type": "run_started", "run_id": run.run_id,
                  "goal": run.goal, "mode": run.mode})
        run.cancel_event.wait(15)
        run.emit({"type": "run_finished", "status": "cancelled",
                  "summary": "", "draft": None, "iterations": 0,
                  "quality_report": None})
        run.status = "cancelled"
        run.done.set()

    server.control._run_agent = stub_run
    try:
        with TestClient(app) as client:
            pid = client.post("/api/v1/projects",
                              json={"name": "cancel-test"}).json()["project_id"]
            run_id = client.post(
                f"/api/v1/projects/{pid}/messages",
                json={"text": "慢任务", "mode": "offline"}).json()["run_id"]

            # a second message on the same project conflicts while running
            r2 = client.post(f"/api/v1/projects/{pid}/messages",
                             json={"text": "第二条", "mode": "offline"})
            assert r2.status_code == 409

            # cancel mid-run, then observe the final SSE event
            assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
            events = _drain_sse(client, run_id)
            final = [e for e in events if e["type"] == "run_finished"][0]
            assert final["status"] == "cancelled"
    finally:
        server.control._run_agent = original

    # unknown run -> 404
    with TestClient(app) as client:
        assert client.post("/api/v1/runs/nope/cancel").status_code == 404
