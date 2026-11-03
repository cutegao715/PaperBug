from paperagent.domain.entities import ResearchProfile
from paperagent.domain.store import DomainStore
from paperagent.quality.evaluators import EvidenceEvaluator
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.references import Reference
from paperagent.tasks.grant_discovery import GrantDiscovery
from paperagent.tasks.journal_recommender import JournalRecommender


def test_evidence_evaluator_reports_coverage():
    refs = [
        Reference(ref_id="a2024", title="A", authors=["A"], year=2024),
        Reference(ref_id="b2024", title="B", authors=["B"], year=2024),
    ]
    evidence = [
        Evidence(evidence_id="ev001", ref_id="a2024", quote="quote",
                 supports="claim"),
        Evidence(evidence_id="ev002", ref_id="a2024", quote="quote"),
    ]

    report = EvidenceEvaluator().evaluate(references=refs, evidence=evidence)

    assert report.coverage_score == 0.5
    assert report.unsupported_references == ["b2024"]
    assert any("without supports" in w for w in report.warnings)


def test_journal_recommender_persists_opportunities(tmp_path):
    store = DomainStore(tmp_path)
    store.save_profile(ResearchProfile(topics=["LLM writing"]))

    result = JournalRecommender(store).recommend(
        title="LLM writing support for academic writing",
        keywords=["academic writing"],
        project_id="demo",
    )

    assert result["recommendations"]
    opps = store.list_opportunities()
    assert opps[0].opportunity_type == "journal"
    assert opps[0].matched is True
    assert store.list_notifications()[0].type == "radar"


def test_grant_discovery_matches_profile_and_intent(tmp_path):
    store = DomainStore(tmp_path)
    store.save_profile(ResearchProfile(topics=["AI 社科"]))
    store.add_standing_intent(
        description="浙江 AI 社科课题提醒",
        type="grant",
        structured_query={"keywords": ["浙江", "AI", "社科"]},
    )

    result = GrantDiscovery(store).discover([
        {"title": "浙江省 AI 社科课题申报", "source": "manual",
         "summary": "面向 AI 与社会科学交叉研究"},
    ])

    assert result["new_opportunities"] == 1
    assert result["matched"] == 1
    opp = store.list_opportunities()[0]
    assert opp.opportunity_type == "grant"
    assert opp.notified is True
