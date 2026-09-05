"""Show the disputed transaction cases and their gate checks."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from revenueguard.audit.audit_log import get_timeline, get_all_cases

# Look for disputed txns
for case_id in ["evt_pay_txn_00026", "evt_pay_txn_00069"]:
    tl = get_timeline(case_id)
    if tl:
        print(f"=== {case_id} ===")
        gate_stage = next((s for s in tl if s["stage"] == "gate_check"), None)
        exec_stage = next((s for s in tl if s["stage"] == "execute"), None)
        if gate_stage:
            print(f"Gate passed: {gate_stage['passed']}")
            print(f"Blocked by: {gate_stage['blocked_by']}")
            print(f"Gates applied:")
            for g in gate_stage.get("gates_applied", []):
                print(f"  {g['gate']}: {json.dumps(g)}")
        if exec_stage:
            print(f"Final action: {exec_stage.get('action')}")
            print(f"Outcome: {exec_stage.get('outcome')}")
            print(f"Details: {exec_stage.get('details')}")
        print()
    else:
        print(f"{case_id}: NOT in audit DB (likely in control group)")
