from fastapi.testclient import TestClient

from paperagent import server
from paperagent.connectors.zotero import ZoteroConnector
from paperagent.domain.store import DomainStore


def _zotero_item(attachment_path=None):
    item = {
        "key": "ABCD1234",
        "data": {
            "itemType": "journalArticle",
            "title": "Research Agents for Academic Workflows",
            "creators": [
                {"firstName": "Jane", "lastName": "Smith"},
                {"firstName": "Li", "lastName": "Chen"},
            ],
            "date": "2025-05-01",
            "publicationTitle": "Journal of Research Tools",
            "DOI": "10.1234/agents",
            "url": "https://example.org/agents",
            "collections": ["col-a"],
            "tags": [{"tag": "paperagent"}, {"tag": "workflow"}],
            "notes": [{"note": "Useful framework paper."}],
            "attachments": [],
        },
    }
    if attachment_path:
        item["data"]["attachments"].append({
            "title": "PDF",
            "path": str(attachment_path),
        })
    return item


def test_zotero_connector_syncs_items_references_and_attachments(tmp_path):
    store = DomainStore(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nstub")
    watcher = store.add_watcher(
        name="zotero library",
        watcher_type="literature",
        source="zotero",
    )

    connector = ZoteroConnector(store)
    result = connector.sync_items(
        [_zotero_item(pdf)],
        watcher_id=watcher.watcher_id,
        project_id="demo",
    )
    again = connector.sync_items([_zotero_item(pdf)], project_id="demo")

    assert len(result["synced"]) == 1
    assert result["synced"][0].title == "Research Agents for Academic Workflows"
    assert result["references"][0].doi == "10.1234/agents"
    assert result["imported_files"][0].source == "zotero"
    assert result["events"][0]["event"].source == "connector:zotero"
    assert result["events"][0]["trigger"].action == "zotero_item_synced"
    assert len(again["synced"]) == 1
    assert len(store.list_zotero_items(project_id="demo")) == 1
    assert len(store.list_imported_files(project_id="demo")) == 1


def test_zotero_connector_updates_existing_item(tmp_path):
    store = DomainStore(tmp_path)
    connector = ZoteroConnector(store)
    connector.sync_items([_zotero_item()], project_id="demo")
    changed = _zotero_item()
    changed["data"]["title"] = "Updated Research Agent Workflows"

    connector.sync_items([changed], project_id="demo")

    items = store.list_zotero_items(project_id="demo")
    assert len(items) == 1
    assert items[0].title == "Updated Research Agent Workflows"


def test_zotero_sync_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERAGENT_SCHEDULER", "off")
    server.RUNS.clear()

    with TestClient(server.app) as client:
        watcher = client.post("/api/v1/watchers", json={
            "name": "zotero library",
            "watcher_type": "literature",
            "source": "zotero",
        }).json()
        response = client.post("/api/v1/connectors/zotero/sync", json={
            "items": [_zotero_item()],
            "watcher_id": watcher["watcher_id"],
            "project_id": "demo",
        })
        listed = client.get("/api/v1/connectors/zotero/items",
                            params={"project_id": "demo"})

    assert response.status_code == 200
    assert response.json()["references"][0]["source"] == "zotero"
    assert response.json()["events"][0]["trigger"]["action"] == "zotero_item_synced"
    assert listed.json()["items"][0]["zotero_key"] == "ABCD1234"
