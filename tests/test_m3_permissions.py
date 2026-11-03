import pytest
from paperagent.workspace.workspace import Workspace
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.artifacts import ToolCall
from paperagent.schemas.policy import ResearchPolicy


def test_proactive_tool_write_denied_and_logged(tmp_path):
    ws=Workspace('p',tmp_path).init('test')
    ws.write_draft('paper.md','test')
    call=ToolCall(tool_id='draft_render',input={'filename':'paper.md'})
    result=ToolExecutor(ResearchPolicy(),ws,role='proactive').execute(call)
    assert result.status=='error' and 'deny' in str(result.errors)
    assert ws.list_tool_calls()[-1]['status']=='error'
    assert ToolExecutor(ResearchPolicy(),ws).execute(call).status=='ok'


def test_scheduler_callback_cannot_escalate_or_write_author_files(tmp_path):
    from paperagent.domain.store import DomainStore
    from paperagent.tasks.scheduler import Scheduler
    store=DomainStore(tmp_path)
    ws=Workspace('p',tmp_path).init('test')
    store.add_automation(name='digest',action='daily_digest',cadence='daily',next_run='2000-01-01T00:00:00Z')
    def bad_digest():
        # Even creating a new default executor inside the callback inherits its role.
        denied=ToolExecutor(ResearchPolicy(),ws).execute(ToolCall(tool_id='draft_render',input={'filename':'paper.md'}))
        assert denied.status=='error'
        ws.write_draft('paper.md','unauthorized')
        return {'title':'bad'}
    log=Scheduler(store,digest_build=bad_digest).tick()
    assert any('denied' in line for line in log)
    assert not (ws.drafts_dir/'paper.md').exists()


def test_proactive_loop_denies_authored_state(tmp_path):
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.schemas.actions import WriteDraftAction
    ws=Workspace('p',tmp_path).init('test')
    class Agent:
        def decide(self,obs): return WriteDraftAction(filename='x.md',content='unauthorized')
    outcome=DecisionLoop(ws,ToolExecutor(ResearchPolicy(),ws),Agent(),role='proactive',log=lambda _:None).run('test')
    assert outcome.status=='too_many_errors'
    assert not (ws.drafts_dir/'x.md').exists()


def test_direct_domain_mutation_and_author_checkpoint_are_protected(tmp_path):
    from paperagent.quality.policies import proactive_run
    from paperagent.domain.lifecycle import LifecycleService
    from paperagent.domain.store import DomainStore
    from paperagent.conversation import ConversationManager
    from paperagent.agent.decision_loop import DecisionLoop
    from paperagent.schemas.actions import WriteDraftAction
    ws=Workspace('p',tmp_path).init('test')
    ConversationManager(ws).begin('author request','author')
    before=(ws.dir/'conversations'/'main.json').read_bytes()
    @proactive_run
    def mutate(): LifecycleService(ws).save(lifecycle_type='thesis',stage='topic',reason='unapproved')
    with pytest.raises(PermissionError): mutate()
    class Agent:
        def decide(self,obs): return WriteDraftAction(filename='x.md',content='no')
    DecisionLoop(ws,ToolExecutor(ResearchPolicy(),ws),Agent(),role='proactive',log=lambda _:None).run('test')
    assert (ws.dir/'conversations'/'main.json').read_bytes()==before


def test_proactive_cannot_create_schedule_or_watcher(tmp_path):
    from paperagent.quality.policies import proactive_run
    from paperagent.domain.store import DomainStore
    store=DomainStore(tmp_path)
    @proactive_run
    def schedule(): store.add_automation(name='unauthorized',action='daily_digest',cadence='daily')
    @proactive_run
    def watch(): store.add_watcher(name='unauthorized',watcher_type='external',source='external')
    with pytest.raises(PermissionError): schedule()
    with pytest.raises(PermissionError): watch()
    assert store.list_automations()==[] and store.list_watchers()==[]


def test_radar_propagates_permission_denial(tmp_path):
    from paperagent.radar.pipeline import LiteratureRadar
    from paperagent.domain.store import DomainStore
    ws=Workspace('p',tmp_path).init('test')
    radar=LiteratureRadar(DomainStore(tmp_path),search_fn=lambda _:ws.write_draft('denied.md','bad'))
    with pytest.raises(PermissionError): radar.run(extra_topics=['topic'])
    assert not (ws.drafts_dir/'denied.md').exists()


def test_research_link_denied_before_any_copy(tmp_path):
    from paperagent.quality.policies import proactive_run
    from paperagent.domain.research_links import ResearchLinks
    source=Workspace('source',tmp_path).init('test');source.write_draft('paper.md','private')
    target=Workspace('target',tmp_path).init('test')
    before={p.relative_to(target.dir):p.read_bytes() for p in target.dir.rglob('*') if p.is_file()}
    @proactive_run
    def link(): ResearchLinks(target).link(source.project_id,'paper.md')
    with pytest.raises(PermissionError): link()
    assert {p.relative_to(target.dir):p.read_bytes() for p in target.dir.rglob('*') if p.is_file()}==before
