"""
Safety gates: independently testable pure functions that enforce hard limits.

Each gate returns True if the action is ALLOWED, False if BLOCKED.
Exception: check_amount_requires_approval returns True if approval IS needed.
           check_disputed_or_fraud_flag returns True if BLOCKED (disputed).
           check_stale returns True if TOO OLD (block).
"""

from __future__ import annotations
from datetime import datetime, timedelta


def check_max_retries(transaction_retry_count: int, max_retries: int = 3) -> bool:
    """
    Returns True if more retries are allowed (count < max).
    Returns False if retry limit reached.
    """
    return transaction_retry_count < max_retries


def check_daily_contact_cap(contacts_today: int, max_per_day: int = 2) -> bool:
    """
    Returns True if more contacts are allowed today (count < max).
    Returns False if daily contact cap reached.
    """
    return contacts_today < max_per_day


def check_amount_requires_approval(amount: float, threshold: float = 5000.0) -> bool:
    """
    Returns True if the amount requires human approval (amount >= threshold).
    Returns False if auto-action is allowed.
    """
    return amount >= threshold


def check_disputed_or_fraud_flag(is_disputed: bool) -> bool:
    """
    Returns True if the transaction is disputed/fraud-flagged (BLOCK auto-action).
    Returns False if safe to proceed.
    """
    return is_disputed


def check_cooldown(
    last_attempt_time: datetime,
    now: datetime,
    cooldown_minutes: int = 15,
) -> bool:
    """
    Returns True if cooldown has elapsed (safe to proceed).
    Returns False if still in cooldown period (BLOCK).
    """
    elapsed = (now - last_attempt_time).total_seconds() / 60.0
    return elapsed >= cooldown_minutes


def check_stale(
    entity_created_at: datetime,
    now: datetime,
    max_age_days: int = 30,
) -> bool:
    """
    Returns True if the entity is too old (BLOCK - don't chase stale cases).
    Returns False if within acceptable age.
    """
    age = (now - entity_created_at).days
    return age > max_age_days
