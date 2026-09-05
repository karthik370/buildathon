"""
Detector orchestrator: ingestion -> rules -> anomaly -> triage -> suppression.
"""

from __future__ import annotations

from collections import Counter

from .ingestion import AtRiskEvent, ingest_all
from .rules import apply_all_rules
from .anomaly import detect_anomalous_windows, compute_method_baseline_rates
from .triage import compute_priority
from .suppression import dedupe_events


def run_detector() -> tuple[list[AtRiskEvent], list[dict], dict[str, dict], list[dict]]:
    """
    Run the full detection pipeline.

    Returns:
        (scored_events, anomaly_flags, customers_lookup, transactions)
    """
    # 1. Ingest
    raw_events, customers, transactions = ingest_all()
    print(f"\n[Detector] Ingested {len(raw_events)} raw events")

    # 2. Apply rules / score
    scored = apply_all_rules(raw_events, transactions)
    print(f"[Detector] {len(scored)} events fired at least one rule")

    # 3. Anomaly detection
    anomalies = detect_anomalous_windows(transactions)
    if anomalies:
        print(f"[Detector] ANOMALY DETECTED: {len(anomalies)} method/window spikes found:")
        for a in anomalies:
            print(f"  - {a['method']} | {a['window_start']} to {a['window_end']} | "
                  f"baseline={a['baseline_rate']:.2%} -> observed={a['observed_rate']:.2%} "
                  f"({a['failed_in_window']}/{a['total_in_window']} txns)")
    else:
        print("[Detector] No anomalous method/window spikes detected (below 3x threshold)")

    # Tag events that fall in an anomaly window
    for event in scored:
        if event.event_type == "HARD_PAYMENT_FAILURE":
            method = event.context.get("payment_method", "")
            ts = event.context.get("timestamp", "")
            from .anomaly import is_event_in_anomaly_window
            event.context["is_anomaly_window"] = is_event_in_anomaly_window(
                method, ts, anomalies
            )
        else:
            event.context["is_anomaly_window"] = False

    # 4. Triage (priority sort)
    triaged = compute_priority(scored)

    # 5. Suppress duplicates
    final = dedupe_events(triaged)

    # Re-sort after dedup
    final.sort(key=lambda e: e.context.get("priority_score", 0), reverse=True)

    # Summary
    type_counts = Counter(e.event_type for e in final)
    print(f"\n[Detector] Final event queue: {len(final)} events")
    for etype, count in type_counts.most_common():
        print(f"  {etype}: {count}")

    return final, anomalies, customers, transactions


if __name__ == "__main__":
    events, anomalies, _, _ = run_detector()
    print(f"\nTop 5 by priority:")
    for e in events[:5]:
        print(f"  {e.event_id} | {e.event_type} | Rs.{e.amount_at_risk:,.2f} | "
              f"score={e.risk_score:.2f} | priority={e.context.get('priority_score', 0):.4f}")
