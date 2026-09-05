"""
Tier-2 LLM diagnosis using OpenRouter (OpenAI-compatible API).

Provider:  OpenRouter (https://openrouter.ai/api/v1/chat/completions)
Model:     minimax/minimax-m3:free  (primary)
           z-ai/glm-5.2:free        (backup — swap in if primary hits rate limits)

Why OpenRouter + free model:
  • Zero cost — no credit card required, no per-token billing.
  • minimax/minimax-m3:free has a 1M-token context window, is built for
    structured JSON output and multi-step agentic reasoning, and is the
    most-used free model on the OpenRouter platform.
  • Suitable for a hackathon build where reproducibility and zero-cost
    setup matter more than frontier model quality.

Environment variable: OPENROUTER_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Diagnosis result schema
DiagnosisResult = dict

# ── Model configuration ────────────────────────────────────────────────
PRIMARY_MODEL = "minimax/minimax-m3:free"
BACKUP_MODEL = "z-ai/glm-5.2:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 30  # seconds

# ── Retry configuration ───────────────────────────────────────────────
# Retry policy: up to MAX_RETRIES_PER_MODEL attempts per model with
# exponential backoff before switching to the backup model.
# Only escalate to human after ALL retries on ALL models are exhausted.
MAX_RETRIES_PER_MODEL = 2          # attempts per model (1 initial + 1 retry)
RETRY_BACKOFF_SECONDS = [2, 4]     # wait times between attempts
# Transient errors eligible for retry (do NOT retry on 4xx auth/quota errors)
_RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

# ── JSON schema embedded in prompt (response_format alone isn't enough) ─
_SCHEMA = """
{
  "root_cause": "<string>",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<string>",
  "recommended_action": "<one of: retry_immediately | retry_later | send_payment_link | offer_alt_method | escalate_human>",
  "recommended_delay_minutes": <integer>
}
"""


def _build_prompt(case_file: dict) -> str:
    """Build the diagnosis prompt with context, few-shot examples, and a
    strict JSON-only instruction at both the top and bottom of the message.

    Free models are less consistent about honouring response_format than
    paid frontier models, so the explicit instruction is a required backstop.
    """
    event = case_file["event"]
    customer = case_file["customer_history"]
    systemic = case_file["systemic_context"]

    prompt = f"""Return ONLY valid JSON matching this exact schema, no other text, no markdown code fences, no explanations:

{_SCHEMA}

You are a payment failure diagnosis agent for an Indian payments platform (Razorpay).
Analyze this payment failure case and determine the root cause and recommended recovery action.

## CASE FILE

**Event:**
- Event ID: {event['event_id']}
- Event Type: {event['event_type']}
- Amount at Risk: Rs.{event['amount_at_risk']:,.2f}
- Failure Code: {event['context'].get('failure_code', 'N/A')}
- Failure Source: {event['context'].get('failure_source', 'N/A')}
- Payment Method: {event['context'].get('payment_method', 'N/A')}
- Timestamp: {event['context'].get('timestamp', 'N/A')}

**Customer History:**
- Total Transactions: {customer['total_transactions']}
- Total Failures: {customer['total_failures']}
- Failure Rate: {customer['failure_rate']:.1%}
- Is Repeat Failer: {customer['is_repeat_failer']}
- Preferred Method: {customer['preferred_method']}

**Systemic Context:**
- Is Anomaly Window: {systemic['is_anomaly_window']}
- Anomaly Flags: {json.dumps(systemic['anomaly_flags'], indent=2) if systemic['anomaly_flags'] else 'None'}

## CRITICAL INSTRUCTION

If the failure_code is one of [bank_technical_error, bank_not_available, gateway_technical_error, payment_timed_out] AND the systemic context shows is_anomaly_window=True, this strongly suggests a bank-side or gateway outage.
In that case:
- root_cause should be "bank_side_outage" or similar
- recommended_action should be "retry_later"
- recommended_delay_minutes should be 60-120 (wait for outage to resolve)
- Do NOT blame the customer

If is_anomaly_window=False AND the customer also has no prior failure history (low failure_rate, is_repeat_failer=False), this is genuinely ambiguous — a one-off technical blip could be bank-side or could be a transient network issue. Reflect that uncertainty with a lower confidence score.

If is_anomaly_window=False, the failure is likely a one-off transient error.

## FEW-SHOT EXAMPLES

Example 1: bank_technical_error during anomaly window
{{"root_cause": "bank_side_outage", "confidence": 0.85, "reasoning": "bank_technical_error during a detected anomaly window where failure rate spiked. This is a systemic bank-side issue, not customer-caused.", "recommended_action": "retry_later", "recommended_delay_minutes": 90}}

Example 2: payment_timed_out, no anomaly
{{"root_cause": "transient_network_timeout", "confidence": 0.65, "reasoning": "Isolated timeout without systemic pattern. Likely a one-off network glitch between customer's bank and payment gateway.", "recommended_action": "retry_immediately", "recommended_delay_minutes": 5}}

Example 3: bank_not_available, no anomaly, first-time failer
{{"root_cause": "unclear_bank_issue", "confidence": 0.42, "reasoning": "Bank reported unavailable but no broader outage pattern detected and customer has no failure history. Could be scheduled maintenance or an isolated incident. Insufficient signal to auto-retry confidently.", "recommended_action": "escalate_human", "recommended_delay_minutes": 0}}

Remember: Return ONLY valid JSON matching the schema above. No markdown, no preamble, no trailing text."""
    return prompt


def call_llm_diagnosis(prompt: str, model: str = PRIMARY_MODEL) -> dict:
    """
    Make a single OpenRouter API call and return the parsed JSON dict.

    Raises:
        ValueError  – if the response cannot be parsed as valid JSON
        requests.HTTPError – on HTTP 4xx/5xx
        requests.Timeout   – on network timeout
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise EnvironmentError("OPENROUTER_API_KEY environment variable is not set")

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    raw_text = response.json()["choices"][0]["message"]["content"]
    logger.debug("[LLM Diagnosis] Raw response content: %s", raw_text)
    print(f"[LLM Diagnosis] Raw response from {model}: {raw_text}")

    # Strip markdown code fences if the model added them despite our instruction
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned

    parsed = json.loads(cleaned)
    return parsed


def diagnose_tier2(case_file: dict) -> Optional[DiagnosisResult]:
    """
    Call OpenRouter for Tier-2 diagnosis.

    Retry policy (exponential backoff):
      - Each model gets up to MAX_RETRIES_PER_MODEL attempts with exponential
        backoff (RETRY_BACKOFF_SECONDS) before switching to the backup model.
      - Retryable errors: 429, 5xx, timeout.
      - Non-retryable: 4xx auth/validation errors, bad JSON → escalate immediately.
      - Only escalate_human after ALL attempts on ALL models are exhausted.

    This means a single transient rate-limit blip retries silently without
    burning the backup-model fallback path.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("[LLM Diagnosis] WARNING: OPENROUTER_API_KEY not set. Using fallback diagnosis.")
        return _fallback_diagnosis(case_file)

    prompt = _build_prompt(case_file)

    for model in (PRIMARY_MODEL, BACKUP_MODEL):
        for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
            attempt_label = f"{model} attempt {attempt}/{MAX_RETRIES_PER_MODEL}"
            try:
                result = call_llm_diagnosis(prompt, model=model)

                # Validate required fields
                required = {"root_cause", "confidence", "reasoning",
                            "recommended_action", "recommended_delay_minutes"}
                missing = required - set(result.keys())
                if missing:
                    raise ValueError(f"LLM response missing fields: {missing}")

                # Clamp confidence to [0.0, 1.0]
                result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

                # Validate recommended_action
                valid_actions = {
                    "retry_immediately", "retry_later", "send_payment_link",
                    "offer_alt_method", "escalate_human",
                }
                if result["recommended_action"] not in valid_actions:
                    result["recommended_action"] = "escalate_human"
                    result["reasoning"] += " [CORRECTED: invalid recommended_action from LLM]"

                print(f"[LLM Diagnosis] SUCCESS via {model} | "
                      f"root_cause={result['root_cause']} | "
                      f"confidence={result['confidence']:.2f} | "
                      f"action={result['recommended_action']}")
                return result

            except requests.exceptions.Timeout:
                reason = f"Timeout after {REQUEST_TIMEOUT}s ({attempt_label})"
                print(f"[LLM Diagnosis] ERROR: {reason}")
                is_last_attempt = (attempt == MAX_RETRIES_PER_MODEL)
                is_last_model = (model == BACKUP_MODEL)
                if not is_last_attempt:
                    delay = RETRY_BACKOFF_SECONDS[attempt - 1]
                    print(f"[LLM Diagnosis] RETRY: waiting {delay}s before attempt {attempt + 1} on {model}")
                    time.sleep(delay)
                    continue
                if not is_last_model:
                    print(f"[LLM Diagnosis] {model} retries exhausted, switching to {BACKUP_MODEL}")
                    break  # try backup model
                return _escalate_on_failure(case_file, reason)

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                reason = f"HTTP {status} from OpenRouter ({attempt_label})"
                print(f"[LLM Diagnosis] ERROR: {reason}: {e}")

                is_retryable = status in _RETRYABLE_HTTP_STATUSES
                is_last_attempt = (attempt == MAX_RETRIES_PER_MODEL)
                is_last_model = (model == BACKUP_MODEL)

                if is_retryable and not is_last_attempt:
                    delay = RETRY_BACKOFF_SECONDS[attempt - 1]
                    print(f"[LLM Diagnosis] RETRY: transient {status}, "
                          f"waiting {delay}s before attempt {attempt + 1} on {model}")
                    time.sleep(delay)
                    continue
                if is_retryable and not is_last_model:
                    print(f"[LLM Diagnosis] {model} retries exhausted, switching to {BACKUP_MODEL}")
                    break  # try backup model
                # Non-retryable (e.g., 401 auth) or all models exhausted
                return _escalate_on_failure(case_file, reason)

            except (json.JSONDecodeError, ValueError) as e:
                reason = f"Failed to parse LLM JSON response ({attempt_label}): {e}"
                print(f"[LLM Diagnosis] ERROR: {reason}")
                # JSON parse failures are not transient — escalate immediately
                return _escalate_on_failure(case_file, reason)

            except Exception as e:
                reason = f"Unexpected error ({attempt_label}): {type(e).__name__}: {e}"
                print(f"[LLM Diagnosis] ERROR: {reason}")
                is_last_attempt = (attempt == MAX_RETRIES_PER_MODEL)
                is_last_model = (model == BACKUP_MODEL)
                if not is_last_attempt:
                    delay = RETRY_BACKOFF_SECONDS[attempt - 1]
                    print(f"[LLM Diagnosis] RETRY: waiting {delay}s before attempt {attempt + 1} on {model}")
                    time.sleep(delay)
                    continue
                if not is_last_model:
                    print(f"[LLM Diagnosis] {model} retries exhausted, switching to {BACKUP_MODEL}")
                    break
                return _escalate_on_failure(case_file, reason)

    # All models, all retries exhausted
    return _escalate_on_failure(case_file, "All LLM models and retries exhausted")



def _escalate_on_failure(case_file: dict, reason: str) -> DiagnosisResult:
    """
    Route a case to escalate_human when LLM response is unusable.
    Confidence is set to 0.0 so the hard confidence override in
    diagnosis_engine.py will also fire as a secondary safety layer.
    """
    print(f"[LLM Diagnosis] Routing to escalate_human due to: {reason}")
    return {
        "root_cause": "llm_diagnosis_failed",
        "confidence": 0.0,
        "reasoning": (
            f"LLM Tier-2 diagnosis failed and could not be trusted. "
            f"Reason: {reason}. "
            f"Escalating to human for manual review to avoid automated action on an undiagnosed case."
        ),
        "recommended_action": "escalate_human",
        "recommended_delay_minutes": 0,
    }


def _fallback_diagnosis(case_file: dict) -> DiagnosisResult:
    """
    Conservative fallback when OPENROUTER_API_KEY is not set at all.
    Uses the systemic context to make a reasonable determination.
    """
    event = case_file["event"]
    is_anomaly = case_file["systemic_context"]["is_anomaly_window"]
    failure_code = event["context"].get("failure_code", "")

    if is_anomaly:
        return {
            "root_cause": "bank_side_outage",
            "confidence": 0.75,
            "reasoning": (
                f"Failure code '{failure_code}' occurred during a detected anomaly window. "
                f"Systemic bank/gateway issue suspected. (Fallback diagnosis — OPENROUTER_API_KEY not set)"
            ),
            "recommended_action": "retry_later",
            "recommended_delay_minutes": 90,
        }
    else:
        return {
            "root_cause": "transient_technical_error",
            "confidence": 0.55,
            "reasoning": (
                f"Failure code '{failure_code}' with no systemic pattern detected. "
                f"Likely a one-off transient issue. (Fallback diagnosis — OPENROUTER_API_KEY not set)"
            ),
            "recommended_action": "retry_later",
            "recommended_delay_minutes": 30,
        }
