"""
Audit Trail: append-only log per case_id stored in SQLite.

Each case has a timeline array of stages (detect -> diagnose -> decide -> gate_check -> execute).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# Database setup
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "revenueguard.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class CaseAudit(Base):
    __tablename__ = "case_audit"

    case_id = Column(String, primary_key=True)
    timeline = Column(Text, default="[]")  # JSON array
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


def init_db():
    """Create tables if they don't exist."""
    Base.metadata.create_all(engine)


def append_stage(case_id: str, stage_data: dict):
    """Append a stage entry to a case's timeline."""
    session = Session()
    try:
        case = session.get(CaseAudit, case_id)
        if case is None:
            case = CaseAudit(case_id=case_id, timeline="[]")
            session.add(case)

        timeline = json.loads(case.timeline)
        stage_data["ts"] = datetime.now().isoformat()
        timeline.append(stage_data)
        case.timeline = json.dumps(timeline)
        case.updated_at = datetime.now()
        session.commit()
    finally:
        session.close()


def get_timeline(case_id: str) -> list[dict]:
    """Get the full timeline for a case."""
    session = Session()
    try:
        case = session.get(CaseAudit, case_id)
        if case is None:
            return []
        return json.loads(case.timeline)
    finally:
        session.close()


def get_all_cases() -> list[dict]:
    """Get summary of all cases."""
    session = Session()
    try:
        cases = session.query(CaseAudit).all()
        result = []
        for case in cases:
            timeline = json.loads(case.timeline)
            result.append({
                "case_id": case.case_id,
                "stages": len(timeline),
                "created_at": case.created_at.isoformat() if case.created_at else None,
                "timeline": timeline,
            })
        return result
    finally:
        session.close()


def clear_all():
    """Clear all audit data (for re-runs)."""
    session = Session()
    try:
        session.query(CaseAudit).delete()
        session.commit()
    finally:
        session.close()


# ── Helper to log a full executed event into the audit trail ──────────

def log_full_event(executed_event: dict):
    """
    Log all stages of a fully-executed event into the audit trail.
    """
    event = executed_event["event"]
    case_id = event.event_id
    diagnosis = executed_event.get("diagnosis", {})
    decision = executed_event.get("decision", {})
    gate_check = executed_event.get("gate_check", {})
    execution = executed_event.get("execution", {})

    # Stage 1: Detect
    append_stage(case_id, {
        "stage": "detect",
        "event_type": event.event_type,
        "entity_id": event.entity_id,
        "customer_id": event.customer_id,
        "amount_at_risk": event.amount_at_risk,
        "risk_score": event.risk_score,
        "triggered_rules": event.triggered_rules,
        "priority_score": event.context.get("priority_score", 0),
    })

    # Stage 2: Diagnose
    append_stage(case_id, {
        "stage": "diagnose",
        "tier": executed_event.get("tier", "unknown"),
        "root_cause": diagnosis.get("root_cause", ""),
        "confidence": diagnosis.get("confidence", 0),
        "reasoning": diagnosis.get("reasoning", ""),
        "recommended_action": diagnosis.get("recommended_action", ""),
    })

    # Stage 3: Decide
    append_stage(case_id, {
        "stage": "decide",
        "candidates": decision.get("candidates_scored", []),
        "chosen_action": decision.get("chosen_action", ""),
        "chosen_ev": decision.get("chosen_ev", 0),
    })

    # Stage 4: Gate check
    append_stage(case_id, {
        "stage": "gate_check",
        "passed": gate_check.get("passed", True),
        "gates_applied": gate_check.get("gates_applied", []),
        "blocked_by": gate_check.get("blocked_by"),
    })

    # Stage 5: Execute
    append_stage(case_id, {
        "stage": "execute",
        "action": execution.get("action_taken", ""),
        "outcome": execution.get("outcome", ""),
        "amount_recovered": execution.get("amount_recovered", 0),
        "details": execution.get("details", ""),
        "channel": execution.get("channel", "n/a"),
        "channel_reason": execution.get("channel_reason", ""),
        "is_showcase_failure_case": execution.get("is_showcase_failure_case", False),
    })
