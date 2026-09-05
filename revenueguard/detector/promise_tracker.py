"""
Promise-to-Pay Tracker: detects PROMISE_BROKEN and PROMISE_DUE_SOON events
from the invoices CSV and routes them through the full detect→diagnose→decide→
execute→audit pipeline.

Event types:
    PROMISE_BROKEN   — promise_to_pay_status == 'broken' (committed date passed, still unpaid)
    PROMISE_DUE_SOON — promise_to_pay_date within 24h AND status != 'kept' AND invoice unpaid

Both fire only for unpaid invoices. 'kept' promises and paid invoices are ignored.

Architecture note: These are standard AtRiskEvent objects using the same pipeline
as OVERDUE_RECEIVABLE, HARD_PAYMENT_FAILURE, etc. No parallel pipeline. The
detect→diagnose→decide→execute→audit chain handles them identically.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..detector.ingestion import AtRiskEvent, load_invoices


def build_promise_events(
    invoices: list[dict] | None = None,
    now: datetime | None = None,
    due_soon_hours: int = 24,
) -> list[AtRiskEvent]:
    """
    Build PROMISE_BROKEN and PROMISE_DUE_SOON events from invoices.

    Parameters
    ----------
    invoices       : Pre-loaded invoice rows. If None, loads from CSV.
    now            : Reference datetime (defaults to datetime.now(); injectable for testing).
    due_soon_hours : How many hours ahead counts as 'due soon' (default 24).

    Returns
    -------
    List of AtRiskEvent, sorted by amount descending.
    """
    if invoices is None:
        invoices = load_invoices()
    if now is None:
        now = datetime.now()

    due_soon_cutoff = now + timedelta(hours=due_soon_hours)
    events: list[AtRiskEvent] = []

    for inv in invoices:
        if inv["status"] != "unpaid":
            continue

        p2p_date_str = inv.get("promise_to_pay_date", "")
        p2p_status   = inv.get("promise_to_pay_status", "")
        amount       = float(inv["amount"])

        # PROMISE_BROKEN: explicit 'broken' status from the data
        if p2p_status == "broken":
            p2p_date = datetime.fromisoformat(p2p_date_str) if p2p_date_str else None
            days_late = (now - p2p_date).days if p2p_date else 0
            events.append(AtRiskEvent(
                event_id=f"evt_p2p_broken_{inv['invoice_id']}",
                event_type="PROMISE_BROKEN",
                entity_id=inv["invoice_id"],
                customer_id=inv["customer_id"],
                amount_at_risk=amount,
                detected_at=now,
                context={
                    "invoice_id": inv["invoice_id"],
                    "amount": amount,
                    "due_date": inv["due_date"],
                    "promise_to_pay_date": p2p_date_str,
                    "promise_to_pay_status": p2p_status,
                    "days_promise_overdue": days_late,
                    "created_at": inv.get("created_at", ""),
                },
            ))
            continue

        # PROMISE_DUE_SOON: promise date is within the next 24h AND not already kept
        if p2p_date_str and p2p_status not in ("kept", "broken"):
            p2p_date = datetime.fromisoformat(p2p_date_str)
            if now <= p2p_date <= due_soon_cutoff:
                hours_until = (p2p_date - now).total_seconds() / 3600
                events.append(AtRiskEvent(
                    event_id=f"evt_p2p_soon_{inv['invoice_id']}",
                    event_type="PROMISE_DUE_SOON",
                    entity_id=inv["invoice_id"],
                    customer_id=inv["customer_id"],
                    amount_at_risk=amount,
                    detected_at=now,
                    context={
                        "invoice_id": inv["invoice_id"],
                        "amount": amount,
                        "due_date": inv["due_date"],
                        "promise_to_pay_date": p2p_date_str,
                        "promise_to_pay_status": p2p_status,
                        "hours_until_promise_due": round(hours_until, 2),
                        "created_at": inv.get("created_at", ""),
                    },
                ))

    events.sort(key=lambda e: e.amount_at_risk, reverse=True)
    return events
