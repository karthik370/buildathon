"""
Anomaly detection: compute baseline failure rate per payment_method and
flag method+time-window combinations where observed rate >= 3x baseline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

# Type alias for anomaly flags
AnomalyFlag = dict  # {method, window_start, window_end, baseline_rate, observed_rate}


def compute_method_baseline_rates(transactions: list[dict]) -> dict[str, float]:
    """
    Compute overall failure rate per payment_method across the full dataset.
    Returns {method: failure_rate}.
    """
    method_total: dict[str, int] = defaultdict(int)
    method_failed: dict[str, int] = defaultdict(int)

    for t in transactions:
        method = t["payment_method"]
        method_total[method] += 1
        if t["status"] == "failed":
            method_failed[method] += 1

    rates: dict[str, float] = {}
    for method, total in method_total.items():
        rates[method] = method_failed.get(method, 0) / total if total > 0 else 0.0

    return rates


def detect_anomalous_windows(
    transactions: list[dict],
    window_hours: int = 3,
    spike_multiplier: float = 3.0,
) -> list[AnomalyFlag]:
    """
    Slide a window across time for each payment_method and flag windows
    where the failure rate is >= spike_multiplier * baseline rate.

    Returns list of anomaly flag dicts.
    """
    baselines = compute_method_baseline_rates(transactions)

    # Parse timestamps
    parsed = []
    for t in transactions:
        parsed.append({
            **t,
            "_ts": datetime.fromisoformat(t["timestamp"]),
        })
    parsed.sort(key=lambda x: x["_ts"])

    if not parsed:
        return []

    # Group by method
    by_method: dict[str, list[dict]] = defaultdict(list)
    for t in parsed:
        by_method[t["payment_method"]].append(t)

    anomalies: list[AnomalyFlag] = []

    for method, txns in by_method.items():
        baseline = baselines.get(method, 0)
        if baseline == 0:
            continue  # can't spike if there are no failures at all

        # Slide a window
        min_ts = txns[0]["_ts"]
        max_ts = txns[-1]["_ts"]
        window_delta = timedelta(hours=window_hours)
        step_delta = timedelta(hours=1)  # 1-hour step

        current_start = min_ts
        while current_start + window_delta <= max_ts + step_delta:
            window_end = current_start + window_delta
            window_txns = [
                t for t in txns
                if current_start <= t["_ts"] < window_end
            ]
            if len(window_txns) >= 3:  # need enough samples
                failed_in_window = sum(1 for t in window_txns if t["status"] == "failed")
                observed_rate = failed_in_window / len(window_txns)

                if observed_rate >= spike_multiplier * baseline:
                    anomalies.append({
                        "method": method,
                        "window_start": current_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        "baseline_rate": round(baseline, 4),
                        "observed_rate": round(observed_rate, 4),
                        "total_in_window": len(window_txns),
                        "failed_in_window": failed_in_window,
                    })

            current_start += step_delta

    # Deduplicate overlapping windows (keep the worst one per method)
    deduped = _dedupe_overlapping(anomalies)
    return deduped


def _dedupe_overlapping(anomalies: list[AnomalyFlag]) -> list[AnomalyFlag]:
    """Keep only the worst (highest observed_rate) anomaly per method."""
    if not anomalies:
        return []

    best_per_method: dict[str, AnomalyFlag] = {}
    for a in anomalies:
        method = a["method"]
        if method not in best_per_method or a["observed_rate"] > best_per_method[method]["observed_rate"]:
            best_per_method[method] = a

    return list(best_per_method.values())


def is_event_in_anomaly_window(
    event_method: str,
    event_timestamp: str,
    anomalies: list[AnomalyFlag],
) -> bool:
    """Check if a specific event falls within a detected anomaly window."""
    for a in anomalies:
        if a["method"] != event_method:
            continue
        ts = datetime.fromisoformat(event_timestamp)
        w_start = datetime.fromisoformat(a["window_start"])
        w_end = datetime.fromisoformat(a["window_end"])
        if w_start <= ts < w_end:
            return True
    return False
