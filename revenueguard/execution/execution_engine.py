"""
Execution Engine: run all gates, then execute or escalate.

Includes showcase failure case logic (Step 9):
- If the first action fails, attempt a fallback
- If fallback also fails, escalate to human and flag as showcase case
"""

from __future__ import annotations

from datetime import datetime, timedelta
from collections import Counter

from .gates import (
    check_max_retries,
    check_daily_contact_cap,
    check_amount_requires_approval,
    check_disputed_or_fraud_flag,
    check_cooldown,
    check_stale,
)
from .executor_factory import get_executor


def execute_action(
    entity_id: str,
    action_type: str,
    root_cause: str,
    amount_at_risk: float,
    attempt: int = 1,
) -> dict:
    """Shim: read EXECUTOR_MODE fresh on every call and delegate to the right executor."""
    executor = get_executor()   # reads os.environ["EXECUTOR_MODE"] each time — no stale singleton
    result = executor.execute(entity_id, action_type, root_cause, amount_at_risk, attempt)
    return result.to_dict()


from .channel_selector import (
    select_channel,
    channel_selection_reason,
    record_channel_outcome,
    reset_channel_log,
    flush_to_db,
)

# Track gate trigger stats globally
gate_trigger_stats: dict[str, int] = Counter()
customer_contacts: dict[str, int] = Counter()


def run_gates(decided_event: dict) -> dict:
    """
    Run all applicable gates on a decided event.

    Returns:
        dict with {passed: bool, gates_applied: list, blocked_by: str|None}
    """
    event = decided_event["event"]
    ctx = event.context
    action = decided_event["decision"]["chosen_action"]

    gates_applied = []
    blocked_by = None

    # Gate 1: Max retries (only for retry actions on payment failures)
    if action in ("retry_immediately", "retry_later") and event.event_type == "HARD_PAYMENT_FAILURE":
        retry_count = ctx.get("retry_count", 0)
        allowed = check_max_retries(retry_count)
        gates_applied.append({
            "gate": "max_retries",
            "retry_count": retry_count,
            "allowed": allowed,
        })
        if not allowed:
            blocked_by = "max_retries"
            gate_trigger_stats["max_retries"] += 1

    # Gate 2: Disputed / fraud flag -- HIGHEST SAFETY PRIORITY
    # Must run before other gates so "disputed_or_fraud" is the reported blocker
    if event.event_type == "HARD_PAYMENT_FAILURE":
        is_disputed = ctx.get("is_disputed", False)
        is_blocked = check_disputed_or_fraud_flag(is_disputed)
        gates_applied.append({
            "gate": "disputed_or_fraud",
            "is_disputed": is_disputed,
            "blocked": is_blocked,
        })
        if is_blocked and not blocked_by:
            blocked_by = "disputed_or_fraud"
            gate_trigger_stats["disputed_or_fraud"] += 1

    # Gate 3: Daily contact cap
    if action in ("send_payment_link", "offer_alt_method"):
        contacts = customer_contacts.get(event.customer_id, 0)
        allowed = check_daily_contact_cap(contacts)
        gates_applied.append({
            "gate": "daily_contact_cap",
            "contacts_today": contacts,
            "allowed": allowed,
        })
        if not allowed and not blocked_by:
            blocked_by = "daily_contact_cap"
            gate_trigger_stats["daily_contact_cap"] += 1

    # Gate 4: Amount requires approval (for auto-actions, not escalation)
    if action != "escalate_human":
        needs_approval = check_amount_requires_approval(event.amount_at_risk)
        gates_applied.append({
            "gate": "amount_requires_approval",
            "amount": event.amount_at_risk,
            "needs_approval": needs_approval,
        })
        if needs_approval and not blocked_by:
            blocked_by = "amount_requires_approval"
            gate_trigger_stats["amount_requires_approval"] += 1

    # Gate 5: Cooldown (check if enough time since last attempt)
    # Uses last_contact_at if present (edge-case rows), otherwise falls back
    # to the transaction timestamp.
    if event.event_type == "HARD_PAYMENT_FAILURE" and action in ("retry_immediately", "retry_later"):
        ts_str = ctx.get("last_contact_at") or ctx.get("timestamp", "")
        if ts_str:
            last_attempt = datetime.fromisoformat(ts_str)
            now = datetime.now()
            cooldown_ok = check_cooldown(last_attempt, now)
            gates_applied.append({
                "gate": "cooldown",
                "last_attempt": ts_str,
                "cooldown_ok": cooldown_ok,
            })
            if not cooldown_ok and not blocked_by:
                blocked_by = "cooldown_active"
                gate_trigger_stats["cooldown_active"] += 1

    # Gate 6: Stale check (don't chase very old cases)
    created_str = ctx.get("created_at", "")
    if created_str:
        try:
            created = datetime.fromisoformat(created_str)
            now = datetime.now()
            is_stale = check_stale(created, now)
            gates_applied.append({
                "gate": "stale_check",
                "created_at": created_str,
                "is_stale": is_stale,
            })
            if is_stale and not blocked_by:
                blocked_by = "stale_check"
                gate_trigger_stats["stale_check"] += 1
        except (ValueError, TypeError):
            pass

    passed = blocked_by is None

    return {
        "passed": passed,
        "gates_applied": gates_applied,
        "blocked_by": blocked_by,
    }


# ── Fallback actions for showcase failure case (Step 9) ───────────────
FALLBACK_MAP = {
    "retry_immediately": "send_payment_link",
    "retry_later": "offer_alt_method",
    "send_payment_link": "offer_alt_method",
    "offer_alt_method": "retry_later",
}


def execute_decided_event(decided_event: dict) -> dict:
    """
    Execute a decided event: run gates, then execute or escalate.

    Includes showcase failure case handling:
    - If first action fails, try a fallback
    - If fallback also fails, escalate and flag as showcase
    """
    event = decided_event["event"]
    decision = decided_event["decision"]
    diagnosis = decided_event["diagnosis"]

    action = decision["chosen_action"]
    root_cause = diagnosis["root_cause"]

    # Run gates
    gate_result = run_gates(decided_event)

    if not gate_result["passed"]:
        blocked_by = gate_result["blocked_by"]

        if blocked_by == "amount_requires_approval":
            # HIGH-VALUE CASE: Do NOT call the Razorpay API automatically.
            # Queue for human review. The real API call (send_payment_link /
            # offer_alt_method) fires ONLY when a human clicks Approve in the
            # dashboard. This prevents burning API quota on unsupervised actions
            # and gives a clean demo: Approve → real plink_* appears in Razorpay.
            return {
                **decided_event,
                "gate_check": gate_result,
                "execution": {
                    "action_taken": "pending_approval",
                    "original_intended_action": action,
                    "outcome": "pending_approval",
                    "amount_recovered": 0.0,
                    "details": (
                        f"Queued for human approval. Gate 'amount_requires_approval' "
                        f"requires a human to review before '{action}' is executed. "
                        f"Clicking Approve in the dashboard will trigger the real "
                        f"Razorpay API call for the first time."
                    ),
                    "is_showcase_failure_case": False,
                },
            }

        # All other gate failures (fraud, retry limit, cooldown, stale) →
        # escalate to human immediately with no further action.
        exec_result = execute_action(
            event.entity_id, "escalate_human", root_cause, event.amount_at_risk
        )
        return {
            **decided_event,
            "gate_check": gate_result,
            "execution": {
                "action_taken": "escalate_human",
                "original_action": action,
                "outcome": exec_result["outcome"],
                "amount_recovered": exec_result["amount_recovered"],
                "details": f"Gate '{blocked_by}' blocked '{action}'. Auto-escalated to human.",
                "is_showcase_failure_case": False,
            },
        }

    # Track contacts
    if action in ("send_payment_link", "offer_alt_method"):
        customer_contacts[event.customer_id] += 1

    # Select outreach channel (for outreach-type actions; 'n/a' for retries/escalations)
    channel = select_channel(event.customer_id, action)
    channel_reason = channel_selection_reason(event.customer_id, channel)

    # Execute primary action
    exec_result = execute_action(
        event.entity_id, action, root_cause, event.amount_at_risk, attempt=1
    )

    # Record channel outcome for preference learning
    if channel != "n/a":
        record_channel_outcome(
            event.customer_id, channel,
            recovered=exec_result["amount_recovered"] > 0
        )

    if exec_result["success"] and exec_result["outcome"] != "escalated_to_human":
        return {
            **decided_event,
            "gate_check": gate_result,
            "execution": {
                "action_taken": action,
                "outcome": exec_result["outcome"],
                "amount_recovered": exec_result["amount_recovered"],
                "details": exec_result["details"],
                "channel": channel,
                "channel_reason": channel_reason,
                "is_showcase_failure_case": False,
            },
        }

    # Primary action failed - try fallback (Step 9 showcase)
    fallback_action = FALLBACK_MAP.get(action, "escalate_human")
    if fallback_action == action:
        fallback_action = "escalate_human"

    fallback_result = execute_action(
        event.entity_id, fallback_action, root_cause, event.amount_at_risk, attempt=2
    )

    if fallback_result["success"] and fallback_result["outcome"] != "escalated_to_human":
        return {
            **decided_event,
            "gate_check": gate_result,
            "execution": {
                "action_taken": fallback_action,
                "outcome": fallback_result["outcome"],
                "amount_recovered": fallback_result["amount_recovered"],
                "details": f"Primary '{action}' failed. Fallback '{fallback_action}' succeeded.",
                "is_showcase_failure_case": False,
                "primary_action_failed": action,
            },
        }

    # Both actions failed - escalate and flag as showcase failure case
    final_result = execute_action(
        event.entity_id, "escalate_human", root_cause, event.amount_at_risk, attempt=3
    )

    return {
        **decided_event,
        "gate_check": gate_result,
        "execution": {
            "action_taken": "escalate_human",
            "outcome": "escalated_after_failures",
            "amount_recovered": 0.0,
            "details": (
                f"Primary '{action}' failed (attempt 1). "
                f"Fallback '{fallback_action}' also failed (attempt 2). "
                f"Correctly stopped retrying and escalated to human agent."
            ),
            "is_showcase_failure_case": True,
            "primary_action_failed": action,
            "fallback_action_failed": fallback_action,
        },
    }


def run_execution(decided_events: list[dict]) -> list[dict]:
    """
    Execute all decided events through gates and executors.
    """
    global gate_trigger_stats, customer_contacts
    gate_trigger_stats = Counter()
    customer_contacts = Counter()
    reset_channel_log()

    executed: list[dict] = []
    showcase_cases: list[str] = []

    for d in decided_events:
        result = execute_decided_event(d)
        executed.append(result)
        if result["execution"].get("is_showcase_failure_case"):
            showcase_cases.append(result["event"].event_id)

    # If no natural showcase case emerged, force one
    if not showcase_cases:
        showcase_cases = _force_showcase_case(executed)

    # Summary
    outcomes = Counter(e["execution"]["outcome"] for e in executed)
    total_recovered = sum(e["execution"]["amount_recovered"] for e in executed)
    gate_blocked = sum(1 for e in executed if not e["gate_check"]["passed"])

    print(f"\n[Execution] Executed {len(executed)} events")
    print(f"  Outcomes:")
    for outcome, count in outcomes.most_common():
        print(f"    {outcome:<30} {count:>3}")
    print(f"  Total recovered: Rs.{total_recovered:,.2f}")
    print(f"  Gate-blocked: {gate_blocked}")
    print(f"  Showcase failure cases: {len(showcase_cases)}")
    if showcase_cases:
        print(f"    IDs: {', '.join(showcase_cases)}")

    print(f"\n  Gate trigger stats:")
    for gate, count in sorted(gate_trigger_stats.items()):
        print(f"    {gate}: {count} times")

    # Persist channel outcomes to SQLite so per-customer preferences
    # survive across pipeline runs (loaded back via reset_channel_log / load_from_db)
    flush_to_db()

    return executed


def _force_showcase_case(executed: list[dict]) -> list[str]:
    """
    If no natural showcase failure case emerged from the random simulation,
    pick one failed case and flag it as the showcase.
    """
    for e in executed:
        if (e["execution"]["outcome"] == "failed" or
            (e["execution"]["outcome"] == "escalated_to_human" and
             e["execution"].get("primary_action_failed"))):
            # This is already close to a showcase - just flag it
            e["execution"]["is_showcase_failure_case"] = True
            return [e["event"].event_id]

    # If still nothing, pick any escalation
    for e in executed:
        if not e["gate_check"]["passed"]:
            e["execution"]["is_showcase_failure_case"] = True
            e["execution"]["details"] += " [SHOWCASE: Gate-blocked action demonstrates bounded execution.]"
            return [e["event"].event_id]

    return []
