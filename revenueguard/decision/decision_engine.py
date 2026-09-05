"""
Decision Engine: picks the highest-EV action for each diagnosed event.
Logs the full scoring breakdown for every candidate.
"""

from __future__ import annotations

from .candidates import generate_candidates
from .scoring import score_candidates


# Decision output type
DecidedEvent = dict  # extends DiagnosedEvent with decision info


def decide_event(diagnosed_event: dict, customer_contacts_today: int = 0) -> DecidedEvent:
    """
    Pick the highest-EV action for a single diagnosed event.
    """
    event = diagnosed_event["event"]
    diagnosis = diagnosed_event["diagnosis"]

    root_cause = diagnosis["root_cause"]
    amount = event.amount_at_risk

    # Generate candidates
    candidates = generate_candidates(diagnosis, event.event_type)

    # Score all candidates
    scored = score_candidates(
        candidates, root_cause, amount, customer_contacts_today
    )

    # Pick the highest EV (first after sort)
    chosen = scored[0]

    return {
        **diagnosed_event,
        "decision": {
            "candidates_scored": scored,
            "chosen_action": chosen["action"],
            "chosen_ev": chosen["ev"],
            "chosen_p_success": chosen["p_success"],
        },
    }


def run_decisions(diagnosed_events: list[dict]) -> list[DecidedEvent]:
    """
    Run decision engine on all diagnosed events.
    """
    # Track contacts per customer per day (simplified: per pipeline run)
    customer_contacts: dict[str, int] = {}
    decided: list[DecidedEvent] = []

    for d in diagnosed_events:
        cust_id = d["event"].customer_id
        contacts = customer_contacts.get(cust_id, 0)

        result = decide_event(d, contacts)
        decided.append(result)

        # Increment contact count if action involves customer contact
        if result["decision"]["chosen_action"] in ("send_payment_link", "offer_alt_method"):
            customer_contacts[cust_id] = contacts + 1

    # Summary
    from collections import Counter
    action_counts = Counter(d["decision"]["chosen_action"] for d in decided)
    total_expected = sum(d["decision"]["chosen_ev"] for d in decided)

    print(f"\n[Decision] Decided actions for {len(decided)} events")
    print(f"  Action distribution:")
    for action, count in action_counts.most_common():
        print(f"    {action:<25} {count:>3}")
    print(f"  Total expected value: Rs.{total_expected:,.2f}")

    return decided
