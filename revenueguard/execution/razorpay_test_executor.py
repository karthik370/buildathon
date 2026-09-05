"""
RazorpayTestModeExecutor — concrete ActionExecutor using Razorpay Test Mode API.

SCOPE STATEMENT (read before assuming anything is "real"):
===========================================================

    send_payment_link  → REAL: calls POST /v1/payment_links via Razorpay test-mode
                         API. Returns a real short_url, id, status from Razorpay's
                         servers. No real money moves in test mode.

    offer_alt_method   → PARTIALLY REAL: calls POST /v1/orders to create a real
                         test-mode order (the first step any alternate-method flow
                         needs). The actual method-switch checkout UI is out of scope.

    retry_immediately  → SIMULATED (structural API limitation, not an implementation
    retry_later          gap). Razorpay, like all card networks, does not allow a
                         merchant to force-retry a customer's payment. Only the
                         customer can re-attempt via their own checkout session.
                         Merchant-initiated retries are not possible by design.
                         Faking this call would be dishonest.

    escalate_human     → No API call. Routes to the human approval queue.

Configuration:
    RAZORPAY_TEST_KEY_ID     — Test-mode key_id (rzp_test_xxx)
    RAZORPAY_TEST_KEY_SECRET — Test-mode key_secret

    Both are available free from https://dashboard.razorpay.com — no credit card,
    no production merchant approval needed. Test mode is instantly available on
    signup.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Optional

import requests

from .executor_interface import ActionExecutor, ExecutionOutcome
from ..decision.scoring import get_p_success

RAZORPAY_BASE_URL = "https://api.razorpay.com/v1"
REQUEST_TIMEOUT = 20  # seconds


def _get_auth() -> Optional[tuple[str, str]]:
    """Return (key_id, key_secret) or None if env vars not set."""
    key_id     = os.environ.get("RAZORPAY_TEST_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_TEST_KEY_SECRET", "")
    if key_id and key_secret:
        return (key_id, key_secret)
    return None


class RazorpayTestModeExecutor(ActionExecutor):
    """
    Executor that calls real Razorpay Test-Mode API endpoints where possible.
    Falls back to SimulatedExecutor behaviour for actions that cannot be made
    real (see module-level scope statement).
    """

    def __init__(self) -> None:
        self._execution_log: dict[str, dict] = {}

    def _idempotency_key(self, entity_id: str, action_type: str, attempt: int) -> str:
        return f"{entity_id}::{action_type}::{attempt}"

    def _deterministic_random(self, key: str) -> float:
        """Fallback for non-API actions — same SHA-256 keyed sim as SimulatedExecutor."""
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

        if action_type == "send_payment_link":
            outcome = self._send_payment_link(key, entity_id, amount_at_risk, attempt)
        elif action_type == "offer_alt_method":
            outcome = self._create_order(key, entity_id, amount_at_risk, attempt)
        elif action_type == "escalate_human":
            outcome = ExecutionOutcome(
                key=key, entity_id=entity_id, action_type=action_type, attempt=attempt,
                success=True, outcome="escalated_to_human", amount_recovered=0.0,
                details="Case routed to human approval queue.",
            )
        else:
            # retry_immediately / retry_later: structural API limitation — simulate
            outcome = self._simulate_retry(key, entity_id, action_type, root_cause, amount_at_risk, attempt)

        self._execution_log[key] = outcome.to_dict()
        return outcome

    def _send_payment_link(
        self, key: str, entity_id: str, amount_at_risk: float, attempt: int
    ) -> ExecutionOutcome:
        """
        Call POST /v1/payment_links in Razorpay test mode.
        Returns a real short_url from Razorpay's servers.
        Amount is passed in paise (Razorpay's unit — 1 Rs = 100 paise).
        """
        auth = _get_auth()
        if not auth:
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="send_payment_link",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details="RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET not set. "
                        "Cannot call real API. Set env vars to use test mode.",
            )

        amount_paise = int(amount_at_risk * 100)
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "accept_partial": False,
            "description": f"RevenueGuard recovery link for entity {entity_id}",
            "reminder_enable": False,
            "notes": {
                "revenueguard_entity_id": entity_id,
                "revenueguard_attempt": str(attempt),
            },
        }

        try:
            # Rate-limit guard: Razorpay test-mode Payment Links API allows ~5 req/sec.
            # Sleep before the call so batch runs don't trigger HTTP 429.
            time.sleep(0.6)

            resp = requests.post(
                f"{RAZORPAY_BASE_URL}/payment_links",
                json=payload,
                auth=auth,
                timeout=REQUEST_TIMEOUT,
            )

            # Retry once on 429 with back-off
            if resp.status_code == 429:
                print(f"[RazorpayTestMode] Payment Links rate-limited (429) for {entity_id}. "
                      f"Backing off 5s and retrying...")
                time.sleep(5)
                resp = requests.post(
                    f"{RAZORPAY_BASE_URL}/payment_links",
                    json=payload,
                    auth=auth,
                    timeout=REQUEST_TIMEOUT,
                )

            resp.raise_for_status()
            data = resp.json()

            short_url = data.get("short_url", "")
            link_id   = data.get("id", "")
            status    = data.get("status", "")

            print(f"[RazorpayTestMode] Payment link created: id={link_id} "
                  f"status={status} short_url={short_url}")
            print(f"[RazorpayTestMode] RAW API RESPONSE: {resp.text[:500]}")

            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="send_payment_link",
                attempt=attempt,
                success=True,
                outcome="payment_link_sent",
                amount_recovered=0.0,  # link sent; money not recovered until customer pays
                details=(
                    f"[REAL TEST-MODE API] Payment link created via Razorpay. "
                    f"id={link_id} | status={status} | short_url={short_url}"
                ),
            )

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:400] if e.response is not None else ""

            if status_code == 429:
                print(f"[RazorpayTestMode] Rate limit hit for {entity_id}. "
                      f"Cancel old links at dashboard.razorpay.com/app/payment-links to free quota.")
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="send_payment_link",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details=f"Razorpay API error HTTP {status_code}: {body}",
            )

        except Exception as e:
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="send_payment_link",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details=f"Razorpay API exception: {type(e).__name__}: {e}",
            )

    def _create_order(
        self, key: str, entity_id: str, amount_at_risk: float, attempt: int
    ) -> ExecutionOutcome:
        """
        Call POST /v1/orders to create a test-mode order for alternate-method flow.

        This is the first API step a real alternate-method flow would execute.
        The checkout UI (where the customer picks UPI/wallet/netbanking) is
        out of scope for this build.
        """
        auth = _get_auth()
        if not auth:
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="offer_alt_method",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details="RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET not set.",
            )

        amount_paise = int(amount_at_risk * 100)
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "notes": {
                "revenueguard_entity_id": entity_id,
                "revenueguard_flow": "alt_method",
                "revenueguard_attempt": str(attempt),
            },
        }

        try:
            resp = requests.post(
                f"{RAZORPAY_BASE_URL}/orders",
                json=payload,
                auth=auth,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            order_id = data.get("id", "")
            status   = data.get("status", "")
            print(f"[RazorpayTestMode] Order created: id={order_id} status={status}")
            print(f"[RazorpayTestMode] RAW API RESPONSE: {resp.text[:500]}")

            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="offer_alt_method",
                attempt=attempt, success=True, outcome="alt_method_order_created",
                amount_recovered=0.0,
                details=(
                    f"[REAL TEST-MODE API] Order created via Razorpay for alt-method flow. "
                    f"id={order_id} | status={status}. "
                    f"Checkout UI (customer-facing method selection) is out of scope."
                ),
            )

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "?"
            body = e.response.text[:300] if e.response is not None else ""
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="offer_alt_method",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details=f"Razorpay API error HTTP {status_code}: {body}",
            )
        except Exception as e:
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type="offer_alt_method",
                attempt=attempt, success=False, outcome="failed",
                amount_recovered=0.0,
                details=f"Razorpay API exception: {type(e).__name__}: {e}",
            )

    def _simulate_retry(
        self, key: str, entity_id: str, action_type: str,
        root_cause: str, amount_at_risk: float, attempt: int
    ) -> ExecutionOutcome:
        """
        Simulated fallback for retry_immediately and retry_later.

        WHY THIS IS SIMULATED AND NOT REAL:
        Razorpay (like all card networks and payment processors) does not
        provide a merchant-facing API to force-retry a customer's payment.
        Payment retries must be customer-initiated — the customer clicks
        "retry" on their own checkout session. There is no
        POST /v1/payments/{id}/retry endpoint, nor any equivalent.
        This is a structural limitation of the payment industry, not an
        implementation gap or an oversight in this build.
        """
        p_success = get_p_success(action_type, root_cause)
        roll = self._deterministic_random(key)
        success = roll < p_success

        details_prefix = (
            "[SIMULATED — structural API limitation: merchant cannot force-retry "
            "a customer's payment; only customer can retry via checkout session] "
        )
        if success:
            return ExecutionOutcome(
                key=key, entity_id=entity_id, action_type=action_type,
                attempt=attempt, success=True, outcome="recovered",
                amount_recovered=amount_at_risk,
                details=details_prefix + f"Simulated '{action_type}' succeeded.",
            )
        return ExecutionOutcome(
            key=key, entity_id=entity_id, action_type=action_type,
            attempt=attempt, success=False, outcome="failed",
            amount_recovered=0.0,
            details=details_prefix + f"Simulated '{action_type}' failed.",
        )

    def reset(self) -> None:
        self._execution_log = {}
