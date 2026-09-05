"""
Tier-1 deterministic diagnosis: direct mapping for unambiguous failure codes.

Uses real Razorpay failure_code values from:
  https://razorpay.com/docs/errors/payments/list/
"""

from __future__ import annotations

from typing import Optional

# Diagnosis result type
DiagnosisResult = dict  # {root_cause, confidence, reasoning, recommended_action, recommended_delay_minutes}

# ── Tier-1 direct mappings (unambiguous cases) ────────────────────────

DETERMINISTIC_MAP: dict[str, DiagnosisResult] = {
    "insufficient_funds": {
        "root_cause": "insufficient_funds",
        "confidence": 1.0,
        "reasoning": "Customer's account/card has insufficient balance. This is a definitive customer-side issue per Razorpay error classification.",
        "recommended_action": "retry_later",
        "recommended_delay_minutes": 240,  # retry after a few hours
    },
    "card_expired": {
        "root_cause": "expired_card",
        "confidence": 1.0,
        "reasoning": "Customer's card has expired. A retry with the same card will fail again. Send a payment link so they can use an updated card or alternate method.",
        "recommended_action": "send_payment_link",
        "recommended_delay_minutes": 0,
    },
    "authentication_failed": {
        "root_cause": "otp_or_3ds_auth_issue",
        "confidence": 0.9,
        "reasoning": "OTP or 3DS authentication failed. Often a transient issue (typo, timeout). Immediate retry is appropriate.",
        "recommended_action": "retry_immediately",
        "recommended_delay_minutes": 0,
    },
    "incorrect_otp": {
        "root_cause": "otp_entry_error",
        "confidence": 0.9,
        "reasoning": "Customer entered incorrect OTP. Likely a typo. Immediate retry is the standard recovery path.",
        "recommended_action": "retry_immediately",
        "recommended_delay_minutes": 0,
    },
}

# Codes that are AMBIGUOUS and must be routed to Tier-2 (LLM diagnosis)
# because they could be one-off blips OR part of a systemic outage.
AMBIGUOUS_CODES = {
    "bank_technical_error",
    "payment_timed_out",
    "bank_not_available",
    "gateway_technical_error",
}


def diagnose_tier1(failure_code: str) -> Optional[DiagnosisResult]:
    """
    Attempt Tier-1 deterministic diagnosis.

    Returns DiagnosisResult if the failure_code is unambiguous,
    or None if it needs Tier-2 (LLM) diagnosis.
    """
    if failure_code in DETERMINISTIC_MAP:
        return {**DETERMINISTIC_MAP[failure_code]}  # return a copy
    return None


def is_ambiguous(failure_code: str) -> bool:
    """Check if a failure code requires Tier-2 LLM diagnosis."""
    return failure_code in AMBIGUOUS_CODES
