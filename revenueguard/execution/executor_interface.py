"""
Abstract executor interface for RevenueGuard action execution.

Architecture intent
-------------------
All execution stages (detection, diagnosis, decision, gating) are completely
decoupled from the concrete execution mechanism.  This module defines the
single interface that any executor must implement.

Current implementation: SimulatedExecutor (see executors.py).

A ``RazorpayLiveExecutor`` implementing this same interface could be swapped
in with zero changes to detection, diagnosis, decision, or gating logic —
this project uses ``SimulatedExecutor`` because production merchant API
credentials are out of scope for a hackathon submission.  The interface exists
so that replacing the simulator with a live integration is a one-file addition,
not a cross-cutting refactor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionOutcome:
    """
    Typed result returned by every executor implementation.

    Fields
    ------
    key             : Idempotency key (entity_id::action_type::attempt)
    entity_id       : The payment/session/subscription/invoice entity acted on
    action_type     : The action that was executed
    attempt         : Attempt number (1 = primary, 2 = fallback, etc.)
    success         : True if the action completed without an error
    outcome         : Human-readable outcome label
                      ('recovered', 'escalated_to_human', 'failed')
    amount_recovered: Rs. amount recovered (0.0 if not recovered)
    details         : Free-form details string for the audit trail
    """
    key: str
    entity_id: str
    action_type: str
    attempt: int
    success: bool
    outcome: str
    amount_recovered: float
    details: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ActionExecutor(ABC):
    """
    Abstract base class for action executors.

    Implementations
    ---------------
    SimulatedExecutor (revenueguard/execution/executors.py)
        Deterministic probabilistic simulation — used for the hackathon demo.
        Results are reproducible (SHA-256 keyed), consistent with P_success
        priors in decision/scoring.py, and idempotent.

    RazorpayLiveExecutor  [NOT YET IMPLEMENTED — out of scope]
        Would call the real Razorpay API:
          - retry_immediately / retry_later  → POST /v1/payments/{id}/capture or re-initiate
          - send_payment_link                → POST /v1/payment_links
          - offer_alt_method                 → POST /v1/payment_links (UPI intent or wallet)
          - escalate_human                   → POST to internal ticketing webhook
        Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET from the merchant account.
        Zero changes needed to detector, diagnosis_engine, decision_engine, or
        any gate — only executors.py is replaced.
    """

    @abstractmethod
    def execute(
        self,
        entity_id: str,
        action_type: str,
        root_cause: str,
        amount_at_risk: float,
        attempt: int = 1,
    ) -> ExecutionOutcome:
        """
        Execute a recovery action for the given entity.

        Parameters
        ----------
        entity_id    : ID of the entity (payment, session, subscription, invoice)
        action_type  : One of retry_immediately | retry_later | send_payment_link
                       | offer_alt_method | escalate_human
        root_cause   : Diagnosed root cause (used for P_success lookup in simulation)
        amount_at_risk: Amount in rupees at risk for this case
        attempt      : 1 for primary, 2 for fallback, etc. — used for idempotency

        Returns
        -------
        ExecutionOutcome
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """
        Reset any internal state (idempotency log, counters, etc.).
        Called between pipeline runs in tests.
        """
        ...
