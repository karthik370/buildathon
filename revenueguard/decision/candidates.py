"""
Candidate action generator: given a diagnosis, generate 2-4 candidate actions.
"""

from __future__ import annotations

# Fixed action enum
ACTIONS = [
    "retry_immediately",
    "retry_later",
    "send_payment_link",
    "offer_alt_method",
    "escalate_human",
    # Promise-tracker-specific actions
    "send_reminder_gentle",
    "send_reminder_firm",
    "request_new_commitment",
]


def generate_candidates(diagnosis: dict, event_type: str) -> list[str]:
    """
    Generate 2-4 candidate actions based on the diagnosis.

    The recommended action is always included, plus relevant alternatives.
    """
    recommended = diagnosis.get("recommended_action", "escalate_human")
    root_cause = diagnosis.get("root_cause", "")

    candidates = set()
    candidates.add(recommended)

    # Always include escalate_human as a safe fallback
    candidates.add("escalate_human")

    # Add contextual alternatives
    if root_cause in ("insufficient_funds", "recent_overdue"):
        candidates.add("retry_later")
        candidates.add("send_payment_link")

    elif root_cause in ("expired_card", "checkout_abandoned"):
        candidates.add("send_payment_link")
        candidates.add("offer_alt_method")

    elif root_cause in ("otp_or_3ds_auth_issue", "otp_entry_error"):
        candidates.add("retry_immediately")
        candidates.add("offer_alt_method")

    elif root_cause in ("bank_side_outage", "transient_technical_error",
                        "transient_network_timeout", "bank_maintenance_window"):
        candidates.add("retry_later")
        candidates.add("offer_alt_method")

    elif root_cause in ("chronic_overdue", "broken_payment_promise"):
        candidates.add("send_payment_link")
        candidates.add("escalate_human")

    elif root_cause == "promise_due_soon":
        # Approaching promise date — gentle reminder is the first-line action
        candidates.add("send_reminder_gentle")
        candidates.add("send_reminder_firm")
        candidates.add("request_new_commitment")

    elif root_cause == "broken_payment_promise":
        # Already broken a promise — firm reminder or escalate
        candidates.add("send_reminder_firm")
        candidates.add("request_new_commitment")
        candidates.add("escalate_human")

    else:
        # Generic: add retry and link
        candidates.add("retry_later")
        candidates.add("send_payment_link")

    # Cap at 4 candidates
    result = list(candidates)[:4]

    # Ensure recommended is first
    if recommended in result:
        result.remove(recommended)
        result.insert(0, recommended)

    return result
