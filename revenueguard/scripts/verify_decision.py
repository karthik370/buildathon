"""Decision engine proof - show full EV scoring for the bank_not_available case."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from revenueguard.detector.detector import run_detector
from revenueguard.diagnosis.diagnosis_engine import diagnose_event
from revenueguard.decision.candidates import generate_candidates
from revenueguard.decision.scoring import score_candidates

events, anomalies, customers, transactions = run_detector()

# Find the bank_not_available case (txn_00129)
for event in events:
    if event.entity_id == "txn_00129":
        result = diagnose_event(event, customers, transactions, anomalies)
        diagnosis = result["diagnosis"]
        
        print("=" * 70)
        print(f"DECISION ENGINE PROOF: {event.event_id}")
        print("=" * 70)
        print(f"Entity: {event.entity_id}")
        print(f"Amount at risk: Rs.{event.amount_at_risk:,.2f}")
        print(f"Root cause: {diagnosis['root_cause']}")
        print(f"Confidence: {diagnosis['confidence']}")
        
        candidates = generate_candidates(diagnosis, event.event_type)
        print(f"\nCandidate actions: {candidates}")
        
        scored = score_candidates(candidates, diagnosis["root_cause"], event.amount_at_risk, 0)
        
        print(f"\nFULL EV SCORING (all candidates, not just winner):")
        print("-" * 70)
        for s in scored:
            print(json.dumps(s, indent=2))
        
        print(f"\nWINNER: {scored[0]['action']} with EV={scored[0]['ev']}")
        break
