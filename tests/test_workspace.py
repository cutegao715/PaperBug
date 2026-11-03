"""Workspace + render round-trip tests."""
from paperagent.schemas.references import Reference, ReferenceCandidate
from paperagent.workspace.render import render_draft
from paperagent.workspace.workspace import Workspace


def _ws(tmp_path, project="t"):
    return Workspace(project, root=tmp_path).init("test goal")


def test_reference_roundtrip(tmp_path):
    ws = _ws(tmp_path)
    cand = ReferenceCandidate(ref_id="yang2020deep", title="Deep Research",
                              authors=["Yang, L"], year=2020, venue="Nature",
                              source="semantic_scholar")
    ws.save_reference(cand, source_query="deep research")
    refs = ws.list_references()
    assert len(refs) == 1
    assert refs[0].ref_id == "yang2020deep"
    assert refs[0].source_query == "deep research"
    # dedup: saving again does not duplicate
    ws.save_reference(cand)
    assert len(ws.list_references()) == 1


def test_evidence_roundtrip(tmp_path):
    from paperagent.schemas.evidence import Evidence
    ws = _ws(tmp_path)
    ws.save_evidence(Evidence(evidence_id="ev001", ref_id="r1",
                              quote="some quote", page=2))
    evs = ws.list_evidence()
    assert len(evs) == 1 and evs[0].page == 2


def test_render_numbers_by_first_appearance(tmp_path):
    ws = _ws(tmp_path)
    r1 = Reference(ref_id="a2020x", title="First", authors=["A"], year=2020)
    r2 = Reference(ref_id="b2021y", title="Second", authors=["B"], year=2021)
    ws.save_reference_obj(r2)
    ws.save_reference_obj(r1)

    text = "Cite second {{ref:b2021y}} then first {{ref:a2020x}} again {{ref:b2021y}}."
    out = render_draft(text, ws.list_references())
    assert "[1] First" not in out  # numbering follows citation order, not DB order
    assert "Cite second [1] then first [2] again [1]." in out
    assert "1. B (2021). Second" in out
    assert "2. A (2020). First" in out


def test_render_unknown_placeholder_left_intact(tmp_path):
    ws = _ws(tmp_path)
    ws.save_reference_obj(Reference(ref_id="a2020x", title="First", authors=["A"]))
    out = render_draft("See {{ref:missing}}.", ws.list_references())
    assert "{{ref:missing}}" in out
