"""
Ingestion layer: loads all 5 CSVs and normalises each row into AtRiskEvent.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class AtRiskEvent(BaseModel):
    """Unified model for every kind of revenue-at-risk signal."""

    event_id: str
    event_type: Literal[
        "HARD_PAYMENT_FAILURE",
        "CHECKOUT_ABANDONMENT",
        "SILENT_RENEWAL_FAILURE",
        "OVERDUE_RECEIVABLE",
        "PROMISE_BROKEN",
        "PROMISE_DUE_SOON",
    ]
    entity_id: str          # transaction_id / session_id / subscription_id / invoice_id
    customer_id: str
    amount_at_risk: float
    risk_score: float = 0.0       # computed in rules.py
    triggered_rules: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=datetime.now)
    context: dict = Field(default_factory=dict)


def _load_csv(filename: str) -> list[dict]:
    fp = DATA_DIR / filename
    with open(fp, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_customers() -> dict[str, dict]:
    """Returns dict keyed by customer_id."""
    rows = _load_csv("customers.csv")
    return {r["customer_id"]: r for r in rows}


def load_payment_transactions() -> list[dict]:
    return _load_csv("payment_transactions.csv")


def load_checkout_sessions() -> list[dict]:
    return _load_csv("checkout_sessions.csv")


def load_subscriptions() -> list[dict]:
    return _load_csv("subscriptions.csv")


def load_invoices() -> list[dict]:
    return _load_csv("invoices.csv")


# ── Raw event builders (without scoring) ──────────────────────────────

def build_payment_events(transactions: list[dict]) -> list[AtRiskEvent]:
    """Build raw events from failed payment transactions."""
    events = []
    for t in transactions:
        if t["status"] != "failed":
            continue
        # Build context dict; include last_contact_at for edge-case rows only
        ctx: dict = {
            "failure_code": t.get("failure_code", ""),
            "failure_source": t.get("failure_source", ""),
            "failure_category": t.get("failure_category", ""),
            "payment_method": t["payment_method"],
            "retry_count": int(t.get("retry_count", 0)),
            "is_disputed": t.get("is_disputed", "False") == "True",
            "timestamp": t["timestamp"],
        }
        # Optional: created_at for stale-check gate (present on edge-case rows)
        if t.get("created_at"):
            ctx["created_at"] = t["created_at"]
        # Optional: last_contact_at for cooldown gate (present on edge-case rows)
        # "RECENT" is a dynamic marker: substituted at runtime to now - 5min
        # so the cooldown gate fires correctly on every pipeline run.
        if t.get("last_contact_at"):
            lca = t["last_contact_at"]
            if lca == "RECENT":
                from datetime import timedelta
                lca = (datetime.now() - timedelta(minutes=5)).isoformat()
            ctx["last_contact_at"] = lca

        events.append(AtRiskEvent(
            event_id=f"evt_pay_{t['transaction_id']}",
            event_type="HARD_PAYMENT_FAILURE",
            entity_id=t["transaction_id"],
            customer_id=t["customer_id"],
            amount_at_risk=float(t["amount"]),
            detected_at=datetime.fromisoformat(t["timestamp"]),
            context=ctx,
        ))
    return events


def build_checkout_events(sessions: list[dict]) -> list[AtRiskEvent]:
    """Build raw events from abandoned checkout sessions."""
    events = []
    for s in sessions:
        if s["status"] != "abandoned":
            continue
        started = datetime.fromisoformat(s["started_at"])
        last_act = datetime.fromisoformat(s["last_activity_at"])
        duration_min = (last_act - started).total_seconds() / 60.0
        events.append(AtRiskEvent(
            event_id=f"evt_chk_{s['session_id']}",
            event_type="CHECKOUT_ABANDONMENT",
            entity_id=s["session_id"],
            customer_id=s["customer_id"],
            amount_at_risk=float(s["cart_value"]),
            detected_at=last_act,
            context={
                "cart_value": float(s["cart_value"]),
                "started_at": s["started_at"],
                "last_activity_at": s["last_activity_at"],
                "duration_minutes": round(duration_min, 2),
            },
        ))
    return events


def build_subscription_events(subs: list[dict]) -> list[AtRiskEvent]:
    """Build raw events from silently-failed subscription renewals."""
    events = []
    for s in subs:
        if s["status"] != "active" or s["last_renewal_status"] != "failed":
            continue
        events.append(AtRiskEvent(
            event_id=f"evt_sub_{s['subscription_id']}",
            event_type="SILENT_RENEWAL_FAILURE",
            entity_id=s["subscription_id"],
            customer_id=s["customer_id"],
            amount_at_risk=float(s["monthly_amount"]),
            detected_at=datetime.fromisoformat(s["last_renewal_attempt_at"]),
            context={
                "monthly_amount": float(s["monthly_amount"]),
                "failure_code": s.get("last_renewal_failure_code", ""),
                "last_renewal_attempt_at": s["last_renewal_attempt_at"],
            },
        ))
    return events


def build_invoice_events(invoices: list[dict]) -> list[AtRiskEvent]:
    """Build raw events from overdue / unpaid invoices."""
    now = datetime.now()
    events = []
    for inv in invoices:
        if inv["status"] != "unpaid":
            continue
        due = datetime.fromisoformat(inv["due_date"])
        if due >= now:
            continue  # not yet overdue
        days_overdue = (now - due).days
        events.append(AtRiskEvent(
            event_id=f"evt_inv_{inv['invoice_id']}",
            event_type="OVERDUE_RECEIVABLE",
            entity_id=inv["invoice_id"],
            customer_id=inv["customer_id"],
            amount_at_risk=float(inv["amount"]),
            detected_at=now,
            context={
                "amount": float(inv["amount"]),
                "due_date": inv["due_date"],
                "days_overdue": days_overdue,
                "created_at": inv["created_at"],
                "promise_to_pay_date": inv.get("promise_to_pay_date", ""),
                "promise_to_pay_status": inv.get("promise_to_pay_status", ""),
            },
        ))
    return events


def ingest_all() -> tuple[
    list[AtRiskEvent],
    dict[str, dict],
    list[dict],
]:
    """
    Load all CSVs and build raw (unscored) events.

    Returns:
        (events, customers_lookup, all_transactions)
    """
    customers = load_customers()
    transactions = load_payment_transactions()
    checkouts = load_checkout_sessions()
    subs = load_subscriptions()
    invoices = load_invoices()

    events: list[AtRiskEvent] = []
    events.extend(build_payment_events(transactions))
    events.extend(build_checkout_events(checkouts))
    events.extend(build_subscription_events(subs))
    events.extend(build_invoice_events(invoices))

    return events, customers, transactions
