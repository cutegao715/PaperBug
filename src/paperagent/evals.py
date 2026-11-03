"""Golden eval runner for the walking skeleton.

Offline evals run ScriptedAgent end-to-end in an isolated workspace and
check hard expectations. LLM-based evals are listed in evals/golden/
with ``mode: llm`` and are skipped when no API key is configured - the
harness is ready, the runs are opt-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.agent.scripted import ScriptedAgent
from paperagent.demo_assets import make_sample_pdf
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.policy import ResearchPolicy
from paperagent.workspace.workspace import Workspace


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    skipped: str = ""
    checks: list[str] = field(default_factory=list)


def run_task(task: dict, eval_root: Path) -> EvalResult:
    task_id = task["id"]
    mode = task.get("mode", "scripted")
    if mode == "llm":
        return EvalResult(task_id, False,
                          skipped="llm mode: set PAPERAGENT_LLM_* to run manually")
    if mode != "scripted":
        return EvalResult(task_id, False, skipped=f"unknown mode {mode}")

    ws = Workspace(task_id, root=eval_root).init(task["goal"])
    pdf_path = None
    if task.get("pdf"):
        pdf_path = str(make_sample_pdf(eval_root))

    provider = task.get("provider", "offline_stub")
    policy = ResearchPolicy(**task.get("policy", {}))
    agent = ScriptedAgent(goal=task["goal"], provider=provider, pdf_path=pdf_path)
    # provider reaches the search tool through the action; offline_stub is the
    # only scripted provider that works without network
    if provider != "offline_stub":
        return EvalResult(task_id, False, skipped=f"scripted mode requires offline_stub")

    loop = DecisionLoop(ws, ToolExecutor(policy, ws), agent,
                        log=lambda msg: None)
    outcome = loop.run(task["goal"])

    checks: list[str] = []
    ok = True

    exp = task.get("expectations", {})
    want_status = exp.get("status", "finished")
    if outcome.status != want_status:
        ok = False
        checks.append(f"status: want {want_status}, got {outcome.status}")
    else:
        checks.append(f"status={outcome.status}")

    if "min_references" in exp:
        n = len(ws.list_references())
        good = n >= exp["min_references"]
        ok = ok and good
        checks.append(f"references={n} (>= {exp['min_references']}) {good}")
    if "min_evidence" in exp:
        n = len(ws.list_evidence())
        good = n >= exp["min_evidence"]
        ok = ok and good
        checks.append(f"evidence={n} (>= {exp['min_evidence']}) {good}")
    if exp.get("must_pass_kernel"):
        report = outcome.quality_report
        good = bool(report and report.passed)
        ok = ok and good
        checks.append(f"kernel_passed={good}")

    return EvalResult(task_id, ok, checks=checks)


def run_evals(golden_dir: Path, eval_root: Path) -> list[EvalResult]:
    results = []
    for f in sorted(golden_dir.glob("*.yaml")):
        task = yaml.safe_load(f.read_text(encoding="utf-8"))
        results.append(run_task(task, eval_root))
    return results
