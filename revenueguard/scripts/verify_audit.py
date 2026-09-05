"""Show full audit timelines for: 1) a recovered case, 2) the disputed case."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from revenueguard.audit.audit_log import get_timeline, get_all_cases

cases = get_all_cases()

# Find a successfully recovered case
recovered_case = None
disputed_case = None

for c in cases:
    tl = c["timeline"]
    exec_stage = next((s for s in tl if s["stage"] == "execute"), None)
    gate_stage = next((s for s in tl if s["stage"] == "gate_check"), None)
    
    if exec_stage and exec_stage.get("outcome") == "recovered" and recovered_case is None:
        recovered_case = c
    
    # Find disputed case (blocked by disputed_or_fraud gate)
    if gate_stage and gate_stage.get("blocked_by") == "disputed_or_fraud":
        disputed_case = c

print("=" * 70)
print("CASE 1: SUCCESSFULLY AUTO-RECOVERED")
print("=" * 70)
if recovered_case:
    print(f"case_id: {recovered_case['case_id']}")
    print(json.dumps(recovered_case["timeline"], indent=2))
else:
    print("No recovered case found in audit DB")

print("\n" + "=" * 70)
print("CASE 2: DISPUTED TRANSACTION (GATE-BLOCKED)")
print("=" * 70)
if disputed_case:
    print(f"case_id: {disputed_case['case_id']}")
    print(json.dumps(disputed_case["timeline"], indent=2))
else:
    print("No disputed-blocked case found in audit DB")
    print("\nSearching for any gate-blocked case instead...")
    for c in cases:
        tl = c["timeline"]
        gate_stage = next((s for s in tl if s["stage"] == "gate_check"), None)
        if gate_stage and not gate_stage.get("passed", True):
            print(f"\nFound gate-blocked case: {c['case_id']}")
            print(f"Blocked by: {gate_stage.get('blocked_by')}")
            print(json.dumps(tl, indent=2))
            break
