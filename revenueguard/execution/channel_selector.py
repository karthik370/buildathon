"""
Multi-channel outreach selection logic.

Channel prior assumptions (HAND-SET, same honesty standard as P_success priors):
    whatsapp  0.85  — WhatsApp Business API published open-rate benchmark
    sms       0.65  — TRAI published SMS open-rate data (India)
    email     0.25  — Industry average email open-rate

These are not learned from real data. They represent the prior probability
that a customer will open/respond to an outreach on each channel.

Preference learning (lightweight, NOT a trained model):
    After each executed outreach action, log (customer_id, channel, recovered).
    For any customer with >= MIN_OBSERVATIONS observed channel outcomes, pick the
    channel with the highest observed success rate on their next event.
    Otherwise fall back to the channel prior ranking.
    This is a simple frequency-based update. It is NOT machine learning.
    Stated plainly because overselling a counter as "ML-powered" would be
    dishonest and inconsistent with this project's standards.

Delivery is SIMULATED for all channels (SMS, email, WhatsApp).
No real WhatsApp Business API, SMS API, or email API integration exists.
The point being demonstrated is the channel-selection logic, not the
delivery infrastructure. This scope boundary is documented here and in the README.
"""

from __future__ import annotations

from collections import defaultdict

# ── Channel priors ────────────────────────────────────────────────────
# Higher score = preferred default channel.
# HAND-SET ASSUMPTIONS — documented sources above.
CHANNEL_PRIOR_SCORE: dict[str, float] = {
    "whatsapp": 0.85,
    "sms":      0.65,
    "email":    0.25,
}

# Channels that apply only to outreach-type actions
OUTREACH_ACTIONS = {
    "send_payment_link",
    "offer_alt_method",
    "send_reminder_gentle",
    "send_reminder_firm",
    "request_new_commitment",
}

# Minimum observations before we trust the per-customer preference
MIN_OBSERVATIONS = 2

# Per-batch channel outcome log: {customer_id: {channel: [True/False, ...]}}
# Loaded from persistent SQLite store at startup; flushed back after each run.
_channel_log: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))


def load_from_db() -> None:
    """
    Load persistent channel outcomes from SQLite into _channel_log.
    Called at the start of each pipeline run so prior-batch customer
    history survives across runs.
    """
    global _channel_log
    try:
        from .channel_history import load_channel_history
        loaded = load_channel_history()
        # Merge into defaultdict format
        new_log: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
        for cust, channels in loaded.items():
            for ch, outcomes in channels.items():
                new_log[cust][ch].extend(outcomes)
        _channel_log = new_log
    except Exception as e:
        # Never crash the pipeline on a channel-history load failure
        import logging
        logging.getLogger(__name__).warning(f"Channel history load failed: {e}")


def reset_channel_log() -> None:
    """Reload channel log from persistent DB (don't wipe — prior-batch history must survive)."""
    load_from_db()


def record_channel_outcome(customer_id: str, channel: str, recovered: bool) -> None:
    """Log a (customer, channel, outcome) triple after execution."""
    _channel_log[customer_id][channel].append(recovered)


def flush_to_db() -> None:
    """
    Persist the current run's channel outcomes to SQLite.
    Called after the pipeline run completes.
    This is what makes per-customer channel preferences survive across batches.
    """
    try:
        from .channel_history import persist_channel_history
        persist_channel_history(_channel_log)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Channel history persist failed: {e}")


def select_channel(customer_id: str, action_type: str) -> str:
    """
    Select the outreach channel for this customer + action.

    Returns one of: 'whatsapp', 'sms', 'email'.
    Returns 'n/a' if the action is not an outreach-type action.

    Selection logic:
    1. If not an outreach action → 'n/a'
    2. If customer has >= MIN_OBSERVATIONS outcomes on any channel:
         pick the channel with highest observed success rate.
         On tie: prefer whatsapp > sms > email (prior ordering).
    3. Otherwise: pick the channel with highest prior score (whatsapp).
    """
    if action_type not in OUTREACH_ACTIONS:
        return "n/a"

    customer_history = _channel_log.get(customer_id, {})

    # Count observations per channel
    qualified = {}
    for channel, outcomes in customer_history.items():
        if len(outcomes) >= MIN_OBSERVATIONS:
            success_rate = sum(outcomes) / len(outcomes)
            qualified[channel] = (success_rate, len(outcomes))

    if qualified:
        # Sort by (success_rate desc, prior_score desc) for tie-breaking
        best = max(
            qualified,
            key=lambda ch: (qualified[ch][0], CHANNEL_PRIOR_SCORE.get(ch, 0)),
        )
        return best

    # No sufficient history — use prior ranking
    return max(CHANNEL_PRIOR_SCORE, key=lambda ch: CHANNEL_PRIOR_SCORE[ch])


def channel_selection_reason(customer_id: str, channel: str) -> str:
    """
    Return a human-readable reason string for the channel selection,
    for logging in the audit trail.
    """
    customer_history = _channel_log.get(customer_id, {})
    outcomes = customer_history.get(channel, [])
    if len(outcomes) >= MIN_OBSERVATIONS:
        rate = sum(outcomes) / len(outcomes)
        return (
            f"Preference-learned: '{channel}' chosen based on {len(outcomes)} "
            f"observed outcomes for customer (success rate={rate:.0%}). "
            f"Prior ranking would have chosen 'whatsapp'."
        )
    return (
        f"Prior-based: '{channel}' chosen from hand-set channel priors "
        f"(whatsapp=0.85, sms=0.65, email=0.25). No sufficient per-customer "
        f"history (need >= {MIN_OBSERVATIONS} observations, have {len(outcomes)})."
    )
