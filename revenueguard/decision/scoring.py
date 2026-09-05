"""
EV-based scoring for candidate actions.

EV(action) = P_success(action, root_cause) * amount_at_risk - cost(action) - annoyance_penalty

All priors are hand-set and documented assumptions. See README for discussion.
"""

from __future__ import annotations

# ── P_success priors (action, root_cause) -> probability ──────────────
# These are HAND-SET ASSUMPTIONS, not learned from real data.
# Documented here and in README.

P_SUCCESS: dict[tuple[str, str], float] = {
    # (action, root_cause) -> probability of successful recovery

    # Insufficient funds
    ("retry_later", "insufficient_funds"): 0.45,
    ("send_payment_link", "insufficient_funds"): 0.35,
    ("retry_immediately", "insufficient_funds"): 0.10,
    ("offer_alt_method", "insufficient_funds"): 0.30,
    ("escalate_human", "insufficient_funds"): 0.0,

    # Expired card
    ("send_payment_link", "expired_card"): 0.55,
    ("offer_alt_method", "expired_card"): 0.50,
    ("retry_later", "expired_card"): 0.05,
    ("retry_immediately", "expired_card"): 0.02,
    ("escalate_human", "expired_card"): 0.0,

    # OTP / auth issues
    ("retry_immediately", "otp_or_3ds_auth_issue"): 0.60,
    ("retry_immediately", "otp_entry_error"): 0.65,
    ("offer_alt_method", "otp_or_3ds_auth_issue"): 0.40,
    ("offer_alt_method", "otp_entry_error"): 0.40,
    ("send_payment_link", "otp_or_3ds_auth_issue"): 0.35,
    ("send_payment_link", "otp_entry_error"): 0.35,
    ("escalate_human", "otp_or_3ds_auth_issue"): 0.0,
    ("escalate_human", "otp_entry_error"): 0.0,

    # Bank-side outage / technical
    ("retry_later", "bank_side_outage"): 0.70,
    ("retry_later", "transient_technical_error"): 0.60,
    ("retry_later", "transient_network_timeout"): 0.65,
    ("retry_later", "bank_maintenance_window"): 0.65,
    ("offer_alt_method", "bank_side_outage"): 0.45,
    ("offer_alt_method", "transient_technical_error"): 0.40,
    ("retry_immediately", "transient_network_timeout"): 0.50,
    ("escalate_human", "bank_side_outage"): 0.0,
    ("escalate_human", "transient_technical_error"): 0.0,

    # Checkout abandoned
    ("send_payment_link", "checkout_abandoned"): 0.25,
    ("offer_alt_method", "checkout_abandoned"): 0.15,
    ("retry_immediately", "checkout_abandoned"): 0.05,
    ("escalate_human", "checkout_abandoned"): 0.0,

    # Overdue invoices
    ("send_payment_link", "recent_overdue"): 0.40,
    ("send_payment_link", "chronic_overdue"): 0.25,
    ("send_payment_link", "broken_payment_promise"): 0.10,
    ("retry_later", "recent_overdue"): 0.20,
    ("escalate_human", "recent_overdue"): 0.0,
    ("escalate_human", "chronic_overdue"): 0.0,
    ("escalate_human", "broken_payment_promise"): 0.0,

    # Promise-to-pay tracker actions
    # HAND-SET ASSUMPTIONS — documented in detector/promise_tracker.py
    ("send_reminder_gentle", "promise_due_soon"): 0.55,   # gentle nudge before due → high conversion
    ("send_reminder_firm",   "promise_due_soon"): 0.45,   # firm before due → slightly lower (may alienate)
    ("request_new_commitment", "promise_due_soon"): 0.35, # asking for re-commitment → moderate
    ("escalate_human",       "promise_due_soon"): 0.0,

    ("send_reminder_firm",     "broken_payment_promise"): 0.30,   # broken promise → firm reminder
    ("request_new_commitment", "broken_payment_promise"): 0.20,   # try to get a new date
    ("send_reminder_gentle",   "broken_payment_promise"): 0.15,   # gentle is too soft after a break
    ("escalate_human",         "broken_payment_promise"): 0.0,
}

# Default P_success if (action, root_cause) pair not in table
DEFAULT_P_SUCCESS: dict[str, float] = {
    "retry_immediately": 0.30,
    "retry_later": 0.35,
    "send_payment_link": 0.20,
    "offer_alt_method": 0.25,
    "escalate_human": 0.0,
}

# ── Cost per action (in Rs.) ──────────────────────────────────────────
# retry = free, SMS link = Rs.1, WhatsApp/alt method = Rs.0.50,
# escalate_human = Rs.50 (human time cost proxy)

ACTION_COST: dict[str, float] = {
    "retry_immediately": 0.0,
    "retry_later": 0.0,
    "send_payment_link": 1.0,
    "offer_alt_method": 0.50,
    "escalate_human": 50.0,
    # Promise-tracker actions — outreach costs similar to send_payment_link
    "send_reminder_gentle":    0.50,
    "send_reminder_firm":      0.50,
    "request_new_commitment":  1.50,   # slightly higher — manual follow-up expected
}

# Annoyance penalty: +Rs.200 if this would be 3rd+ contact today
ANNOYANCE_THRESHOLD = 3
ANNOYANCE_PENALTY = 200.0


def get_p_success(action: str, root_cause: str) -> float:
    """Get success probability for (action, root_cause) pair."""
    return P_SUCCESS.get(
        (action, root_cause),
        DEFAULT_P_SUCCESS.get(action, 0.1),
    )


def compute_ev(
    action: str,
    root_cause: str,
    amount_at_risk: float,
    customer_contacts_today: int = 0,
) -> dict:
    """
    Compute Expected Value of an action.

    EV = P_success * amount_at_risk - cost - annoyance_penalty

    Returns dict with full breakdown for audit trail.
    """
    p_success = get_p_success(action, root_cause)
    cost = ACTION_COST.get(action, 0.0)
    annoyance = ANNOYANCE_PENALTY if customer_contacts_today >= ANNOYANCE_THRESHOLD else 0.0

    ev = p_success * amount_at_risk - cost - annoyance

    return {
        "action": action,
        "p_success": p_success,
        "amount_at_risk": amount_at_risk,
        "expected_recovery": round(p_success * amount_at_risk, 2),
        "cost": cost,
        "annoyance_penalty": annoyance,
        "customer_contacts_today": customer_contacts_today,
        "ev": round(ev, 2),
    }


def score_candidates(
    candidates: list[str],
    root_cause: str,
    amount_at_risk: float,
    customer_contacts_today: int = 0,
) -> list[dict]:
    """
    Score all candidate actions and return sorted by EV descending.
    """
    scored = [
        compute_ev(action, root_cause, amount_at_risk, customer_contacts_today)
        for action in candidates
    ]
    scored.sort(key=lambda x: x["ev"], reverse=True)
    return scored
