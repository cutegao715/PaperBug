"""Opt-in bounded live-model acceptance on fictional data, isolated from user projects.

Run: python tests/verify_project_model.py --live --output <new artifact directory>
Uses the configured provider and credentials. Never prints credentials or config URLs.
Only source reads and local writing actions can execute; no external research tools.
"""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.llm_agent import LLMAgent
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.project_dossier import ProjectDossier, ProjectBindingInput, ProjectRecordInput
from paperagent.domain.project_acceptance import objective_matrix
from paperagent.domain.project_claims import ProjectClaims
from paperagent.env import load_env_file
from paperagent.harness.llm import LLMClient
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.policy import ResearchPolicy
from paperagent.tasks.session import SessionManager
from paperagent.workspace.workspace import Workspace


class LocalWritingAgent:
    allowed = {"read_context", "read_draft", "write_draft", "append_draft", "replace_draft_section",
               "update_task_context", "update_draft_plan", "finish", "ask_user", "chat_reply"}

    def __init__(self, client):
        self.agent = LLMAgent(client)

    def decide(self, obs):
        action = self.agent.decide(obs)
        if action.action not in self.allowed:
            raise ValueError("This fictional acceptance fixture permits only source reading and local writing")
        return action


class DiagnosticClient(LLMClient):
    def __init__(self, root):
        super().__init__(timeout=90)
        self.diagnostics = root / "provider-diagnostics.jsonl"

    def _anthropic_text(self, data):
        metadata = {"stop_reason":data.get("stop_reason"),"usage":data.get("usage"),
                    "blocks":[{"type":b.get("type"),"chars":len(b.get("text") or b.get("thinking") or "")} for b in data.get("content",[])]}
        with self.diagnostics.open("a",encoding="utf-8") as stream:
            stream.write(json.dumps(metadata,ensure_ascii=False)+"\n")
        return super()._anthropic_text(data)


def recheck(root):
    """Replay local checks with current code, preserving the original run result."""
    from paperagent.kernel.project_delivery import check_project_delivery
    from paperagent.workflows.registry import select_workflow
    root = root.resolve()
    original = json.loads((root / "acceptance.json").read_text(encoding="utf-8"))
    ws = Workspace("fictional-acceptance", root)
    tasks = SessionManager(ws).list()
    workflows = {"application":"grant_application", "midterm":"project_midterm_report", "final":"project_final_report"}
    results = []
    for run in original["runs"]:
        task = next(t for t in tasks if t.workflow_id == workflows[run["stage"]])
        filename = run.get("draft") or f"{run['stage']}-样例.md"
        text = ws.read_draft(filename)
        workflow = select_workflow(ws, task.objective, task.workflow_id, task.workflow_options)
        results.append({"stage":run["stage"], "original_status":run["status"], "filename":filename,
                        "kernel":check_project_delivery(ws, text, workflow, filename).model_dump(),
                        "claims":ProjectClaims(ws).audit(text, filename),
                        "matrix":objective_matrix(ws, run["stage"], task.workflow_options.get("report_date"))})
    path = root / "recheck.json"
    path.write_text(json.dumps({"note":"Current rules against saved drafts; does not certify factual meaning or rerun the model.",
                                "runs":results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Recheck report: {path}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--recheck", type=Path, help="Recheck an existing fixture locally without model calls")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.recheck:
        recheck(args.recheck)
        return
    load_env_file()
    # Preserve the normal response budget: reasoning-capable providers can consume
    # a small token cap before producing their final JSON action.
    os.environ.setdefault("PAPERAGENT_LLM_MAX_TOKENS", "8192")
    root = (args.output or Path(tempfile.mkdtemp(prefix="paperagent-model-acceptance-"))).resolve()
    if (root / "projects").exists():
        raise SystemExit("Choose a fresh output directory; existing workspaces are never reused")
    root.mkdir(parents=True, exist_ok=True)
    client = DiagnosticClient(root)
    ws = Workspace("fictional-acceptance", root).init("虚构验收样例：教学案例比较研究，不用于真实申报")
    lifecycle = LifecycleService(ws)
    lifecycle.save(lifecycle_type="project", stage="application", reason="虚构验收样例")
    ws.write_draft("任务书.md", "# 虚构任务书\n计划完成3个教学案例和一份比较报告，期限2026-10-01。采用案例比较，不做问卷。")
    ds = ProjectDossier(ws)
    ds.bind(ProjectBindingInput(role="baseline", filename="任务书.md", reason="虚构样例基线"))
    goal = ds.record(ProjectRecordInput(kind="objective", stage="application", content="完成3个教学案例",
        criteria={"quantity":3,"unit":"个","due_date":"2026-10-01","acceptance":"形成可审阅的案例材料"},
        sources=[{"kind":"draft","key":"任务书.md"}], reason="虚构目标"))
    ds.record(ProjectRecordInput(kind="decision", stage="application", content="采用案例比较，不使用问卷调查",
        sources=[{"kind":"draft","key":"任务书.md"}], reason="虚构方案"))
    report = {"fixture":"fictional; not for actual submission", "provider_configured":True, "runs":[]}
    for stage, workflow in [("application","grant_application"),("midterm","project_midterm_report"),("final","project_final_report")]:
        if stage != "application":
            # A single cumulative total grows from one to two, not one plus two.
            count = 1 if stage == "midterm" else 2
            date = "2026-06-30" if stage == "midterm" else "2026-10-01"
            material = root / f"{stage}-实验日志.txt"
            material.write_text(f"虚构验收样例：截至{date}已完成{count}个案例。" +
                                ("第三案例因样本不足未完成，不得写成完成3个。" if stage == "final" else "后续继续收集案例。"), encoding="utf-8")
            imported = ws.upsert_material(str(material), "imported")
            ds.record(ProjectRecordInput(kind="progress", stage=stage, content=material.read_text(encoding="utf-8"),
                sources=[{"kind":"material","key":imported["material_id"]}], relates_to=[goal["record_id"]],
                measurements=[{"objective_id":goal["record_id"],"as_of":date,"quantity":count,"unit":"个",
                               "completion":"not_completed" if stage=="final" else "in_progress","explanation":"样本不足，按实际结果说明"}], reason="虚构实际进展"))
            lifecycle.save(lifecycle_type="project", stage=stage, reason="验收脚本显式设置实际阶段")
        filename = f"{stage}-样例.md"
        task = SessionManager(ws).create("按虚构样例完成当前阶段正文", workflow)
        SessionManager(ws).update(task.task_id, workflow_options={"scope":"full","delivery":"ready",
            "sections":["项目概况","目标与进展","问题与下一步"],"claim_policy":"traceable","report_date":"2026-10-13"})
        objective = (f"这是虚构验收样例，不是真实申报。完成{stage}阶段的短文档，文件名{filename}，正文300至500汉字。"
            "先read_context project_dossier读取目标、决定和实际记录，再按需读取project_acceptance；"
            "按任务约定的三个章节写作，每条实际成果声明就近使用{{project:记录ID}}。"
            "使用案例比较，不改成问卷。不得虚构论文、获奖或成果数量；明确计划和实际。"
            "材料已足够，未完成就是结果，不需要问用户。write_draft后可read_context project_claims核查，最后finish。"
            "最多8个动作，仅本地读写；按实际结果交付，不得降低用户要求。")
        print(f"running {stage}", flush=True)
        loop = DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=8,max_tool_calls=1),ws),
                            LocalWritingAgent(client), task_id=task.task_id, log=lambda _:None,
                            on_step=lambda step: print(json.dumps({"step":step.get("iteration"),
                                "action":(step.get("action") or {}).get("action") if isinstance(step.get("action"),dict) else None,
                                "status":(step.get("result") or {}).get("status","error")}),flush=True))
        outcome = loop.run(objective)
        draft = ws.read_draft(outcome.draft) if outcome.draft else ""
        audit = ProjectClaims(ws).audit(draft, outcome.draft or "")
        entry = {"stage":stage,"status":outcome.status,"iterations":outcome.iterations,
                 "draft":outcome.draft,"draft_exists":bool(draft),"claim_candidates":audit["candidate_count"],
                 "unresolved_claims":audit["unresolved"],
                 "kernel":outcome.quality_report.project_delivery.model_dump() if outcome.quality_report and outcome.quality_report.project_delivery else None,
                 "actual_stage":lifecycle.get().stage,
                 "actions":[s.get("action",{}).get("action") for s in outcome.steps if isinstance(s.get("action"),dict)]}
        if outcome.status in {"too_many_errors","error"}:
            entry["failure_category"] = "provider_or_action_error"
            # Sanitized classification only; full traces remain within the local fixture.
            codes=re.findall(r"HTTP\s+(\d{3})",outcome.summary)
            if codes: entry["http_status"]=codes[-1]
        report["runs"].append(entry)
        (root / "acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"stage":stage,"status":outcome.status,"iterations":outcome.iterations},ensure_ascii=False),flush=True)
        if entry.get("failure_category"):
            break  # don't repeatedly call an unavailable provider
    report["matrix"] = objective_matrix(ws,"final","2026-10-13")
    report["all_stages_finished"] = len(report["runs"])==3 and all(r["status"]=="finished" and r["draft_exists"] and r["kernel"] and not r["kernel"]["errors"] for r in report["runs"])
    (root / "acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Acceptance report: {root / 'acceptance.json'}",flush=True)


if __name__ == "__main__":
    main()
