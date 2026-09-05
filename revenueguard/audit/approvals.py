"""
get_escalated_cases() helper for the human-approval queue.
Retrieves all cases where the last execute stage outcome indicates
human escalation (either gate-blocked or both-actions-failed).
"""

from __future__ import annotations

import json
from datetime import datetime

from .audit_log import Session, CaseAudit


def get_escalated_cases() -> list[dict]:
    """
    Return all cases currently in an escalated state, enriched with
    the diagnosis/decision/gate context needed by the approvals UI.

    A case is escalated if its most recent execute stage has
    outcome in {'escalated_to_human', 'escalated_after_failures'}
    AND it does not yet have a 'human_review' stage (i.e., not yet
    actioned by a human).
    """
    session = Session()
    try:
        cases = session.query(CaseAudit).all()
        escalated = []
        for case in cases:
            timeline = json.loads(case.timeline)
            if not timeline:
                continue

            # Skip cases already reviewed by a human
            if any(s.get("stage") == "human_review" for s in timeline):
                continue

            # Skip control-group cases (holdout — no intervention applied)
            if any(s.get("stage") == "control_baseline" for s in timeline):
                continue

            # Find the last execute stage
            exec_stages = [s for s in timeline if s.get("stage") == "execute"]
            if not exec_stages:
                continue
            last_exec = exec_stages[-1]

            if last_exec.get("outcome") not in (
                "escalated_to_human", "escalated_after_failures", "pending_approval"
            ):
                continue

            # Pull context from earlier stages
            detect = next((s for s in timeline if s.get("stage") == "detect"), {})
            diagnose = next((s for s in timeline if s.get("stage") == "diagnose"), {})
            gate = next((s for s in timeline if s.get("stage") == "gate_check"), {})
            decide = next((s for s in timeline if s.get("stage") == "decide"), {})

            escalated.append({
                "case_id": case.case_id,
                "event_type": detect.get("event_type", ""),
                "entity_id": detect.get("entity_id", ""),
                "customer_id": detect.get("customer_id", ""),
                "amount_at_risk": detect.get("amount_at_risk", 0.0),
                "root_cause": diagnose.get("root_cause", ""),
                "reasoning": diagnose.get("reasoning", ""),
                "recommended_action": diagnose.get("recommended_action", ""),
                "chosen_action": decide.get("chosen_action", ""),
                "blocked_by": gate.get("blocked_by"),
                "gates_applied": gate.get("gates_applied", []),
                "outcome": last_exec.get("outcome", ""),
                "details": last_exec.get("details", ""),
                "escalated_at": last_exec.get("ts", ""),
                "updated_at": case.updated_at.isoformat() if case.updated_at else "",
            })

        # Sort by amount descending (highest value cases first)
        escalated.sort(key=lambda x: x["amount_at_risk"], reverse=True)
        return escalated
    finally:
        session.close()


def append_human_review(
    case_id: str,
    decision: str,  # "approved" | "rejected"
    reviewer: str,
    re_executed_action: str | None = None,
    re_execution_outcome: str | None = None,
    amount_recovered: float = 0.0,
    reason: str = "",
):
    """Append a human_review stage to a case's audit timeline."""
    from .audit_log import append_stage
    append_stage(case_id, {
        "stage": "human_review",
        "decision": decision,
        "reviewer": reviewer,
        "re_executed_action": re_executed_action,
        "re_execution_outcome": re_execution_outcome,
        "amount_recovered": amount_recovered,
        "reason": reason,
    })
