"""Skill Loader for V2.

Skills describe how research work should be done. They are loaded
dynamically from YAML and selected by scope/triggers, instead of dumping the
whole library into every agent context.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


Layer = Literal["general", "domain", "task", "project"]
LAYER_ORDER = {"general": 0, "domain": 1, "task": 2, "project": 3}


class SkillResource(BaseModel):
    id: str
    path: str
    description: str = ""
    when: str = ""


class SkillSpec(BaseModel):
    id: str
    version: str = "1.0"
    name: str = ""
    description: str = ""
    instructions: str = ""
    resources: list[SkillResource] = Field(default_factory=list)
    layer: Layer = "task"
    # Method specificity and library ownership are independent dimensions.
    capability_type: Literal["general", "lifecycle_specific"] | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    triggers: list[str] = Field(default_factory=list)
    principles: list[str] = Field(default_factory=list)
    procedure: list[str] = Field(default_factory=list)
    heuristics: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    section_writing_guide: dict[str, Any] = Field(default_factory=dict)
    document_logic_chain: list[str] = Field(default_factory=list)
    reviewer_risk_points: list[str] = Field(default_factory=list)
    writing_norm_review: dict[str, Any] = Field(default_factory=dict)
    academic_quality_review: dict[str, Any] = Field(default_factory=dict)
    tool_preferences: list[str] = Field(default_factory=list)
    quality_requirements: list[str] = Field(default_factory=list)
    output_contract: dict[str, Any] = Field(default_factory=dict)
    source_path: str = ""
    source_kind: Literal["builtin", "shared", "workspace"] = "builtin"
    source_project_id: str | None = None
    shadowed_sources: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source_kind}:{self.id}@{self.version}"

    def metadata_view(self) -> dict[str, Any]:
        """Discovery never exposes procedures or project facts to the model."""
        return {
            "id": self.id, "version": self.version, "key": self.key,
            "name": self.name or self.id,
            "description": self.description or " / ".join(self.triggers[:4]),
            "layer": self.layer, "capability_type": self.capability_type,
            "scope": self.scope, "triggers": self.triggers,
            "source_kind": self.source_kind, "source_project_id": self.source_project_id,
            "shadowed_sources": self.shadowed_sources,
            "resource_count": len(self.resources),
            "read_with": {"action": "read_context", "kind": "skill", "key": self.key},
        }

    def context_view(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data["resources"] = [{**r.model_dump(), "read_with": {
            "action": "read_context", "kind": "skill_resource", "key": self.key,
            "resource_id": r.id}} for r in self.resources]
        return data


class SkillLoader:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.library_dir = Path(__file__).parent / "library"

    def skill_dirs(self, project_id: str | None = None) -> list[Path]:
        dirs = [self.library_dir, self.root / "skills"]
        if project_id:
            if project_id in {".", ".."} or any(c in project_id for c in "/\\:"):
                raise ValueError("Invalid project ID")
            dirs.append(self.root / "projects" / project_id / "skills")
        return dirs

    def discover(self, project_id: str | None = None) -> list[SkillSpec]:
        skills: list[SkillSpec] = []
        keys: set[str] = set()
        for kind, base in zip(("builtin", "shared", "workspace"), self.skill_dirs(project_id)):
            if not base.exists():
                continue
            if kind == "workspace" and not base.resolve().is_relative_to(base.parent.resolve()):
                continue
            manifests = sorted(base.rglob("SKILL.md"))
            packages = {p.parent for p in manifests}
            yaml_files = sorted(base.rglob("*.yaml")) + sorted(base.rglob("*.yml"))
            yaml_files = [p for p in yaml_files
                          if not any(parent in packages for parent in p.parents)
                          and not set(p.relative_to(base).parts[:-1]) & {"references", "assets", "scripts", "agents"}]
            for path in yaml_files + manifests:
                # A workspace skill must not expose another workspace through a link.
                if not path.resolve().is_relative_to(base.resolve()):
                    continue
                spec = self._load_file(path)
                spec.source_kind = kind
                spec.source_project_id = project_id if kind == "workspace" else None
                spec.shadowed_sources = []
                if spec.key in keys:
                    raise ValueError(f"Duplicate skill key: {spec.key}")
                keys.add(spec.key)
                skills.append(spec)
        return skills

    def load_all(self, project_id: str | None = None) -> list[SkillSpec]:
        # Ownership and specificity are independent of the legacy layer label.
        by_id_version: dict[str, SkillSpec] = {}
        for spec in self.discover(project_id):
            key = f"{spec.id}@{spec.version}"
            previous = by_id_version.get(key)
            if previous:
                spec.shadowed_sources = [*previous.shadowed_sources, previous.key]
            by_id_version[key] = spec
        skills = list(by_id_version.values())
        skills.sort(key=lambda s: (LAYER_ORDER[s.layer], s.id, s.version))
        return skills

    def resolve(self, key: str, project_id: str | None = None) -> SkillSpec:
        skills = self.discover(project_id) if ":" in key else self.load_all(project_id)
        matches = [s for s in skills if key in {s.key, s.id, f"{s.id}@{s.version}"}]
        if not matches:
            mapped = self.canonical_key(key)
            matches = [s for s in skills if mapped in {s.key, s.id, f"{s.id}@{s.version}"}]
        if not matches:
            raise ValueError(f"Skill not found: {key}")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous skill version; use an exact key: {', '.join(s.key for s in matches)}")
        return matches[0]

    def alias_metadata(self):
        path = Path(__file__).parent / "legacy_aliases.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"aliases": {}, "resources": {}}

    def canonical_key(self, key):
        source, value = key.split(":", 1) if ":" in key else ("", key)
        identifier, sep, version = value.partition("@")
        if source not in {"", "builtin"}:
            return key  # Never redirect a private/shared precise key into builtin.
        mapped = self.alias_metadata()["aliases"].get(identifier, identifier)
        return (source + ":" if source else "") + mapped + (sep + version if sep else "")

    def read_resource(self, key: str, resource_id: str, project_id: str | None = None,
                      *, offset: int = 0, max_chars: int = 6000, expected_sha256: str = "") -> dict:
        if offset < 0 or not 500 <= max_chars <= 20000:
            raise ValueError("Invalid resource page bounds")
        skill = self.resolve(key, project_id)
        old_id = key.split(":")[-1].split("@")[0]
        if old_id != skill.id:
            resource_id = self.alias_metadata()["resources"].get(old_id, {}).get(resource_id, resource_id)
        resource = next((r for r in skill.resources if r.id == resource_id), None)
        if resource is None:
            raise ValueError("Resource is not declared by this skill")
        base = Path(skill.source_path).parent.resolve()
        relative = Path(resource.path)
        if relative.is_absolute() or ".." in relative.parts or ":" in resource.path or "\\" in resource.path:
            raise ValueError("Resource path must stay inside its skill package")
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
            raise ValueError("Only declared text resources inside the skill package may be read")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 and expected_sha256 != digest:
            raise ValueError("Resource changed during reading; restart from offset 0")
        content = raw.decode("utf-8-sig")
        end = offset + max_chars
        return {"status": "ok", "skill_key": skill.key, "resource_id": resource_id,
                "source": str(path), "sha256": digest, "content": content[offset:end],
                "offset": offset, "next_offset": end if end < len(content) else None,
                "total_chars": len(content), "content_role": "skill_guidance"}

    def select(self, *, goal: str = "", project_id: str | None = None,
               task_type: str | None = None,
               project_type: str | None = None,
               discipline_ids: list[str] | None = None,
               lifecycle_type: str | None = None,
               recommendations: list[dict] | None = None,
               limit: int = 4) -> list[SkillSpec]:
        raw_text = " ".join([
            goal,
            task_type or "",
            project_type or "",
            " ".join(discipline_ids or []),
        ]).lower()
        disciplines = [d.lower() for d in (discipline_ids or [])]
        tokens = _tokens(raw_text)
        scored = []
        for skill in self.load_all(project_id):
            allowed_lifecycles = skill.scope.get("lifecycle_types") or []
            if lifecycle_type and allowed_lifecycles and lifecycle_type not in allowed_lifecycles:
                continue
            discipline = str(skill.scope.get("discipline") or "").lower()
            if discipline and discipline not in disciplines and not any(t.lower() in raw_text for t in skill.triggers):
                continue
            score = _score(skill, tokens, raw_text, task_type, project_type, disciplines)
            if score > 0 and lifecycle_type and lifecycle_type in allowed_lifecycles:
                score += 2
            if any(r.get("skill_id") == skill.id and r.get("version") == skill.version
                   for r in recommendations or []):
                score += 3 if any(r.get("skill_id") == skill.id and r.get("role") == "core" for r in recommendations or []) else 1
            if score > 0 or skill.layer == "general":
                scored.append((score, LAYER_ORDER[skill.layer], skill))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].id))
        if recommendations and limit <= 6:
            # A shortlist is bounded advice, not an implicit call of every core
            # skill in the routing table. Full catalog access remains available.
            roles = {r.get("skill_id"): r.get("role") for r in recommendations}
            selected, counts = [], {"core": 0, "support": 0}
            for _, _, skill in scored:
                role = roles.get(skill.id)
                if role in counts:
                    if counts[role] >= (3 if role == "core" else 2):
                        continue
                    counts[role] += 1
                selected.append(skill)
                if len(selected) >= limit:
                    break
            return selected
        return [skill for _, _, skill in scored[:limit]]

    def select_metadata(self, **kwargs) -> list[dict]:
        recommendations = kwargs.get("recommendations") or []
        return [{**s.metadata_view(), "workflow_recommendations": [
            r for r in recommendations
            if r.get("skill_id") == s.id and r.get("version") == s.version
        ]} for s in self.select(**kwargs)]

    def _load_file(self, path: Path) -> SkillSpec:
        text = path.read_text(encoding="utf-8-sig")
        if path.name == "SKILL.md":
            lines = text.splitlines()
            if not lines or lines[0].strip() != "---":
                raise ValueError(f"Skill manifest needs YAML front matter: {path}")
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
            if end is None:
                raise ValueError(f"Unclosed skill front matter: {path}")
            data = yaml.safe_load("\n".join(lines[1:end])) or {}
            if not isinstance(data, dict) or not data.get("name"):
                raise ValueError(f"Skill manifest requires a name: {path}")
            data.setdefault("id", data["name"])
            data["instructions"] = "\n".join(lines[end + 1:]).strip()
            if "resources" not in data:
                data["resources"] = [
                    {"id": p.relative_to(path.parent).as_posix(),
                     "path": p.relative_to(path.parent).as_posix(), "description": p.stem.replace("-", " ")}
                    for p in sorted((path.parent / "references").rglob("*.md"))]
        else:
            data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError(f"skill file must contain a mapping: {path}")
        data.setdefault("id", path.stem)
        data["source_path"] = str(path)
        spec = SkillSpec.model_validate(data)
        if len({r.id for r in spec.resources}) != len(spec.resources):
            raise ValueError(f"Duplicate resource IDs in {path}")
        return spec


def _tokens(text: str) -> set[str]:
    return {p.lower() for p in text.replace("-", " ").replace("_", " ").split() if len(p) >= 2}


def _score(skill: SkillSpec, tokens: set[str], raw_text: str, task_type: str | None,
           project_type: str | None, discipline_ids: list[str]) -> int:
    haystack = " ".join([
        skill.id,
        skill.name,
        " ".join(skill.triggers),
        " ".join(str(v) for v in skill.scope.values()),
    ]).lower()
    score = sum(1 for token in tokens if token in haystack)
    if skill.id in raw_text or (skill.name and skill.name.lower() in raw_text):
        score += 8
    score += sum(2 for trigger in skill.triggers
                 if trigger and trigger.lower() in raw_text)
    if task_type and skill.scope.get("task_type") == task_type:
        score += 3
    if project_type and skill.scope.get("project_type") == project_type:
        score += 3
    discipline = str(skill.scope.get("discipline") or "").lower()
    if discipline and discipline in discipline_ids:
        score += 4
    return score
