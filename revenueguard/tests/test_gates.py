"""
Unit tests for safety gates.

This is a SCORED requirement -- all gates must have passing tests,
including edge cases (exactly at the limit, one over).
"""

import pytest
from datetime import datetime, timedelta

from revenueguard.execution.gates import (
    check_max_retries,
    check_daily_contact_cap,
    check_amount_requires_approval,
    check_disputed_or_fraud_flag,
    check_cooldown,
    check_stale,
)


# ── check_max_retries ─────────────────────────────────────────────────

class TestMaxRetries:
    def test_zero_retries_allowed(self):
        assert check_max_retries(0, max_retries=3) is True

    def test_one_retry_allowed(self):
        assert check_max_retries(1, max_retries=3) is True

    def test_two_retries_allowed(self):
        assert check_max_retries(2, max_retries=3) is True

    def test_at_limit_blocked(self):
        """Exactly at the limit: 3 retries when max is 3 -> blocked."""
        assert check_max_retries(3, max_retries=3) is False

    def test_over_limit_blocked(self):
        assert check_max_retries(5, max_retries=3) is False

    def test_custom_limit(self):
        assert check_max_retries(4, max_retries=5) is True
        assert check_max_retries(5, max_retries=5) is False


# ── check_daily_contact_cap ───────────────────────────────────────────

class TestDailyContactCap:
    def test_zero_contacts_allowed(self):
        assert check_daily_contact_cap(0, max_per_day=2) is True

    def test_one_contact_allowed(self):
        assert check_daily_contact_cap(1, max_per_day=2) is True

    def test_at_limit_blocked(self):
        """Exactly at the limit: 2 contacts when max is 2 -> blocked."""
        assert check_daily_contact_cap(2, max_per_day=2) is False

    def test_over_limit_blocked(self):
        assert check_daily_contact_cap(3, max_per_day=2) is False

    def test_custom_limit(self):
        assert check_daily_contact_cap(4, max_per_day=5) is True
        assert check_daily_contact_cap(5, max_per_day=5) is False


# ── check_amount_requires_approval ────────────────────────────────────

class TestAmountApproval:
    def test_below_threshold_no_approval(self):
        assert check_amount_requires_approval(4999.99) is False

    def test_at_threshold_requires_approval(self):
        """Exactly at threshold: 5000 -> requires approval."""
        assert check_amount_requires_approval(5000.0) is True

    def test_above_threshold_requires_approval(self):
        assert check_amount_requires_approval(10000.0) is True

    def test_zero_amount_no_approval(self):
        assert check_amount_requires_approval(0.0) is False

    def test_custom_threshold(self):
        assert check_amount_requires_approval(999, threshold=1000) is False
        assert check_amount_requires_approval(1000, threshold=1000) is True


# ── check_disputed_or_fraud_flag ──────────────────────────────────────

class TestDisputedFlag:
    def test_not_disputed_allowed(self):
        assert check_disputed_or_fraud_flag(False) is False

    def test_disputed_blocked(self):
        assert check_disputed_or_fraud_flag(True) is True


# ── check_cooldown ────────────────────────────────────────────────────

class TestCooldown:
    def test_cooldown_elapsed(self):
        last = datetime(2026, 9, 1, 10, 0, 0)
        now = datetime(2026, 9, 1, 10, 20, 0)  # 20 min later
        assert check_cooldown(last, now, cooldown_minutes=15) is True

    def test_cooldown_not_elapsed(self):
        last = datetime(2026, 9, 1, 10, 0, 0)
        now = datetime(2026, 9, 1, 10, 10, 0)  # 10 min later
        assert check_cooldown(last, now, cooldown_minutes=15) is False

    def test_exactly_at_cooldown(self):
        """Exactly at the cooldown boundary: 15 min -> allowed."""
        last = datetime(2026, 9, 1, 10, 0, 0)
        now = datetime(2026, 9, 1, 10, 15, 0)
        assert check_cooldown(last, now, cooldown_minutes=15) is True

    def test_one_second_before_cooldown(self):
        last = datetime(2026, 9, 1, 10, 0, 0)
        now = datetime(2026, 9, 1, 10, 14, 59)  # 14m59s later
        assert check_cooldown(last, now, cooldown_minutes=15) is False

    def test_zero_cooldown(self):
        last = datetime(2026, 9, 1, 10, 0, 0)
        now = datetime(2026, 9, 1, 10, 0, 0)
        assert check_cooldown(last, now, cooldown_minutes=0) is True


# ── check_stale ───────────────────────────────────────────────────────

class TestStale:
    def test_recent_not_stale(self):
        created = datetime(2026, 9, 1)
        now = datetime(2026, 9, 10)  # 9 days
        assert check_stale(created, now, max_age_days=30) is False

    def test_exactly_at_limit_not_stale(self):
        """Exactly 30 days: not stale (> 30 required)."""
        created = datetime(2026, 8, 4)
        now = datetime(2026, 9, 3)  # 30 days
        assert check_stale(created, now, max_age_days=30) is False

    def test_one_day_over_stale(self):
        created = datetime(2026, 8, 3)
        now = datetime(2026, 9, 3)  # 31 days
        assert check_stale(created, now, max_age_days=30) is True

    def test_very_old_stale(self):
        created = datetime(2026, 1, 1)
        now = datetime(2026, 9, 3)
        assert check_stale(created, now, max_age_days=30) is True

    def test_custom_max_age(self):
        created = datetime(2026, 9, 1)
        now = datetime(2026, 9, 3)  # 2 days
        assert check_stale(created, now, max_age_days=1) is True
        assert check_stale(created, now, max_age_days=7) is False
