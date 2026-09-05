"""
Simulated executor — concrete implementation of ActionExecutor.

Each executor returns a probabilistic outcome using the same P_success priors
as the decision scoring (internally consistent simulation).

Execution is IDEMPOTENT: keyed by (entity_id, action_type, attempt_number).

Architecture note
-----------------
This module implements ``ActionExecutor`` from ``executor_interface.py``.
A ``RazorpayLiveExecutor`` implementing the same interface could be swapped
in with zero changes to detection, diagnosis, decision, or gating logic.
See ``executor_interface.py`` for the full interface contract and docstring.
"""

from __future__ import annotations

import hashlib

from .executor_interface import ActionExecutor, ExecutionOutcome
from ..decision.scoring import get_p_success

# Module-level default instance used by execution_engine.py
_default_executor: SimulatedExecutor | None = None


def _get_default_executor() -> "SimulatedExecutor":
    global _default_executor
    if _default_executor is None:
        _default_executor = SimulatedExecutor()
    return _default_executor


class SimulatedExecutor(ActionExecutor):
    """
    Deterministic probabilistic simulator.

    Uses SHA-256 keyed pseudo-random numbers so that every (entity_id,
    action_type, attempt) triple always produces the same outcome — results
    are reproducible across runs with the same data.
    """

    def __init__(self) -> None:
        self._execution_log: dict[str, dict] = {}

    def _idempotency_key(self, entity_id: str, action_type: str, attempt: int) -> str:
        return f"{entity_id}::{action_type}::{attempt}"

    def _deterministic_random(self, key: str) -> float:
        h = hashlib.sha256(key.encode()).hexdigest()
        return int(h[:8], 16) / 0xFFFFFFFF

    def execute(
        self,
        entity_id: str,
        action_type: str,
        root_cause: str,
        amount_at_risk: float,
        attempt: int = 1,
    ) -> ExecutionOutcome:
        key = self._idempotency_key(entity_id, action_type, attempt)

        if key in self._execution_log:
            return ExecutionOutcome(**self._execution_log[key])

        p_success = get_p_success(action_type, root_cause)
        roll = self._deterministic_random(key)
        success = roll < p_success

        if action_type == "escalate_human":
            outcome = ExecutionOutcome(
                key=key,
                entity_id=entity_id,
                action_type=action_type,
                attempt=attempt,
                success=True,
                outcome="escalated_to_human",
                amount_recovered=0.0,
                details="Case handed off to human agent for manual resolution.",
            )
        elif action_type == "send_payment_link":
            # Mirror real-mode behaviour: link is created (sent) but not yet paid.
            # Recovery = 0 until the customer actually completes payment.
            outcome = ExecutionOutcome(
                key=key,
                entity_id=entity_id,
                action_type=action_type,
                attempt=attempt,
                success=True,
                outcome="payment_link_sent",
                amount_recovered=0.0,
                details="[Simulated] Payment link queued for delivery. Awaiting customer action.",
            )
        elif action_type == "offer_alt_method":
            # Mirror real-mode: order created, not yet paid.
            outcome = ExecutionOutcome(
                key=key,
                entity_id=entity_id,
                action_type=action_type,
                attempt=attempt,
                success=True,
                outcome="alt_method_order_created",
                amount_recovered=0.0,
                details="[Simulated] Alt-method order created. Awaiting customer payment.",
            )
        elif success:
            outcome = ExecutionOutcome(
                key=key,
                entity_id=entity_id,
                action_type=action_type,
                attempt=attempt,
                success=True,
                outcome="recovered",
                amount_recovered=amount_at_risk,
                details=f"Action '{action_type}' succeeded. Full amount recovered.",
            )
        else:
            outcome = ExecutionOutcome(
                key=key,
                entity_id=entity_id,
                action_type=action_type,
                attempt=attempt,
                success=False,
                outcome="failed",
                amount_recovered=0.0,
                details=f"Action '{action_type}' did not result in recovery.",
            )

        self._execution_log[key] = outcome.to_dict()
        return outcome

    def reset(self) -> None:
        self._execution_log = {}


# ── Backwards-compatible module-level shim ────────────────────────────
# execution_engine.py calls execute_action() and reset_execution_log() as
# module-level functions.  These shims delegate to the default instance so
# existing callers need no changes.

def execute_action(
    entity_id: str,
    action_type: str,
    root_cause: str,
    amount_at_risk: float,
    attempt: int = 1,
) -> dict:
    """Module-level shim — delegates to the default SimulatedExecutor instance."""
    result = _get_default_executor().execute(
        entity_id, action_type, root_cause, amount_at_risk, attempt
    )
    return result.to_dict()


def reset_execution_log() -> None:
    """Module-level shim — resets the default SimulatedExecutor instance."""
    _get_default_executor().reset()
