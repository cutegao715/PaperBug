from paperagent.quality.policies import author_write
"""Explicit research reuse with source versions and separate approval state."""
import hashlib
import re
import uuid

from paperagent.workspace.state_store import read_json, write_json, state_lock
from paperagent.workspace.workspace import Workspace


class ResearchLinks:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.dir / "research_links.json"

    @author_write
    def link(self, source_project_id, filename, relation="related_research"):
        if source_project_id in {".", "..", self.ws.project_id} or any(c in source_project_id for c in "/\\:"):
            raise ValueError("Choose a different research space")
        source = Workspace(source_project_id, self.ws.root)
        if not source.dir.is_dir():
            raise ValueError("Source research space does not exist")
        version = source.snapshot_draft(filename)
        text = source.read_draft(filename)
        # Reuse cited bibliography without silently accepting conflicting IDs.
        refs = [source.get_reference(i) for i in set(re.findall(r"\{\{ref:([^}]+)\}\}", text))]
        if any(r is None for r in refs):
            raise ValueError("Source has unresolved reference IDs")
        for ref in refs:
            existing = self.ws.get_reference(ref.ref_id)
            if existing and (existing.title, existing.doi) != (ref.title, ref.doi):
                raise ValueError("Reference ID conflicts with this space: " + ref.ref_id)
        identifier = "reuse_" + uuid.uuid4().hex[:16]
        output = self.ws.dir / "reused_sources" / (identifier + ".md")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        for ref in refs:
            self.ws.save_reference_obj(ref)
        row = {"link_id": identifier, "source_project_id": source_project_id, "filename": filename,
               "source_version": version["version"], "local_path": str(output),
               "local_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "relation": relation,
               "approval_transfer": False, "rule": "Reuse source content only; workflow approvals and delivery status remain independent."}
        with state_lock(self.path):
            rows = read_json(self.path, [])
            rows.append(row)
            write_json(self.path, rows)
        self.ws.upsert_material(str(output), "imported", source_project_id=source_project_id,
                                source_version=version["version"], reuse_link_id=identifier)
        return row

    def view(self):
        rows = read_json(self.path, [])
        for row in rows:
            source = Workspace(row["source_project_id"], self.ws.root)
            try:
                current = source.snapshot_draft(row["filename"])["version"]
            except (ValueError, OSError):
                current = None
            row["needs_review"] = current != row["source_version"]
        return rows
