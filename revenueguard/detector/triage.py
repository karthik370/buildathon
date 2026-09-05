"""
Triage: compute priority_score and rank events.
"""

from __future__ import annotations
from .ingestion import AtRiskEvent


def compute_priority(events: list[AtRiskEvent]) -> list[AtRiskEvent]:
    """
    Compute priority_score = risk_score * normalised_amount and sort descending.
    Normalised amount = amount / max_amount across all events.
    """
    if not events:
        return []

    max_amount = max(e.amount_at_risk for e in events)
    if max_amount == 0:
        max_amount = 1.0

    for event in events:
        norm_amount = event.amount_at_risk / max_amount
        priority = event.risk_score * norm_amount
        event.context["priority_score"] = round(priority, 6)

    events.sort(key=lambda e: e.context.get("priority_score", 0), reverse=True)
    return events
