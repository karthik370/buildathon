"""
Risk-scoring rules for each event type.

Each rule is an independently-testable function returning:
    (fired: bool, score_contribution: float, rule_name: str)
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .ingestion import AtRiskEvent


def rule_hard_payment_failure(
    event: AtRiskEvent,
    median_amount: float,
    customer_failure_count: int,
) -> tuple[bool, float, str]:
    """
    Fires when status=='failed'.
    base 0.6, +0.2 if amount > median, +0.15 if customer has >=2 failures.
    Note: events with retry_count >= 3 are intentionally allowed through
    so the max_retries safety gate (in execution_engine.py) can block them.
    The gate — not the detector rule — is the correct enforcement point.
    """
    if event.event_type != "HARD_PAYMENT_FAILURE":
        return False, 0.0, "rule_hard_payment_failure"

    score = 0.6
    if event.amount_at_risk > median_amount:
        score += 0.2
    if customer_failure_count >= 2:
        score += 0.15

    return True, min(score, 1.0), "rule_hard_payment_failure"


def rule_checkout_abandonment(
    event: AtRiskEvent,
) -> tuple[bool, float, str]:
    """
    Fires when status=='abandoned' and session duration < 15 min.
    base 0.4, +0.3 if cart_value > 3000.
    """
    if event.event_type != "CHECKOUT_ABANDONMENT":
        return False, 0.0, "rule_checkout_abandonment"

    duration = event.context.get("duration_minutes", 999)
    if duration >= 15:
        return False, 0.0, "rule_checkout_abandonment"

    score = 0.4
    if event.context.get("cart_value", 0) > 3000:
        score += 0.3

    return True, min(score, 1.0), "rule_checkout_abandonment"


def rule_silent_renewal_failure(
    event: AtRiskEvent,
) -> tuple[bool, float, str]:
    """
    Fires when status=='active' and last_renewal_status=='failed'.
    base 0.7 (high priority - customer didn't choose to leave).
    """
    if event.event_type != "SILENT_RENEWAL_FAILURE":
        return False, 0.0, "rule_silent_renewal_failure"

    # The event was only built if status==active and renewal==failed,
    # so if we reach here it always fires.
    return True, 0.7, "rule_silent_renewal_failure"


def rule_overdue_receivable(
    event: AtRiskEvent,
    max_invoice_amount: float,
) -> tuple[bool, float, str]:
    """
    Fires when due_date < now and status=='unpaid'.
    Score scales with days_overdue/30 and amount/max_invoice_amount.
    """
    if event.event_type != "OVERDUE_RECEIVABLE":
        return False, 0.0, "rule_overdue_receivable"

    days_overdue = event.context.get("days_overdue", 0)
    amount = event.context.get("amount", 0)

    time_score = min(days_overdue / 30.0, 1.0)
    amount_score = amount / max_invoice_amount if max_invoice_amount > 0 else 0
    score = 0.5 * time_score + 0.5 * amount_score

    return True, min(score, 1.0), "rule_overdue_receivable"


# ── Orchestrator ──────────────────────────────────────────────────────

def apply_all_rules(
    events: list[AtRiskEvent],
    transactions: list[dict],
) -> list[AtRiskEvent]:
    """
    Apply the appropriate rule to each event, setting risk_score and
    triggered_rules.  Returns only events that fired at least one rule.
    """
    # Precompute stats needed by rules
    failed_txns = [t for t in transactions if t["status"] == "failed"]
    amounts = [float(t["amount"]) for t in failed_txns]
    median_amount = sorted(amounts)[len(amounts) // 2] if amounts else 0.0

    # Customer failure counts (across all failed txns in the dataset)
    cust_fail_counts: dict[str, int] = Counter(
        t["customer_id"] for t in failed_txns
    )

    # Max invoice amount for normalisation
    max_invoice_amount = max(
        (e.amount_at_risk for e in events if e.event_type == "OVERDUE_RECEIVABLE"),
        default=1.0,
    )

    scored: list[AtRiskEvent] = []
    for event in events:
        fired = False
        rules_triggered: list[str] = []
        score = 0.0

        if event.event_type == "HARD_PAYMENT_FAILURE":
            f, s, name = rule_hard_payment_failure(
                event,
                median_amount,
                cust_fail_counts.get(event.customer_id, 0),
            )
            if f:
                fired = True
                score = s
                rules_triggered.append(name)

        elif event.event_type == "CHECKOUT_ABANDONMENT":
            f, s, name = rule_checkout_abandonment(event)
            if f:
                fired = True
                score = s
                rules_triggered.append(name)

        elif event.event_type == "SILENT_RENEWAL_FAILURE":
            f, s, name = rule_silent_renewal_failure(event)
            if f:
                fired = True
                score = s
                rules_triggered.append(name)

        elif event.event_type == "OVERDUE_RECEIVABLE":
            f, s, name = rule_overdue_receivable(event, max_invoice_amount)
            if f:
                fired = True
                score = s
                rules_triggered.append(name)

        if fired:
            event.risk_score = score
            event.triggered_rules = rules_triggered
            scored.append(event)

    return scored
