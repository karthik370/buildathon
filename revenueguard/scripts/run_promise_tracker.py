"""
Run the Promise-to-Pay tracker as a standalone pass.

This re-uses the full detect→diagnose→decide→execute→audit stack.
Promise-tracker events are NOT mixed into the main pipeline run —
they are a separate batch to keep metrics clean and avoid double-counting
invoices that are already detected as OVERDUE_RECEIVABLE.

Output: raw counts, action choices, audit trail for one case.
"""

import sys
sys.path.insert(0, r'c:\Users\govar\razorpay')
from dotenv import load_dotenv
load_dotenv(r'c:\Users\govar\razorpay\.env', override=False)

from datetime import datetime
from collections import Counter

from revenueguard.detector.promise_tracker import build_promise_events
from revenueguard.detector.ingestion import load_customers, load_payment_transactions
from revenueguard.detector.anomaly import detect_anomalous_windows
from revenueguard.diagnosis.diagnosis_engine import run_diagnosis
from revenueguard.decision.decision_engine import run_decisions
from revenueguard.execution.execution_engine import run_execution
from revenueguard.audit.audit_log import init_db, log_full_event, get_timeline

init_db()

now = datetime.now()
customers = load_customers()
transactions = load_payment_transactions()
anomalies = detect_anomalous_windows(transactions)

# ── Detect ────────────────────────────────────────────────────────────
events = build_promise_events(now=now)

print("=" * 70)
print("PROMISE-TO-PAY TRACKER — FULL PIPELINE RUN")
print("=" * 70)
print(f"\n[P2P Detector] Scanned invoices at: {now.isoformat()}")
print(f"[P2P Detector] Events detected: {len(events)}")

type_counts = Counter(e.event_type for e in events)
for etype, count in type_counts.items():
    print(f"  {etype}: {count}")

if not events:
    print("\nNo promise events in current data. This is expected if no 'broken'")
    print("status invoices exist and no promise dates fall within 24h of now.")
    sys.exit(0)

print(f"\n[P2P Detector] Events:")
for e in events:
    ctx = e.context
    print(f"  {e.event_id:40s}  {e.event_type:20s}  Rs.{e.amount_at_risk:>12,.2f}")
    if e.event_type == "PROMISE_BROKEN":
        print(f"    promise_date={ctx['promise_to_pay_date'][:10]}  "
              f"days_late={ctx['days_promise_overdue']}")
    elif e.event_type == "PROMISE_DUE_SOON":
        print(f"    promise_date={ctx['promise_to_pay_date'][:10]}  "
              f"hours_until={ctx['hours_until_promise_due']:.1f}h")

# ── Diagnose ──────────────────────────────────────────────────────────
diagnosed = run_diagnosis(events, customers, transactions, anomalies)

# ── Decide ────────────────────────────────────────────────────────────
decided = run_decisions(diagnosed)

# ── Execute ───────────────────────────────────────────────────────────
executed = run_execution(decided)

# ── Log audit trail ───────────────────────────────────────────────────
for e in executed:
    log_full_event(e)

# ── Metrics ───────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("PROMISE-TO-PAY METRICS")
print("=" * 70)

broken_events  = [e for e in events if e.event_type == "PROMISE_BROKEN"]
due_soon_events = [e for e in events if e.event_type == "PROMISE_DUE_SOON"]

executed_by_type = {}
for ex in executed:
    etype = ex["event"].event_type
    if etype not in executed_by_type:
        executed_by_type[etype] = []
    executed_by_type[etype].append(ex)

print(f"\nEvent breakdown:")
print(f"  PROMISE_BROKEN:    {len(broken_events)}")
print(f"  PROMISE_DUE_SOON:  {len(due_soon_events)}")

print(f"\nOutcomes by event type:")
for etype, execs in executed_by_type.items():
    outcomes = Counter(e["execution"]["outcome"] for e in execs)
    actions  = Counter(e["execution"]["action_taken"] for e in execs)
    recovered = sum(e["execution"]["amount_recovered"] for e in execs)
    print(f"  {etype}:")
    for outcome, count in outcomes.most_common():
        print(f"    outcome={outcome}: {count}")
    for action, count in actions.most_common():
        print(f"    action={action}: {count}")
    print(f"    Amount recovered: Rs.{recovered:,.2f}")

# ── Full audit trail for first case ──────────────────────────────────
if executed:
    showcase = executed[0]
    case_id = showcase["event"].event_id
    timeline = get_timeline(case_id)

    print(f"\n" + "=" * 70)
    print(f"AUDIT TRAIL — {case_id}")
    print("=" * 70)
    import json
    for stage in timeline:
        print(f"\n  [{stage.get('ts', '?')[:19]}] stage={stage['stage']}")
        for k, v in stage.items():
            if k in ("stage", "ts"):
                continue
            if isinstance(v, str) and len(v) > 120:
                v = v[:120] + "…"
            print(f"    {k}: {v}")
