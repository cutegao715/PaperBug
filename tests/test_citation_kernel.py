"""Citation Integrity kernel: the deterministic quality gate."""
from paperagent.demo_assets import make_text_pdf
from paperagent.kernel.citation_integrity import check_citation_integrity
from paperagent.schemas.evidence import Evidence
from paperagent.schemas.references import Reference


def _refs():
    return [Reference(ref_id="a2020x", title="First", authors=["A"], year=2020)]


def test_clean_draft_passes(tmp_path):
    pdf = make_text_pdf(tmp_path / "p.pdf", ["Deterministic kernels catch fake quotes."])
    ev = Evidence(evidence_id="ev1", ref_id="a2020x",
                  quote="Deterministic kernels catch fake quotes.",
                  page=1, source_path=str(pdf))
    report = check_citation_integrity(
        "A claim {{ref:a2020x}}.", _refs(), [ev], resolve=lambda p: pdf)
    assert report.passed
    assert not report.errors


def test_fabricated_citation_is_an_error():
    report = check_citation_integrity("Bad {{ref:ghost2020}}.", _refs(), [])
    assert not report.passed
    assert any("ghost2020" in e for e in report.errors)


def test_malformed_placeholder_is_an_error():
    report = check_citation_integrity("Broken {{ref: and {{ref:a2020x}}.",
                                      _refs(), [])
    assert not report.passed
    assert any("malformed" in e for e in report.errors)


def test_repeated_valid_placeholder_is_not_malformed():
    report = check_citation_integrity(
        "Repeat {{ref:a2020x}} and cite again {{ref:a2020x}}.",
        _refs(),
        [],
    )
    assert not any("malformed" in e for e in report.errors)


def test_uncited_reference_is_a_warning_not_error():
    report = check_citation_integrity("Only cites {{ref:a2020x}}.", _refs(), [])
    assert report.passed
    # a second uncited ref triggers the warning path
    refs = _refs() + [Reference(ref_id="b2021y", title="B", authors=["B"])]
    report2 = check_citation_integrity("Only {{ref:a2020x}}.", refs, [])
    assert report2.passed
    assert any("never cited" in w for w in report2.warnings)


def test_quote_not_in_source_is_an_error(tmp_path):
    pdf = make_text_pdf(tmp_path / "p.pdf", ["Totally different content here."])
    ev = Evidence(evidence_id="ev1", ref_id="a2020x",
                  quote="This sentence is not in the source.", page=1,
                  source_path=str(pdf))
    report = check_citation_integrity(
        "Claim {{ref:a2020x}}.", _refs(), [ev], resolve=lambda p: pdf)
    assert not report.passed
    assert any("quote not found" in e for e in report.errors)


def test_missing_source_is_a_warning(tmp_path):
    ev = Evidence(evidence_id="ev1", ref_id="a2020x", quote="whatever",
                  page=1, source_path=str(tmp_path / "nope.pdf"))
    report = check_citation_integrity(
        "Claim {{ref:a2020x}}.", _refs(), [ev], resolve=lambda p: p)
    assert report.passed
    assert any("cannot verify" in w for w in report.warnings)


def test_non_pdf_source_is_warning_without_pdf_parse_error(tmp_path):
    docx = tmp_path / "source.docx"
    docx.write_bytes(b"PK fake docx")
    ev = Evidence(evidence_id="ev1", ref_id="a2020x", quote="whatever",
                  page=1, source_path=str(docx))
    report = check_citation_integrity(
        "Claim {{ref:a2020x}}.", _refs(), [ev], resolve=lambda p: p)

    assert report.passed
    assert any("source-not-pdf" in w for w in report.warnings)


def test_citation_without_evidence_warns():
    report = check_citation_integrity("Claim {{ref:a2020x}}.", _refs(), [])
    assert report.passed
    assert any("no evidence card" in w for w in report.warnings)
