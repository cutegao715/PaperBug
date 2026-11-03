import requests
import pytest

from paperagent.schemas.references import Reference
from paperagent.workspace.workspace import Workspace


def ref(**changes):
    return Reference(**(dict(ref_id='smith2024test', title='Reliable research evidence',
                            authors=['Alice Smith'], year=2024, doi='10.1234/good') | changes))


def resolver(inp):
    if inp.get('doi') == '10.1234/goof':
        response = requests.Response(); response.status_code = 404
        raise requests.HTTPError(response=response)
    return {'candidates': [ref().model_dump() | {'source': 'crossref', 'publication_years': [2023, 2024]}]}


def test_identity_cache_and_years(tmp_path):
    from paperagent.kernel.external_source_check import ExternalSourceChecker
    calls = []
    checker = ExternalSourceChecker(tmp_path, resolver=lambda q: (calls.append(q) or resolver(q)), min_delay=0)
    assert checker.check(ref())['status'] == 'verified'
    assert checker.check(ref())['status'] == 'verified'
    assert len(calls) == 1
    assert checker.check(ref(year=2022))['status'] == 'mismatch'
    assert checker.check(ref(year=2023))['status'] == 'verified'
    assert checker.check(ref(authors=[]))['status'] == 'unchecked'
    assert len(calls) == 4


def test_title_search_missing_metadata_and_transient_retry(tmp_path, monkeypatch):
    from paperagent.kernel import external_source_check as external
    checker = external.ExternalSourceChecker(tmp_path, resolver=resolver, min_delay=0)
    assert checker.check(ref(doi=None))['status'] == 'verified'
    assert checker.check(ref(title='Completely different subject'))['status'] == 'mismatch'
    missing = external.ExternalSourceChecker(tmp_path/'missing', resolver=lambda q: {
        'candidates': [ref(authors=[]).model_dump()]}, min_delay=0)
    assert missing.check(ref())['status'] == 'unchecked'
    calls = []
    def timeout(query):
        calls.append(query)
        raise requests.Timeout()
    checker = external.ExternalSourceChecker(tmp_path/'transient', resolver=timeout, min_delay=0)
    assert checker.check(ref())['status'] == 'unchecked'
    assert checker.check(ref())['status'] == 'unchecked'
    assert len(calls) == 1
    now = external.time.time()
    monkeypatch.setattr(external.time, 'time', lambda: now+61)
    assert checker.check(ref())['status'] == 'unchecked'
    assert len(calls) == 2


def test_imported_verification_not_trusted_and_cli_checks(tmp_path, monkeypatch):
    from paperagent.kernel import external_source_check as external
    from paperagent.cli import cmd_validate
    from types import SimpleNamespace
    monkeypatch.setenv('PAPERAGENT_REFERENCE_PROVIDER', 'crossref')
    monkeypatch.setenv('PAPERAGENT_REFERENCE_STRICT', '1')
    monkeypatch.setattr(external, 'run_reference_resolve', resolver)
    monkeypatch.setattr(external, '_rate_limit', lambda *a: None)
    ws = Workspace('test', tmp_path).init('test')
    ws.save_reference_obj(ref(doi='10.1234/goof', verification={'status': 'verified'}))
    assert ws.list_references()[0].verification['status'] == 'not_found'
    ws.write_draft('paper.md', '{{ref:smith2024test}}')
    with pytest.raises(SystemExit) as exit:
        cmd_validate(SimpleNamespace(project='test', root=str(tmp_path), draft='paper.md'))
    assert exit.value.code == 1


def test_malformed_crossref_wire_is_unchecked(tmp_path, monkeypatch):
    from paperagent.kernel.external_source_check import ExternalSourceChecker
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message': []}
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response())
    assert ExternalSourceChecker(tmp_path, min_delay=0).check(ref())['status'] == 'unchecked'


def test_rate_limit_serializes_workspaces(tmp_path, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from paperagent.kernel.external_source_check import ExternalSourceChecker
    from paperagent.tools import web
    starts = []; barrier = Barrier(3)
    monkeypatch.setitem(web._LAST_FETCH_BY_HOST, 'api.crossref.org', time.monotonic())
    def query(inp):
        starts.append(time.monotonic())
        return resolver(inp)
    def check(i):
        barrier.wait()
        return ExternalSourceChecker(tmp_path/str(i), resolver=query, min_delay=.08).check(ref())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(check, range(3)))
    assert all(r['status'] == 'verified' for r in results)
    starts.sort()
    assert all(b-a >= .07 for a,b in zip(starts, starts[1:]))


@pytest.mark.parametrize('status', [429, 500, 403])
def test_transient_http_is_unchecked(tmp_path, status):
    from paperagent.kernel.external_source_check import ExternalSourceChecker
    def fail(inp):
        response = requests.Response(); response.status_code = status
        raise requests.HTTPError(response=response)
    assert ExternalSourceChecker(tmp_path, resolver=fail, min_delay=0).check(ref())['status'] == 'unchecked'


def test_strict_only_explicit_doi_404_and_stale_result(tmp_path):
    from paperagent.kernel.external_source_check import ExternalSourceChecker
    from paperagent.kernel.citation_integrity import check_citation_integrity
    checker = ExternalSourceChecker(tmp_path, resolver=resolver, min_delay=0)
    item = ref(doi='10.1234/goof'); item.verification = checker.check(item)
    text = '{{ref:smith2024test}}'
    assert check_citation_integrity(text, [item], [], strict_external=True).errors
    assert not check_citation_integrity(text, [item], [], strict_external=False).errors
    item.doi = '10.1234/good'
    assert not check_citation_integrity(text, [item], [], strict_external=True).errors
    item = ref(doi=None)
    item.verification = ExternalSourceChecker(tmp_path, resolver=lambda q: {'candidates': []}, min_delay=0).check(item)
    report = check_citation_integrity(text, [item], [], strict_external=True)
    assert not report.errors and any('not_found' in w for w in report.warnings)


def test_offline_never_resolves_and_save_render_finish_hooks(tmp_path, monkeypatch):
    from paperagent.kernel import external_source_check as external
    checker = external.ExternalSourceChecker(tmp_path, provider='offline_stub', resolver=lambda q: pytest.fail('network'))
    assert checker.check(ref())['status'] == 'unchecked'
    monkeypatch.setenv('PAPERAGENT_REFERENCE_PROVIDER', 'crossref')
    monkeypatch.setenv('PAPERAGENT_REFERENCE_STRICT', '1')
    monkeypatch.setattr(external, 'run_reference_resolve', resolver)
    monkeypatch.setattr(external, '_rate_limit', lambda *a: None)
    ws = Workspace('test', tmp_path).init('test')
    saved = ws.save_reference_obj(ref(doi='10.1234/goof'))
    assert saved.verification['status'] == 'not_found'
    ws.write_draft('paper.md', '{{ref:smith2024test}}')
    from paperagent.tools.writing import run_draft_render
    with pytest.raises(ValueError, match='external'):
        run_draft_render({'filename': 'paper.md'}, ws)
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.schemas.actions import FinishAction
    from paperagent.harness.tool_executor import ToolExecutor
    from paperagent.schemas.policy import ResearchPolicy
    loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(), ws), None); loop.last_draft = 'paper.md'
    result = loop._finish(FinishAction(summary='done'), 'test')
    assert result['status'] == 'error'
    assert any('external' in e for e in result['errors'])
