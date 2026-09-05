"""
Unit tests for detector rules.
"""

import pytest
from datetime import datetime

from revenueguard.detector.ingestion import AtRiskEvent
from revenueguard.detector.rules import (
    rule_hard_payment_failure,
    rule_checkout_abandonment,
    rule_silent_renewal_failure,
    rule_overdue_receivable,
)


def _make_event(event_type, amount=1000.0, **ctx_overrides):
    """Helper to create test events."""
    ctx = {"amount": amount}
    ctx.update(ctx_overrides)
    return AtRiskEvent(
        event_id="test_001",
        event_type=event_type,
        entity_id="test_entity",
        customer_id="test_cust",
        amount_at_risk=amount,
        detected_at=datetime.now(),
        context=ctx,
    )


class TestPaymentFailureRule:
    def test_fires_on_failed_payment(self):
        event = _make_event("HARD_PAYMENT_FAILURE", retry_count=0)
        fired, score, name = rule_hard_payment_failure(event, median_amount=500, customer_failure_count=0)
        assert fired is True
        assert score == 0.8  # 0.6 base + 0.2 (amount 1000 > median 500)
        assert name == "rule_hard_payment_failure"

    def test_not_fires_on_other_type(self):
        event = _make_event("CHECKOUT_ABANDONMENT")
        fired, score, name = rule_hard_payment_failure(event, 500, 0)
        assert fired is False

    def test_fires_even_at_retry_limit(self):
        """
        Detection fires for a retry_count=3 event — it is NOT the detector's
        job to block it. The gate check_max_retries in execution/gates.py is
        the sole enforcement point (proven by TestMaxRetries.test_at_limit_blocked
        and test_over_limit_blocked in test_gates.py). Detection must always
        fire so the case enters the pipeline and reaches the gate.
        """
        event = _make_event("HARD_PAYMENT_FAILURE", retry_count=3)
        fired, score, _ = rule_hard_payment_failure(event, 500, 0)
        assert fired is True

    def test_repeat_failer_bonus(self):
        event = _make_event("HARD_PAYMENT_FAILURE", amount=100, retry_count=0)
        fired, score, _ = rule_hard_payment_failure(event, median_amount=500, customer_failure_count=3)
        assert fired is True
        assert score == 0.75  # 0.6 + 0.15

    def test_all_modifiers(self):
        event = _make_event("HARD_PAYMENT_FAILURE", amount=10000, retry_count=0)
        fired, score, _ = rule_hard_payment_failure(event, median_amount=500, customer_failure_count=5)
        assert fired is True
        assert score == pytest.approx(0.95)  # 0.6 + 0.2 + 0.15


class TestCheckoutRule:
    def test_fires_on_abandoned_short_session(self):
        event = _make_event("CHECKOUT_ABANDONMENT", duration_minutes=5.0, cart_value=500)
        fired, score, name = rule_checkout_abandonment(event)
        assert fired is True
        assert score == 0.4
        assert name == "rule_checkout_abandonment"

    def test_high_cart_value_bonus(self):
        event = _make_event("CHECKOUT_ABANDONMENT", duration_minutes=5.0, cart_value=5000)
        fired, score, _ = rule_checkout_abandonment(event)
        assert fired is True
        assert score == 0.7  # 0.4 + 0.3

    def test_long_session_not_fires(self):
        event = _make_event("CHECKOUT_ABANDONMENT", duration_minutes=20.0, cart_value=5000)
        fired, _, _ = rule_checkout_abandonment(event)
        assert fired is False


class TestRenewalRule:
    def test_fires_on_silent_failure(self):
        event = _make_event("SILENT_RENEWAL_FAILURE")
        fired, score, name = rule_silent_renewal_failure(event)
        assert fired is True
        assert score == 0.7
        assert name == "rule_silent_renewal_failure"

    def test_not_fires_on_other_type(self):
        event = _make_event("HARD_PAYMENT_FAILURE")
        fired, _, _ = rule_silent_renewal_failure(event)
        assert fired is False


class TestOverdueRule:
    def test_recent_overdue(self):
        event = _make_event("OVERDUE_RECEIVABLE", amount=50000, days_overdue=10)
        fired, score, _ = rule_overdue_receivable(event, max_invoice_amount=100000)
        assert fired is True
        # time_score = 10/30 = 0.333, amount_score = 50000/100000 = 0.5
        # score = 0.5 * 0.333 + 0.5 * 0.5 = 0.416
        assert 0.1 < score < 0.5

    def test_very_overdue_high_amount(self):
        event = _make_event("OVERDUE_RECEIVABLE", amount=100000, days_overdue=45)
        fired, score, _ = rule_overdue_receivable(event, max_invoice_amount=100000)
        assert fired is True
        # time_score = min(45/30, 1.0) = 1.0, amount_score = 100k/100k = 1.0
        # score = 0.5 * 1.0 + 0.5 * 1.0 = 1.0
        assert score == 1.0
