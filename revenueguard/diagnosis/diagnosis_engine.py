"""
Diagnosis Engine: orchestrates Tier-1 (deterministic) -> Tier-2 (LLM) fallback.

Hard rule: if confidence < 0.5 for any case, override to escalate_human.
"""

from __future__ import annotations

from .context_assembly import build_case_file
from .deterministic_map import diagnose_tier1, is_ambiguous
from .llm_diagnosis import diagnose_tier2
from ..detector.ingestion import AtRiskEvent


# Diagnosis output attached to each event
DiagnosedEvent = dict  # {event: AtRiskEvent, case_file: dict, diagnosis: dict, tier: str}


def diagnose_event(
    event: AtRiskEvent,
    customers: dict[str, dict],
    transactions: list[dict],
    anomalies: list[dict],
) -> DiagnosedEvent:
    """
    Diagnose a single event through Tier-1 -> Tier-2 fallback.
    """
    case_file = build_case_file(event, customers, transactions, anomalies)
    failure_code = event.context.get("failure_code", "")

    tier = "tier1"
    diagnosis = None

    # For events without a failure_code (checkouts, invoices), use event-type defaults
    if event.event_type == "CHECKOUT_ABANDONMENT":
        diagnosis = {
            "root_cause": "checkout_abandoned",
            "confidence": 0.85,
            "reasoning": "Customer abandoned checkout before completing payment. Cart recovery action recommended.",
            "recommended_action": "send_payment_link",
            "recommended_delay_minutes": 30,
        }
        tier = "default"
    elif event.event_type == "OVERDUE_RECEIVABLE":
        days_overdue = event.context.get("days_overdue", 0)
        promise_status = event.context.get("promise_to_pay_status", "")

        if promise_status == "broken":
            diagnosis = {
                "root_cause": "broken_payment_promise",
                "confidence": 0.95,
                "reasoning": f"Invoice is {days_overdue} days overdue with a broken promise-to-pay. Escalation recommended.",
                "recommended_action": "escalate_human",
                "recommended_delay_minutes": 0,
            }
        elif days_overdue > 30:
            diagnosis = {
                "root_cause": "chronic_overdue",
                "confidence": 0.90,
                "reasoning": f"Invoice is {days_overdue} days overdue. Payment reminder with link recommended.",
                "recommended_action": "send_payment_link",
                "recommended_delay_minutes": 0,
            }
        else:
            diagnosis = {
                "root_cause": "recent_overdue",
                "confidence": 0.80,
                "reasoning": f"Invoice is {days_overdue} days overdue. Gentle reminder appropriate.",
                "recommended_action": "send_payment_link",
                "recommended_delay_minutes": 60,
            }
        tier = "default"
    elif event.event_type == "PROMISE_BROKEN":
        days_late = event.context.get("days_promise_overdue", 0)
        p2p_date = event.context.get("promise_to_pay_date", "")
        diagnosis = {
            "root_cause": "broken_payment_promise",
            "confidence": 0.95,
            "reasoning": (
                f"Customer committed to pay by {p2p_date} but the promise was "
                f"broken ({days_late} days late). Firm reminder or human escalation required."
            ),
            "recommended_action": "send_reminder_firm",
            "recommended_delay_minutes": 0,
        }
        tier = "tier1"

    elif event.event_type == "PROMISE_DUE_SOON":
        hours_until = event.context.get("hours_until_promise_due", 0)
        p2p_date = event.context.get("promise_to_pay_date", "")
        # Check if this customer has a history of broken promises (needs LLM tone judgment)
        # Simple heuristic: look at all transactions for this customer
        customer_broken_promises = sum(
            1 for t in transactions
            if t.get("customer_id") == event.customer_id
        )  # placeholder — real check would scan invoice history
        diagnosis = {
            "root_cause": "promise_due_soon",
            "confidence": 0.80,
            "reasoning": (
                f"Customer promised to pay by {p2p_date} — "
                f"{hours_until:.1f}h remaining. Gentle reminder recommended "
                f"to confirm intent and avoid the promise becoming broken."
            ),
            "recommended_action": "send_reminder_gentle",
            "recommended_delay_minutes": 0,
        }
        tier = "tier1"

    elif failure_code:
        # Try Tier 1 deterministic
        diagnosis = diagnose_tier1(failure_code)
        if diagnosis:
            tier = "tier1"
        elif is_ambiguous(failure_code):
            # Route to Tier 2
            diagnosis = diagnose_tier2(case_file)
            tier = "tier2"

    # Final fallback
    if diagnosis is None:
        diagnosis = {
            "root_cause": "unknown",
            "confidence": 0.3,
            "reasoning": "Could not determine root cause through any tier.",
            "recommended_action": "escalate_human",
            "recommended_delay_minutes": 0,
        }
        tier = "fallback"

    # ── HARD RULE: confidence < 0.5 -> escalate_human ──
    if diagnosis["confidence"] < 0.5:
        diagnosis["recommended_action"] = "escalate_human"
        diagnosis["reasoning"] += " [OVERRIDE: confidence < 0.5, forcing escalate_human]"

    return {
        "event": event,
        "case_file": case_file,
        "diagnosis": diagnosis,
        "tier": tier,
    }


def run_diagnosis(
    events: list[AtRiskEvent],
    customers: dict[str, dict],
    transactions: list[dict],
    anomalies: list[dict],
) -> list[DiagnosedEvent]:
    """
    Run diagnosis on all detected events.
    """
    diagnosed: list[DiagnosedEvent] = []

    tier_counts = {"tier1": 0, "tier2": 0, "default": 0, "fallback": 0}
    escalated_count = 0

    for event in events:
        result = diagnose_event(event, customers, transactions, anomalies)
        diagnosed.append(result)
        tier_counts[result["tier"]] += 1
        if result["diagnosis"]["recommended_action"] == "escalate_human":
            escalated_count += 1

    print(f"\n[Diagnosis] Diagnosed {len(diagnosed)} events")
    print(f"  Tier 1 (deterministic): {tier_counts['tier1']}")
    print(f"  Tier 2 (LLM):          {tier_counts['tier2']}")
    print(f"  Default (type-based):   {tier_counts['default']}")
    print(f"  Fallback:               {tier_counts['fallback']}")
    print(f"  Escalated to human:     {escalated_count}")

    # Print sample of 5 diagnoses
    print(f"\n[Diagnosis] Sample diagnoses (first 5):")
    for d in diagnosed[:5]:
        evt = d["event"]
        diag = d["diagnosis"]
        print(f"\n  --- {evt.event_id} ({evt.event_type}) ---")
        print(f"  Amount: Rs.{evt.amount_at_risk:,.2f}")
        print(f"  Tier: {d['tier']}")
        print(f"  Root cause: {diag['root_cause']}")
        print(f"  Confidence: {diag['confidence']}")
        print(f"  Action: {diag['recommended_action']}")
        print(f"  Reasoning: {diag['reasoning'][:120]}...")

    return diagnosed
