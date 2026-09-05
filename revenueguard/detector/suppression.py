"""
Suppression: deduplicate events so the same entity_id doesn't appear twice.
"""

from __future__ import annotations
from .ingestion import AtRiskEvent


def dedupe_events(events: list[AtRiskEvent]) -> list[AtRiskEvent]:
    """
    Remove duplicate events for the same entity_id.
    Keeps the event with the highest risk_score if duplicates exist.
    """
    seen: dict[str, AtRiskEvent] = {}
    for event in events:
        eid = event.entity_id
        if eid not in seen or event.risk_score > seen[eid].risk_score:
            seen[eid] = event
    return list(seen.values())
