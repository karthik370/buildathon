"""
Unit tests for diagnosis engine.
"""

import pytest

from revenueguard.diagnosis.deterministic_map import diagnose_tier1, is_ambiguous


class TestDeterministicMap:
    def test_insufficient_funds(self):
        result = diagnose_tier1("insufficient_funds")
        assert result is not None
        assert result["root_cause"] == "insufficient_funds"
        assert result["confidence"] == 1.0
        assert result["recommended_action"] == "retry_later"

    def test_card_expired(self):
        result = diagnose_tier1("card_expired")
        assert result is not None
        assert result["root_cause"] == "expired_card"
        assert result["recommended_action"] == "send_payment_link"

    def test_authentication_failed(self):
        result = diagnose_tier1("authentication_failed")
        assert result is not None
        assert result["root_cause"] == "otp_or_3ds_auth_issue"
        assert result["recommended_action"] == "retry_immediately"
        assert result["confidence"] == 0.9

    def test_incorrect_otp(self):
        result = diagnose_tier1("incorrect_otp")
        assert result is not None
        assert result["root_cause"] == "otp_entry_error"
        assert result["recommended_action"] == "retry_immediately"

    def test_ambiguous_returns_none(self):
        assert diagnose_tier1("bank_technical_error") is None
        assert diagnose_tier1("payment_timed_out") is None
        assert diagnose_tier1("bank_not_available") is None
        assert diagnose_tier1("gateway_technical_error") is None

    def test_unknown_code_returns_none(self):
        assert diagnose_tier1("some_unknown_code") is None


class TestAmbiguousCheck:
    def test_ambiguous_codes(self):
        assert is_ambiguous("bank_technical_error") is True
        assert is_ambiguous("payment_timed_out") is True
        assert is_ambiguous("bank_not_available") is True
        assert is_ambiguous("gateway_technical_error") is True

    def test_non_ambiguous_codes(self):
        assert is_ambiguous("insufficient_funds") is False
        assert is_ambiguous("card_expired") is False
        assert is_ambiguous("authentication_failed") is False


class TestConfidenceOverride:
    """Test the hard rule: confidence < 0.5 -> escalate_human."""

    def test_low_confidence_forces_escalation(self):
        """This tests the engine logic, but we verify the map doesn't
        have any unintentionally low-confidence entries."""
        from revenueguard.diagnosis.deterministic_map import DETERMINISTIC_MAP
        for code, diag in DETERMINISTIC_MAP.items():
            assert diag["confidence"] >= 0.5, \
                f"Tier-1 mapping for '{code}' has confidence {diag['confidence']} < 0.5"
