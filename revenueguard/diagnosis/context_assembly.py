"""
Context assembly: build a structured case file for each AtRiskEvent.
"""

from __future__ import annotations

from collections import Counter
from ..detector.ingestion import AtRiskEvent


def build_case_file(
    event: AtRiskEvent,
    customers: dict[str, dict],
    transactions: list[dict],
    anomalies: list[dict],
) -> dict:
    """
    Build a structured case file for diagnosis.

    Includes:
      - The event itself
      - Customer history (past failure count, preferred method, avg txn amount)
      - Systemic context (is this method+window flagged anomalous?)
    """
    cust_id = event.customer_id
    cust_info = customers.get(cust_id, {})

    # Customer history from transactions
    cust_txns = [t for t in transactions if t["customer_id"] == cust_id]
    cust_failures = [t for t in cust_txns if t["status"] == "failed"]
    cust_amounts = [float(t["amount"]) for t in cust_txns]
    avg_amount = sum(cust_amounts) / len(cust_amounts) if cust_amounts else 0.0

    # Failure code distribution for this customer
    cust_fail_codes = Counter(t.get("failure_code", "") for t in cust_failures)

    # Systemic context
    is_anomaly = event.context.get("is_anomaly_window", False)

    case_file = {
        "event": {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "entity_id": event.entity_id,
            "customer_id": event.customer_id,
            "amount_at_risk": event.amount_at_risk,
            "risk_score": event.risk_score,
            "triggered_rules": event.triggered_rules,
            "context": event.context,
        },
        "customer_history": {
            "customer_id": cust_id,
            "preferred_method": cust_info.get("preferred_method", "unknown"),
            "is_repeat_failer": cust_info.get("is_repeat_failer", "False") == "True",
            "signup_days_ago": int(cust_info.get("signup_days_ago", 0)),
            "total_transactions": len(cust_txns),
            "total_failures": len(cust_failures),
            "failure_rate": len(cust_failures) / len(cust_txns) if cust_txns else 0.0,
            "avg_transaction_amount": round(avg_amount, 2),
            "failure_code_distribution": dict(cust_fail_codes),
        },
        "systemic_context": {
            "is_anomaly_window": is_anomaly,
            "anomaly_flags": [
                a for a in anomalies
                if a.get("method") == event.context.get("payment_method")
            ] if is_anomaly else [],
        },
    }

    return case_file
