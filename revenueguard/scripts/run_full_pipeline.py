"""
run_full_pipeline.py - End-to-end RevenueGuard pipeline.

Runs: generate data -> detect -> holdout split -> diagnose -> decide ->
      execute -> audit -> measure -> report.
"""

import sys
import os

# Add project root (parent of revenueguard/) to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Load .env from project root so OPENROUTER_API_KEY is available
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".env",
    )
    load_dotenv(_env_path, override=True)
    print(f"[dotenv] Loaded from: {_env_path}")
except ImportError:
    pass  # python-dotenv not installed; rely on shell env vars

from revenueguard.data.generate_data import print_data_summary
from revenueguard.detector.detector import run_detector
from revenueguard.diagnosis.diagnosis_engine import run_diagnosis
from revenueguard.decision.decision_engine import run_decisions
from revenueguard.execution.execution_engine import run_execution, gate_trigger_stats
from revenueguard.audit.audit_log import init_db, clear_all, log_full_event
from revenueguard.measurement.holdout import split_holdout, simulate_baseline_recovery
from revenueguard.measurement.metrics import compute_metrics, print_metrics


def main():
    print("=" * 70)
    print("REVENUEGUARD -- AI REVENUE RECOVERY AGENT")
    print("Full Pipeline Execution")
    print("=" * 70)

    # Step 1: Data summary
    print("\n>>> STEP 1: Data Summary")
    print_data_summary()

    # Step 2: Detection
    print("\n>>> STEP 2: Detection")
    events, anomalies, customers, transactions = run_detector()

    # Step 7 (early): Holdout split
    print("\n>>> STEP 7a: Holdout Split")
    treatment_events, control_events = split_holdout(events)

    # Baseline simulation for control group
    print("\n>>> STEP 7b: Baseline Simulation (Control)")
    control_results = simulate_baseline_recovery(control_events)

    # Steps 3-5: Pipeline on treatment group
    print("\n>>> STEP 3: Diagnosis (Treatment Group)")
    diagnosed = run_diagnosis(treatment_events, customers, transactions, anomalies)

    print("\n>>> STEP 4: Decision")
    decided = run_decisions(diagnosed)

    print("\n>>> STEP 5: Execution")
    executed = run_execution(decided)

    # Step 6: Audit trail
    print("\n>>> STEP 6: Audit Trail")
    init_db()
    clear_all()  # clean slate for this run
    for ex in executed:
        log_full_event(ex)
    # Also log control events as "untouched"
    from revenueguard.audit.audit_log import append_stage
    for cr in control_results:
        append_stage(cr["event_id"], {
            "stage": "control_baseline",
            "group": "control",
            "recovered": cr["recovered"],
            "amount_recovered": cr["amount_recovered"],
        })
    print(f"  Logged {len(executed)} treatment + {len(control_results)} control cases to audit DB")

    # Step 7c: Metrics
    print("\n>>> STEP 7c: Final Metrics")
    # Import current gate stats
    from revenueguard.execution.execution_engine import gate_trigger_stats as gts
    metrics = compute_metrics(executed, control_results, gts, len(events))
    print_metrics(metrics)

    # Step 9: Showcase failure case
    print("\n>>> STEP 9: Showcase Failure Case")
    showcase_ids = metrics.get("showcase_failure_cases", [])
    if showcase_ids:
        for sid in showcase_ids:
            showcase = next((e for e in executed if e["event"].event_id == sid), None)
            if showcase:
                print(f"\n  SHOWCASE CASE: {sid}")
                print(f"  Event type: {showcase['event'].event_type}")
                print(f"  Amount: Rs.{showcase['event'].amount_at_risk:,.2f}")
                print(f"  Root cause: {showcase['diagnosis']['root_cause']}")
                print(f"  Execution details: {showcase['execution']['details']}")
                print(f"  Outcome: {showcase['execution']['outcome']}")
                print(f"  This case demonstrates graceful degradation: ")
                print(f"  the agent tried recovery, failed, and correctly")
                print(f"  escalated to human rather than retrying indefinitely.")
    else:
        print("  No showcase failure case found in this run.")

    print("\n" + "=" * 70)
    print("PIPELINE COMPLETE")
    print("=" * 70)

    # Return metrics for API use
    return metrics


if __name__ == "__main__":
    main()
