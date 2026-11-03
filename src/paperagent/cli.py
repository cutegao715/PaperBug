"""PaperAgent CLI - research copilot entrypoint."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from paperagent import __version__
from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.llm_agent import LLMAgent
from paperagent.agent.scripted import ScriptedAgent
from paperagent.demo_assets import make_sample_pdf
from paperagent.evals import run_evals
from paperagent.harness.llm import LLMClient
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.kernel.citation_integrity import build_quality_report
from paperagent.schemas.policy import ResearchPolicy
from paperagent.workspace.render import render_project_draft
from paperagent.workspace.workspace import Workspace


def _ws(args) -> Workspace:
    from paperagent.identity import selected_root
    root = selected_root(Path(args.root)) if args.root else None
    return Workspace(args.project, root=root)


def _print_report(report):
    r = report.citation_integrity
    print(f"  kernel: {r.kernel}  passed={r.passed}  stats={r.stats}")
    for e in r.errors:
        print(f"  ERROR   {e}")
    for w in r.warnings:
        print(f"  warning {w}")


def cmd_init(args):
    ws = _ws(args).init(args.goal or "")
    print(f"workspace ready: {ws.dir}")


def cmd_demo(args):
    root = Path(args.root).resolve() if args.root else Path("./workspace").resolve()
    ws = Workspace("demo", root=root).init(
        "offline walking-skeleton demo: literature overview of research writing agents")
    pdf = make_sample_pdf(root)

    print(f"project: {ws.dir}")
    print(f"sample pdf: {pdf}\n")

    agent = ScriptedAgent(goal=ws.goal, provider="offline_stub", pdf_path=str(pdf))
    executor = ToolExecutor(ResearchPolicy(), ws)
    loop = DecisionLoop(ws, executor, agent, log=lambda m: print(m))

    outcome = loop.run(ws.goal)
    print(f"\noutcome: {outcome.status} after {outcome.iterations} iterations")
    if outcome.quality_report:
        _print_report(outcome.quality_report)

    rendered = render_project_draft(ws, "draft.md")
    out = ws.drafts_dir / "draft.rendered.md"
    out.write_text(rendered, encoding="utf-8")
    print(f"\nrendered draft -> {out}\n")
    print(rendered)


def cmd_run(args):
    try:
        client = LLMClient()
    except Exception as e:
        print(f"error: {e}")
        sys.exit(2)
    ws = _ws(args).init(args.goal)
    agent = LLMAgent(client)
    executor = ToolExecutor(ResearchPolicy(), ws)
    loop = DecisionLoop(ws, executor, agent, log=lambda m: print(m))

    def ask(question: str) -> str:
        return "[non-interactive run: question logged, see trace]"

    outcome = loop.run(args.goal)
    print(f"\noutcome: {outcome.status} after {outcome.iterations} iterations")
    if outcome.summary:
        print(f"summary: {outcome.summary}")
    if outcome.quality_report:
        _print_report(outcome.quality_report)
    if outcome.draft:
        rendered = render_project_draft(ws, outcome.draft)
        out = ws.drafts_dir / outcome.draft.replace(".md", ".rendered.md")
        out.write_text(rendered, encoding="utf-8")
        print(f"rendered draft -> {out}")


def cmd_chat(args):
    try:
        client = LLMClient()
    except Exception as e:
        print(f"error: {e}")
        sys.exit(2)
    goal = args.goal or input("研究目标: ").strip()
    ws = _ws(args).init(goal)
    agent = LLMAgent(client)
    executor = ToolExecutor(ResearchPolicy(), ws)

    def ask(question: str) -> str:
        print(f"\n[agent asks] {question}")
        return input("your answer> ").strip()

    loop = DecisionLoop(ws, executor, agent, ask_user_fn=ask,
                        log=lambda m: print(m))
    outcome = loop.run(goal)
    print(f"\noutcome: {outcome.status} after {outcome.iterations} iterations")
    if outcome.summary:
        print(f"summary: {outcome.summary}")
    if outcome.quality_report:
        _print_report(outcome.quality_report)
    if outcome.draft:
        rendered = render_project_draft(ws, outcome.draft)
        out = ws.drafts_dir / outcome.draft.replace(".md", ".rendered.md")
        out.write_text(rendered, encoding="utf-8")
        print(f"rendered draft -> {out}")


def cmd_validate(args):
    ws = _ws(args)
    if not ws.dir.exists():
        print(f"error: project not found: {ws.dir}")
        sys.exit(2)
    from paperagent.kernel.external_source_check import verify_workspace
    verify_workspace(ws)
    text = ws.read_draft(args.draft)
    report = build_quality_report(text, ws.list_references(),
                                  ws.list_evidence(), resolve=ws.resolve_path)
    _print_report(report)
    sys.exit(0 if report.passed else 1)


def cmd_render(args):
    ws = _ws(args)
    rendered = render_project_draft(ws, args.draft)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(f"rendered -> {args.out}")
    else:
        print(rendered)


def cmd_eval(args):
    golden = Path(args.golden).resolve()
    eval_root = Path(args.eval_root).resolve() if args.eval_root \
        else golden.parent / "_runs"
    results = run_evals(golden, eval_root)
    failed = skipped = 0
    for r in results:
        if r.skipped:
            skipped += 1
            print(f"SKIP {r.task_id}: {r.skipped}")
            continue
        mark = "PASS" if r.passed else "FAIL"
        print(f"{mark}  {r.task_id}  [{'; '.join(r.checks)}]")
        failed += 0 if r.passed else 1
    ran = len(results) - skipped
    print(f"\n{ran - failed}/{ran} passed, {skipped} skipped")
    sys.exit(1 if failed else 0)


def cmd_serve(args):
    import uvicorn
    from paperagent.server import _root, app
    print(f"serving on http://{args.host}:{args.port}  (workspace: {_root()})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _domain_root(args):
    from paperagent.domain.store import DomainStore
    root = Path(args.root).resolve() if args.root else Path("./workspace").resolve()
    from paperagent.identity import selected_root
    return DomainStore(selected_root(root))


def cmd_tick(args):
    """One deterministic Proactive Runtime pass (automations + deadlines)."""
    from paperagent.radar.pipeline import LiteratureRadar
    from paperagent.tasks.scheduler import Scheduler
    store = _domain_root(args)
    sched = Scheduler(store, radar_run=lambda: LiteratureRadar(store).run(
        provider=args.provider))
    for line in sched.tick():
        print(line)
    print("tick complete")


def cmd_radar(args):
    """Run the Literature Radar once and print the result."""
    from paperagent.radar.pipeline import LiteratureRadar
    store = _domain_root(args)
    result = LiteratureRadar(store).run(provider=args.provider)
    print(f"topics={result['topics']} new_hits={result['new_hits']} "
          f"matched={result.get('matched', 0)}")
    if result.get("note"):
        print(result["note"])


def cmd_account(args):
    import os
    from paperagent.identity import connect_account
    identity=connect_account(Path(args.root),os.environ.get('PAPERAGENT_CLOUD_URL',''),os.environ.get('PAPERAGENT_CLOUD_TOKEN',''))
    print('Verified account profile: '+identity.key)
    print('Set PAPERAGENT_ACCOUNT to this profile before starting local services.')


def cmd_cloud_task(args):
    import json
    from paperagent.tasks.cloud_tasks import CloudTasks
    service=CloudTasks(_ws(args))
    if args.task_action=='start':
        result=service.start(args.goal,args.file,confirmed=args.confirm,interval_seconds=args.interval,
                             actions=args.actions.split(','),include_memories=args.include_memories)
    elif args.task_action=='poll': result=service.poll()
    elif args.task_action=='status': result=service.status(args.id)
    elif args.task_action=='cancel': result=service.cancel(args.id)
    elif args.task_action=='pause': result=service.set_enabled(False)
    elif args.task_action=='resume': result=service.set_enabled(True)
    elif args.task_action=='revoke': result=service.revoke(args.id)
    elif args.task_action=='share-memory': result=service.share_memory(args.id,args.confirm)
    elif args.task_action=='monitor': result=service.monitor(args.goal,args.confirm,args.interval or 86400)
    elif args.task_action=='stop-monitor': result=service.stop_monitor(args.id)
    elif args.task_action=='proposals': result=service.executor.proposals()
    elif args.task_action=='undo': result=service.executor.undo(args.id,args.confirm)
    elif args.task_action=='apply': result=service.executor.apply(args.id,args.confirm)
    print(json.dumps(result,ensure_ascii=False,indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paperagent",
                                description="Research Writing Agent (V2-m1)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--root", help="workspace root (default ./workspace)")
        sp.add_argument("--project", default="default")

    sp = sub.add_parser("init", help="create a project workspace")
    common(sp)
    sp.add_argument("--goal", default="")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("demo", help="offline end-to-end demo (no LLM, no network)")
    sp.add_argument("--root", help="workspace root (default ./workspace)")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("run", help="single-shot LLM run")
    common(sp)
    sp.add_argument("--goal", required=True)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("chat", help="interactive LLM session")
    common(sp)
    sp.add_argument("--goal", default=None)
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("validate", help="run citation kernel on a draft")
    common(sp)
    sp.add_argument("--draft", default="draft.md")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("render", help="render {{ref:}} placeholders to [n]")
    common(sp)
    sp.add_argument("--draft", default="draft.md")
    sp.add_argument("--out", default=None)
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("eval", help="run golden evals")
    sp.add_argument("--golden", default="evals/golden")
    sp.add_argument("--eval-root", default=None)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("serve", help="launch web UI + control service")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("tick", help="run one scheduler pass (automations + deadline reminders)")
    sp.add_argument("--root", default=None)
    sp.add_argument("--provider", default=None)
    sp.set_defaults(func=cmd_tick)

    sp = sub.add_parser("radar", help="run the Literature Radar once")
    sp.add_argument("--root", default=None)
    sp.add_argument("--provider", default=None,
                    help="offline_stub for offline runs")
    sp.set_defaults(func=cmd_radar)

    sp = sub.add_parser('account-connect', help='verify cloud identity and create an isolated local profile')
    sp.add_argument('--root', default='./workspace')
    sp.set_defaults(func=cmd_account)
    sp = sub.add_parser('cloud-task', help='managed cloud tasks with explicitly granted local capabilities')
    common(sp)
    sp.add_argument('task_action', choices=['start','poll','status','cancel','pause','resume','revoke','share-memory','apply','undo','proposals','monitor','stop-monitor'])
    sp.add_argument('--goal', default='')
    sp.add_argument('--file', default='')
    sp.add_argument('--id', default='')
    sp.add_argument('--actions', default='read_pdf,verify_citations,search_local')
    sp.add_argument('--interval', type=int)
    sp.add_argument('--include-memories', action='store_true')
    sp.add_argument('--confirm', action='store_true')
    sp.set_defaults(func=cmd_cloud_task)

    from paperagent.secrets import command, NAMES
    sp = sub.add_parser("secrets", help="manage system credentials")
    actions = sp.add_subparsers(dest="secrets_action", required=True)
    setting = actions.add_parser("set")
    setting.add_argument("name", choices=NAMES)
    setting.set_defaults(func=command)
    listing = actions.add_parser("list")
    listing.set_defaults(func=command)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    from paperagent.env import load_env_file
    load_env_file()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    finally:
        from paperagent.tools.mcp_bridge import close_all
        close_all()


if __name__ == "__main__":
    main()
