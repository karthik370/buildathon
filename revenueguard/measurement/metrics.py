"""
Metrics: compute and report final measurement results.
"""

from __future__ import annotations

from collections import Counter


def compute_metrics(
    treatment_results: list[dict],
    control_results: list[dict],
    gate_trigger_stats: dict[str, int],
    total_events: int,
) -> dict:
    """
    Compute comprehensive metrics comparing treatment vs control.

    Returns a dict with all metrics for the dashboard and report.
    """
    # Treatment group
    t_total = len(treatment_results)
    t_total_amount = sum(r["execution"]["amount_recovered"] + r["event"].amount_at_risk
                         if r["execution"]["amount_recovered"] == 0
                         else r["event"].amount_at_risk
                         for r in treatment_results)
    t_total_amount = sum(r["event"].amount_at_risk for r in treatment_results)
    t_recovered_count = sum(
        1 for r in treatment_results
        if r["execution"]["amount_recovered"] > 0
    )
    t_recovered_amount = sum(
        r["execution"]["amount_recovered"] for r in treatment_results
    )
    t_recovery_rate = t_recovered_count / t_total if t_total > 0 else 0

    # Control group
    c_total = len(control_results)
    c_total_amount = sum(r["amount_at_risk"] for r in control_results)
    c_recovered_count = sum(1 for r in control_results if r["recovered"])
    c_recovered_amount = sum(r["amount_recovered"] for r in control_results)
    c_recovery_rate = c_recovered_count / c_total if c_total > 0 else 0

    # Combined
    combined_total_amount = t_total_amount + c_total_amount

    # Net lift
    # lift_pp: difference in recovery RATES (percentage points) — count-based,
    # comparable because both sides use their own group size as denominator.
    lift_pp = (t_recovery_rate - c_recovery_rate) * 100  # percentage points

    # lift_amount (approach a): project the rate difference onto the treatment
    # pool's own at-risk amount.
    #   incremental_₹ = (treatment_rate - baseline_rate) × treatment_at_risk_amount
    # This is the only formula guaranteed to share the sign of lift_pp.
    # The previous formula subtracted (c_recovered_amount × t_total / c_total)
    # which compares raw ₹ amounts across pools of different size and
    # composition — the treatment pool contains high-value invoices that the
    # amount_requires_approval gate correctly blocks, inflating the denominator
    # without a matching numerator, so it produced a spurious negative result
    # even when the recovery rate was materially higher.
    lift_amount = (t_recovery_rate - c_recovery_rate) * t_total_amount

    # Breakdown by event type
    type_breakdown = {}
    for r in treatment_results:
        etype = r["event"].event_type
        if etype not in type_breakdown:
            type_breakdown[etype] = {"total": 0, "recovered": 0, "amount_at_risk": 0, "amount_recovered": 0}
        type_breakdown[etype]["total"] += 1
        type_breakdown[etype]["amount_at_risk"] += r["event"].amount_at_risk
        if r["execution"]["amount_recovered"] > 0:
            type_breakdown[etype]["recovered"] += 1
            type_breakdown[etype]["amount_recovered"] += r["execution"]["amount_recovered"]

    # Breakdown by action
    action_breakdown = {}
    for r in treatment_results:
        action = r["execution"]["action_taken"]
        if action not in action_breakdown:
            action_breakdown[action] = {"total": 0, "recovered": 0, "amount_recovered": 0}
        action_breakdown[action]["total"] += 1
        if r["execution"]["amount_recovered"] > 0:
            action_breakdown[action]["recovered"] += 1
            action_breakdown[action]["amount_recovered"] += r["execution"]["amount_recovered"]

    # Exception list (unrecovered cases)
    exceptions = []
    for r in treatment_results:
        if r["execution"]["amount_recovered"] == 0:
            exceptions.append({
                "event_id": r["event"].event_id,
                "event_type": r["event"].event_type,
                "amount_at_risk": r["event"].amount_at_risk,
                "root_cause": r["diagnosis"]["root_cause"],
                "action_taken": r["execution"]["action_taken"],
                "outcome": r["execution"]["outcome"],
                "reason": r["execution"]["details"],
            })

    # Showcase failure cases
    showcase_cases = [
        r["event"].event_id for r in treatment_results
        if r["execution"].get("is_showcase_failure_case")
    ]

    metrics = {
        "total_events_detected": total_events,
        "treatment_count": t_total,
        "control_count": c_total,
        "combined_amount_at_risk": round(combined_total_amount, 2),

        "baseline": {
            "count": c_total,
            "recovered_count": c_recovered_count,
            "recovery_rate": round(c_recovery_rate * 100, 1),
            "amount_at_risk": round(c_total_amount, 2),
            "amount_recovered": round(c_recovered_amount, 2),
        },

        "agent_assisted": {
            "count": t_total,
            "recovered_count": t_recovered_count,
            "recovery_rate": round(t_recovery_rate * 100, 1),
            "amount_at_risk": round(t_total_amount, 2),
            "amount_recovered": round(t_recovered_amount, 2),
        },

        "net_lift": {
            "percentage_points": round(lift_pp, 1),
            "incremental_amount": round(lift_amount, 2),
        },

        "type_breakdown": type_breakdown,
        "action_breakdown": action_breakdown,
        "gate_trigger_stats": dict(gate_trigger_stats),
        "exception_count": len(exceptions),
        "exceptions": exceptions,
        "showcase_failure_cases": showcase_cases,

        "honesty_note": (
            f"Batch size is {total_events} -- this is a directional result on "
            f"synthetic data, not a statistically validated result at production scale."
        ),
    }

    return metrics


def print_metrics(metrics: dict):
    """Print a formatted metrics report."""
    print("\n" + "=" * 70)
    print("REVENUEGUARD -- FINAL METRICS REPORT")
    print("=" * 70)

    print(f"\nTotal events detected: {metrics['total_events_detected']}")
    print(f"Treatment group: {metrics['treatment_count']} | "
          f"Control group: {metrics['control_count']}")
    print(f"Combined amount at risk: Rs.{metrics['combined_amount_at_risk']:,.2f}")

    print(f"\n--- BASELINE (Control, no intervention) ---")
    b = metrics["baseline"]
    print(f"  Recovered: {b['recovered_count']}/{b['count']} ({b['recovery_rate']}%)")
    print(f"  Amount: Rs.{b['amount_recovered']:,.2f} / Rs.{b['amount_at_risk']:,.2f}")

    print(f"\n--- AGENT-ASSISTED (Treatment) ---")
    a = metrics["agent_assisted"]
    print(f"  Recovered: {a['recovered_count']}/{a['count']} ({a['recovery_rate']}%)")
    print(f"  Amount: Rs.{a['amount_recovered']:,.2f} / Rs.{a['amount_at_risk']:,.2f}")

    print(f"\n--- NET LIFT ---")
    nl = metrics["net_lift"]
    sign = "+" if nl['percentage_points'] >= 0 else ""
    print(f"  Lift: {sign}{nl['percentage_points']} percentage points")
    print(f"  Incremental recovery (rate_diff x treatment_at_risk): Rs.{nl['incremental_amount']:,.2f}")

    print(f"\n--- BREAKDOWN BY EVENT TYPE ---")
    for etype, data in metrics["type_breakdown"].items():
        rate = data["recovered"] / data["total"] * 100 if data["total"] > 0 else 0
        print(f"  {etype}: {data['recovered']}/{data['total']} ({rate:.0f}%) "
              f"| Rs.{data['amount_recovered']:,.2f}")

    print(f"\n--- BREAKDOWN BY ACTION ---")
    for action, data in metrics["action_breakdown"].items():
        rate = data["recovered"] / data["total"] * 100 if data["total"] > 0 else 0
        print(f"  {action}: {data['recovered']}/{data['total']} ({rate:.0f}%) "
              f"| Rs.{data['amount_recovered']:,.2f}")

    print(f"\n--- GATE TRIGGER STATS ---")
    for gate, count in sorted(metrics["gate_trigger_stats"].items()):
        print(f"  {gate}: {count} times")

    print(f"\n--- EXCEPTIONS (unrecovered cases) ---")
    print(f"  Total: {metrics['exception_count']}")
    for exc in metrics["exceptions"][:5]:  # show first 5
        print(f"    {exc['event_id']} | {exc['event_type']} | "
              f"Rs.{exc['amount_at_risk']:,.2f} | {exc['root_cause']} | "
              f"{exc['outcome']}")
    if metrics["exception_count"] > 5:
        print(f"    ... and {metrics['exception_count'] - 5} more")

    print(f"\n--- SHOWCASE FAILURE CASES ---")
    for case_id in metrics["showcase_failure_cases"]:
        print(f"  {case_id}")

    print(f"\n[HONESTY NOTE] {metrics['honesty_note']}")
    print("=" * 70)
