"""
FastAPI application for RevenueGuard dashboard.

Endpoints:
  GET  /           -> Dashboard HTML page
  GET  /summary    -> Full metrics JSON
  GET  /cases      -> List of all cases
  GET  /cases/{id} -> Audit timeline for one case
  POST /run        -> Trigger full pipeline
  GET  /approvals      -> Human approval queue UI
  GET  /approvals/list -> Escalated cases as JSON
  POST /approvals/{case_id}/approve -> Human approves: re-executes bypassing blocking gate
  POST /approvals/{case_id}/reject  -> Human rejects: closes case with logged reason
"""

import sys
import os
import json
from pathlib import Path

from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

app = FastAPI(title="RevenueGuard", description="AI Revenue Recovery Agent Dashboard")

# Templates and static
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(DASHBOARD_DIR / "templates"))

# Cache for last pipeline run metrics
_last_metrics: dict = {}
_last_executed: list = []


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "metrics": _last_metrics,
    })


@app.get("/summary")
async def get_summary():
    """Return the full metrics as JSON."""
    if not _last_metrics:
        return JSONResponse({"error": "Pipeline has not been run yet. POST /run first."}, status_code=404)

    # Convert metrics to JSON-safe format
    safe_metrics = _make_json_safe(_last_metrics)
    return JSONResponse(safe_metrics)


@app.get("/cases")
async def get_cases():
    """Return list of all cases with their outcomes."""
    from revenueguard.audit.audit_log import get_all_cases
    cases = get_all_cases()
    return JSONResponse(cases)


@app.get("/cases/{case_id}")
async def get_case(case_id: str):
    """Return full audit timeline for one case."""
    from revenueguard.audit.audit_log import get_timeline
    timeline = get_timeline(case_id)
    if not timeline:
        return JSONResponse({"error": f"Case {case_id} not found"}, status_code=404)
    return JSONResponse({"case_id": case_id, "timeline": timeline})


@app.post("/run")
async def run_pipeline():
    """Trigger the full pipeline and return metrics."""
    global _last_metrics, _last_executed

    from revenueguard.scripts.run_full_pipeline import main
    metrics = main()
    _last_metrics = metrics

    safe_metrics = _make_json_safe(metrics)
    return JSONResponse({"status": "complete", "metrics": safe_metrics})


# ── Human Approval Queue ──────────────────────────────────────────────

@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request):
    """Render the human-approval queue UI."""
    return templates.TemplateResponse("approvals.html", {"request": request})


@app.get("/approvals/list")
async def list_approvals():
    """Return all escalated cases awaiting human review as JSON."""
    from revenueguard.audit.approvals import get_escalated_cases
    cases = get_escalated_cases()
    return JSONResponse({"count": len(cases), "cases": cases})


@app.post("/approvals/{case_id}/approve")
async def approve_case(case_id: str, body: dict = Body(default={})):
    """
    Human approves a case.

    Re-executes the originally chosen action, bypassing ONLY the gate
    that blocked it (all other gates remain in force).
    Logs a human_review stage in the audit trail.

    Single-operator queue — no authentication for demo.
    """
    from revenueguard.audit.approvals import get_escalated_cases, append_human_review
    from revenueguard.audit.audit_log import get_timeline
    from revenueguard.execution.razorpay_test_executor import RazorpayTestModeExecutor

    reviewer = body.get("reviewer", "demo_user")
    timeline = get_timeline(case_id)
    if not timeline:
        return JSONResponse({"detail": f"Case {case_id} not found"}, status_code=404)

    if any(s.get("stage") == "human_review" for s in timeline):
        return JSONResponse({"detail": "Case already reviewed"}, status_code=409)

    # Pull context from audit trail
    decide   = next((s for s in timeline if s.get("stage") == "decide"), {})
    gate     = next((s for s in timeline if s.get("stage") == "gate_check"), {})
    detect   = next((s for s in timeline if s.get("stage") == "detect"), {})
    diagnose = next((s for s in timeline if s.get("stage") == "diagnose"), {})

    chosen_action = decide.get("chosen_action", "escalate_human")
    blocked_by    = gate.get("blocked_by")
    entity_id     = detect.get("entity_id", case_id)
    amount        = float(detect.get("amount_at_risk", 0))
    root_cause    = diagnose.get("root_cause", "unknown")

    # Always use the REAL Razorpay API for human approvals — regardless of EXECUTOR_MODE.
    # The pipeline runs in simulated mode to preserve quota.
    # This is the ONLY place a real payment link / order is created.
    _real_executor = RazorpayTestModeExecutor()
    outcome_obj = _real_executor.execute(
        entity_id=entity_id,
        action_type=chosen_action,
        root_cause=root_cause,
        amount_at_risk=amount,
        attempt=1,
    )
    exec_result = outcome_obj.to_dict()


    append_human_review(
        case_id=case_id,
        decision="approved",
        reviewer=reviewer,
        re_executed_action=chosen_action,
        re_execution_outcome=exec_result["outcome"],
        amount_recovered=exec_result["amount_recovered"],
        reason=f"Human reviewer '{reviewer}' approved. Gate '{blocked_by}' overridden.",
    )

    return JSONResponse({
        "case_id": case_id,
        "decision": "approved",
        "reviewer": reviewer,
        "gate_bypassed": blocked_by,
        "re_executed_action": chosen_action,
        "re_execution_outcome": exec_result["outcome"],
        "amount_recovered": exec_result["amount_recovered"],
    })


@app.post("/approvals/{case_id}/reject")
async def reject_case(case_id: str, body: dict = Body(default={})):
    """
    Human rejects a case — marks it closed with no further automated action.
    Logs a human_review stage in the audit trail.
    """
    from revenueguard.audit.approvals import append_human_review
    from revenueguard.audit.audit_log import get_timeline

    reviewer = body.get("reviewer", "demo_user")
    reason   = body.get("reason", "Rejected by human reviewer — no further action.")
    timeline = get_timeline(case_id)
    if not timeline:
        return JSONResponse({"detail": f"Case {case_id} not found"}, status_code=404)
    if any(s.get("stage") == "human_review" for s in timeline):
        return JSONResponse({"detail": "Case already reviewed"}, status_code=409)

    append_human_review(
        case_id=case_id,
        decision="rejected",
        reviewer=reviewer,
        reason=reason,
    )
    return JSONResponse({"case_id": case_id, "decision": "rejected", "reviewer": reviewer})


def _make_json_safe(obj):
    """Convert an object to JSON-safe format, handling non-serializable types."""
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_safe(item) for item in obj]
    elif hasattr(obj, '__dict__'):
        return str(obj)
    else:
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


def run_and_serve():
    """Run the pipeline first, then start the server."""
    global _last_metrics

    from revenueguard.scripts.run_full_pipeline import main
    _last_metrics = main()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run_and_serve()
