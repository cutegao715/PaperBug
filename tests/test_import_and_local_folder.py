from fastapi.testclient import TestClient
import zipfile
from pathlib import Path

from paperagent import server
from paperagent.connectors.local_folder import LocalFolderConnector
from paperagent.domain.store import DomainStore
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tools.registry import REGISTRY, describe_tools
from paperagent.workspace.workspace import Workspace


def _write_minimal_docx(path, text: str):
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


def test_import_and_reference_tools_registered():
    tool_ids = {tool["tool_id"] for tool in describe_tools()}

    assert {"file_import", "reference_resolve"} <= tool_ids
    assert REGISTRY["file_import"].permission == "local_read"
    assert REGISTRY["reference_resolve"].permission == "network_read"


def test_file_import_markdown_and_bibtex(tmp_path):
    ws = Workspace("import", root=tmp_path).init("import sources")
    md = tmp_path / "note.md"
    md.write_text("# Note\nEvidence text.", encoding="utf-8")
    bib = tmp_path / "refs.bib"
    bib.write_text(
        """
@article{smith2024,
  title={AI Research Workflows},
  author={Smith, Jane and Chen, Li},
  year={2024},
  journal={Research Tools Quarterly},
  doi={10.1234/example}
}
""",
        encoding="utf-8",
    )
    ex = ToolExecutor(ResearchPolicy(), ws)

    md_result = ex.execute(ToolCall(tool_id="file_import",
                                    input={"path": str(md)}))
    bib_result = ex.execute(ToolCall(tool_id="file_import",
                                     input={"path": str(bib)}))

    assert md_result.status == "ok"
    assert md_result.data["kind"] == "text"
    assert "Evidence text" in md_result.data["text"]
    assert bib_result.status == "ok"
    assert bib_result.data["kind"] == "bibtex"
    assert bib_result.data["references"][0]["title"] == "AI Research Workflows"
    assert bib_result.data["references"][0]["doi"] == "10.1234/example"


def test_file_import_legacy_doc_converts_when_converter_available(tmp_path, monkeypatch):
    from paperagent.tools import importers

    legacy = tmp_path / "legacy.doc"
    legacy.write_bytes(b"legacy placeholder")
    converted_dir = tmp_path / "converted"

    def fake_convert(path, output_dir, timeout=20.0):
        target = output_dir / f"{path.stem}.docx"
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_minimal_docx(target, "Converted legacy document text.")
        return {
            "status": "converted",
            "converter": "fake",
            "original_path": str(path),
            "converted_path": str(target),
        }

    monkeypatch.setattr(importers, "_convert_doc_to_docx", fake_convert)
    result = importers.run_file_import(
        {"path": str(legacy), "conversion_dir": str(converted_dir)},
        resolve=lambda p: Path(p),
    )

    assert result["kind"] == "docx"
    assert result["original_kind"] == "doc"
    assert "Converted legacy document text." in result["text"]
    assert result["converted_path"].endswith(".docx")


def test_file_import_wps_converts_when_converter_available(tmp_path, monkeypatch):
    from paperagent.tools import importers

    wps = tmp_path / "proposal.wps"
    wps.write_bytes(b"wps placeholder")
    converted_dir = tmp_path / "converted"

    def fake_convert(path, output_dir, timeout=20.0):
        target = output_dir / f"{path.stem}.docx"
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_minimal_docx(target, "Converted WPS document text.")
        return {
            "status": "converted",
            "converter": "fake wps",
            "original_path": str(path),
            "converted_path": str(target),
        }

    monkeypatch.setattr(importers, "_convert_wps_to_docx", fake_convert)
    result = importers.run_file_import(
        {"path": str(wps), "conversion_dir": str(converted_dir)},
        resolve=lambda p: Path(p),
    )

    assert result["kind"] == "docx"
    assert result["original_kind"] == "wps"
    assert "Converted WPS document text." in result["text"]


def test_reference_resolve_offline_stub(tmp_path):
    ws = Workspace("resolve", root=tmp_path).init("resolve reference")
    ex = ToolExecutor(ResearchPolicy(), ws)

    result = ex.execute(ToolCall(
        tool_id="reference_resolve",
        input={"title": "AI Research Workflows", "provider": "offline_stub"},
    ))

    assert result.status == "ok"
    assert result.data["candidates"][0]["title"] == "AI Research Workflows"
    assert result.data["candidates"][0]["source"] == "offline_stub"


def test_local_folder_connector_records_files_and_deduplicates(tmp_path):
    store = DomainStore(tmp_path)
    folder = tmp_path / "sources"
    folder.mkdir()
    (folder / "note.md").write_text("hello", encoding="utf-8")
    (folder / "ignore.tmp").write_text("skip", encoding="utf-8")
    watcher = store.add_watcher(
        name="local sources",
        watcher_type="external",
        source="local_folder",
    )

    connector = LocalFolderConnector(store)
    first = connector.scan(folder, watcher_id=watcher.watcher_id, project_id="demo")
    second = connector.scan(folder, watcher_id=watcher.watcher_id, project_id="demo")

    assert len(first["imported"]) == 1
    assert first["imported"][0].project_id == "demo"
    assert first["events"][0]["event"].source == "connector:local_folder"
    assert first["events"][0]["trigger"].action == "local_file_imported"
    assert second["imported"] == []
    assert len(store.list_imported_files(project_id="demo")) == 1


def test_local_folder_scan_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    server.RUNS.clear()
    folder = tmp_path / "sources"
    folder.mkdir()
    (folder / "refs.ris").write_text(
        "TY  - JOUR\nTI  - Research Agent Systems\nAU  - Doe, Jane\nPY  - 2025\nER  -",
        encoding="utf-8",
    )

    with TestClient(server.app) as client:
        watcher = client.post("/api/v1/watchers", json={
            "name": "local sources",
            "watcher_type": "external",
            "source": "local_folder",
        }).json()
        response = client.post("/api/v1/connectors/local-folder/scan", json={
            "folder": str(folder),
            "watcher_id": watcher["watcher_id"],
            "project_id": "demo",
        })

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["imported"]) == 1
    assert payload["events"][0]["trigger"]["action"] == "local_file_imported"
