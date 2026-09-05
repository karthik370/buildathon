"""
Holdout: split detected events into treatment (70%) and control (30%) groups.
"""

from __future__ import annotations

import random
from ..detector.ingestion import AtRiskEvent

HOLDOUT_SEED = 42


def split_holdout(
    events: list[AtRiskEvent],
    treatment_ratio: float = 0.70,
    seed: int = HOLDOUT_SEED,
) -> tuple[list[AtRiskEvent], list[AtRiskEvent]]:
    """
    Split events into treatment and control groups.

    Args:
        events: All detected events
        treatment_ratio: Fraction assigned to treatment (default 0.70)
        seed: Random seed for reproducibility (default 42)

    Returns:
        (treatment_events, control_events)
    """
    # Edge-case events (IDs prefixed with 'edge_') are always routed to
    # treatment so gate demonstrations are guaranteed to execute.
    edge_cases = [e for e in events if "edge_" in e.event_id]
    regular = [e for e in events if "edge_" not in e.event_id]

    rng = random.Random(seed)
    shuffled = list(regular)
    rng.shuffle(shuffled)

    split_idx = int(len(shuffled) * treatment_ratio)
    treatment = shuffled[:split_idx] + edge_cases
    control = shuffled[split_idx:]

    print(f"\n[Holdout] Split {len(events)} events:")
    print(f"  Treatment: {len(treatment)} ({len(treatment)/len(events)*100:.0f}%)")
    print(f"  Control:   {len(control)} ({len(control)/len(events)*100:.0f}%)")
    print(f"  Edge cases in treatment: {len(edge_cases)} (guaranteed)")
    print(f"  Seed: {seed}")

    return treatment, control


def simulate_baseline_recovery(
    control_events: list[AtRiskEvent],
    seed: int = HOLDOUT_SEED + 1,
) -> list[dict]:
    """
    Simulate natural (no-intervention) recovery for the control group.

    Uses a low fixed probability per event type:
      - HARD_PAYMENT_FAILURE: 12% (some customers retry on their own)
      - CHECKOUT_ABANDONMENT: 10% (some come back naturally)
      - SILENT_RENEWAL_FAILURE: 8% (rare without nudge)
      - OVERDUE_RECEIVABLE: 15% (some pay late without reminder)
    """
    NATURAL_RECOVERY_RATES = {
        "HARD_PAYMENT_FAILURE": 0.12,
        "CHECKOUT_ABANDONMENT": 0.10,
        "SILENT_RENEWAL_FAILURE": 0.08,
        "OVERDUE_RECEIVABLE": 0.15,
    }

    rng = random.Random(seed)
    results = []

    for event in control_events:
        rate = NATURAL_RECOVERY_RATES.get(event.event_type, 0.10)
        recovered = rng.random() < rate
        results.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "entity_id": event.entity_id,
            "customer_id": event.customer_id,
            "amount_at_risk": event.amount_at_risk,
            "recovered": recovered,
            "amount_recovered": event.amount_at_risk if recovered else 0.0,
            "group": "control",
        })

    recovered_count = sum(1 for r in results if r["recovered"])
    recovered_amount = sum(r["amount_recovered"] for r in results)
    total_amount = sum(r["amount_at_risk"] for r in results)

    print(f"\n[Baseline] Simulated natural recovery for control group:")
    print(f"  Recovered: {recovered_count}/{len(results)} "
          f"({recovered_count/len(results)*100:.1f}%)")
    print(f"  Amount recovered: Rs.{recovered_amount:,.2f} / Rs.{total_amount:,.2f}")

    return results
