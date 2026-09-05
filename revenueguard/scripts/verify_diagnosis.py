"""Diagnosis verification script - shows Tier1, Tier2, and confidence-override examples."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from revenueguard.detector.detector import run_detector
from revenueguard.diagnosis.diagnosis_engine import diagnose_event

events, anomalies, customers, transactions = run_detector()

# Find examples
tier1_example = None
tier2_example = None
override_example = None

for event in events:
    result = diagnose_event(event, customers, transactions, anomalies)
    
    if result["tier"] == "tier1" and tier1_example is None:
        tier1_example = result
    elif result["tier"] == "tier2" and tier2_example is None:
        tier2_example = result
    
    # Check for confidence override
    if result["diagnosis"]["confidence"] < 0.5 and override_example is None:
        override_example = result

print("=" * 70)
print("TIER 1 (DETERMINISTIC) EXAMPLE")
print("=" * 70)
if tier1_example:
    e = tier1_example["event"]
    d = tier1_example["diagnosis"]
    print(f"Event: {e.event_id} | {e.event_type}")
    print(f"Failure code: {e.context.get('failure_code')}")
    print(f"Tier: {tier1_example['tier']}")
    print(f"Diagnosis JSON:")
    print(json.dumps(d, indent=2))
else:
    print("No Tier 1 example found")

print("\n" + "=" * 70)
print("TIER 2 (LLM / FALLBACK) EXAMPLE")
print("=" * 70)
if tier2_example:
    e = tier2_example["event"]
    d = tier2_example["diagnosis"]
    print(f"Event: {e.event_id} | {e.event_type}")
    print(f"Failure code: {e.context.get('failure_code')}")
    print(f"Is anomaly window: {e.context.get('is_anomaly_window')}")
    print(f"Tier: {tier2_example['tier']}")
    print(f"Diagnosis JSON (raw from LLM/fallback):")
    print(json.dumps(d, indent=2))
else:
    print("No Tier 2 example found (all codes were deterministic)")

print("\n" + "=" * 70)
print("CONFIDENCE OVERRIDE EXAMPLE (conf < 0.5 -> escalate_human)")  
print("=" * 70)
if override_example:
    e = override_example["event"]
    d = override_example["diagnosis"]
    print(f"Event: {e.event_id} | {e.event_type}")
    print(f"Tier: {override_example['tier']}")
    print(f"Diagnosis JSON:")
    print(json.dumps(d, indent=2))
    print(f"\nNote: recommended_action is '{d['recommended_action']}' because confidence={d['confidence']} < 0.5")
else:
    print("No confidence override triggered (all diagnoses had confidence >= 0.5)")
    print("This is expected when Tier 1 codes all have confidence >= 0.9")
    print("The override would fire on unknown/fallback codes or low-confidence LLM results")
