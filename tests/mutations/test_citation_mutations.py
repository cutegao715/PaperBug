"""Offline mutation corpus. Numeric semantic consistency is an explicit gap."""
import shutil
import pytest
import requests

from paperagent.demo_assets import make_text_pdf
from paperagent.schemas.references import Reference
from paperagent.schemas.evidence import Evidence
from paperagent.workspace.workspace import Workspace
from paperagent.kernel.external_source_check import ExternalSourceChecker
from paperagent.kernel.citation_integrity import check_citation_integrity

QUOTE = 'The measured accuracy was 0.8 in the controlled study.'


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    monkeypatch.setenv('PAPERAGENT_REFERENCE_PROVIDER', 'offline_stub')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('real HTTP forbidden'))


@pytest.fixture
def baseline(tmp_path):
    ws = Workspace('baseline', tmp_path).init('mutation baseline')
    make_text_pdf(ws.dir / 'source.pdf', [QUOTE])
    ws.save_reference_obj(Reference(ref_id='smith2024study', title='Controlled accuracy study',
                                   authors=['Alice Smith'], year=2024, doi='10.1234/study'))
    ws.save_evidence(Evidence(evidence_id='ev1', ref_id='smith2024study', quote=QUOTE,
                             source_path='source.pdf', page=1))
    ws.write_draft('paper.md', QUOTE + ' {{ref:smith2024study}}')
    copied = Workspace('mutation', tmp_path)
    shutil.copytree(ws.dir, copied.dir)
    evidence = copied.list_evidence()[0]
    evidence.source_path = str(copied.dir / 'source.pdf')
    copied.evidence_path.write_text(evidence.model_dump_json() + '\n', encoding='utf-8')
    return copied


def fixture_resolver(query):
    if query.get('doi') != '10.1234/study':
        response = requests.Response(); response.status_code = 404
        raise requests.HTTPError(response=response)
    return {'candidates': [dict(title='Controlled accuracy study', authors=['Alice Smith'],
                               year=2024, doi='10.1234/study', source='crossref')]}


@pytest.mark.parametrize('mutation,severity,identifier', [
    ('clean', None, None), ('doi', 'error', 'smith2024study'),
    ('quote', 'error', 'ev1'), ('delete_evidence', 'warning', 'smith2024study'),
    ('year', 'warning', 'smith2024study'), ('year_minus', 'warning', 'smith2024study'),
    ('number', None, None), ('number_minus', None, None),
])
def test_mutations(baseline, mutation, severity, identifier, monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('real HTTP forbidden'))
    refs = baseline.list_references(); evidence = baseline.list_evidence()
    text = baseline.read_draft('paper.md')
    if mutation == 'doi': refs[0].doi = '10.1234/studz'
    if mutation == 'quote': evidence[0].quote = QUOTE.replace('accuracy', 'recall')
    if mutation == 'delete_evidence': evidence.clear()
    if mutation == 'year': refs[0].year += 1
    if mutation == 'year_minus': refs[0].year -= 1
    if mutation == 'number': text = text.replace('0.8', '0.9')
    if mutation == 'number_minus': text = text.replace('0.8', '0.7')
    checker = ExternalSourceChecker(baseline.dir, resolver=fixture_resolver, min_delay=0)
    for ref in refs: ref.verification = checker.check(ref)
    report = check_citation_integrity(text, refs, evidence, baseline.resolve_path, strict_external=True)
    if severity == 'error':
        assert any(identifier in e for e in report.errors)
    else:
        assert not report.errors
        if severity == 'warning': assert any(identifier in w for w in report.warnings)
    if mutation.startswith('year'): assert any('mismatch' in w for w in report.warnings)
    if mutation == 'clean': assert not report.warnings
    if mutation.startswith('number'):
        # No claim-to-number mapping: no general semantic detector is claimed.
        assert not report.warnings
