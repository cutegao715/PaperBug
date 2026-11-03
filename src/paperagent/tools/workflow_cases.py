"""Agent access to workflow cases: reading and proposing, never user approval."""
from paperagent.workflows.runtime import WorkflowRuntime


def run(input, ws):
    runtime = WorkflowRuntime(ws)
    operation = input.get("operation", "list")
    if operation == "list":
        return {"cases": runtime.summary()}
    if operation == "read":
        return {"case": runtime.get(input["case_id"])}
    if operation not in {"step", "decision", "inputs", "output", "quality"}:
        raise ValueError("Agent access permits reading and proposals only")
    return {"case": runtime.update(input["case_id"], operation, input.get("data"),
                                    actor="agent", expected_revision=input["expected_revision"])}
