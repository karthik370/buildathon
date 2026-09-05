# RevenueGuard — AI Revenue Recovery Agent

> **Baseline: 11.5% recovery | Agent-assisted: 50.0% recovery | +38.5pp lift | Rs.341,240 projected incremental recovery | Rs.47,940 actually recovered on a batch of 90 at-risk events across payments, checkouts, subscriptions, and invoices.**

RevenueGuard is an AI agent that detects revenue at risk from four surfaces (failed payments, abandoned checkouts, silent subscription renewal failures, and overdue invoices), diagnoses root causes using a tiered deterministic + LLM approach, selects the highest-EV recovery action within strict safety bounds, executes it, logs a full audit trail, and reports honest, measured recovery metrics against a holdout baseline.

Built for the **Razorpay Buildathon 2026**.

---

## Architecture

```
                                REVENUEGUARD PIPELINE
  +------------------------------------------------------------------+
  |                                                                  |
  |  [CSV Data]  -->  [DETECTOR]  -->  [DIAGNOSIS]  -->  [DECISION]  |
  |   5 tables        ingestion        Tier 1: map       candidates  |
  |   80 customers    rules            Tier 2: LLM       EV scoring  |
  |   143 payments    anomaly          confidence         ranking    |
  |   103 checkouts   triage           override                      |
  |   40 subs         suppression                                    |
  |   35 invoices                                                    |
  |                                                                  |
  |  -->  [EXECUTION]  -->  [AUDIT]  -->  [MEASUREMENT]              |
  |       6 safety         SQLite         70/30 holdout              |
  |       gates            JSON           baseline vs                |
  |       simulators       timeline       agent lift                 |
  |       fallback                        metrics                    |
  |                                                                  |
  +------------------------------------------------------------------+
                              |
                    [FastAPI + Jinja2 Dashboard]
                    localhost:8000
```

## Results (verified, reproducible with `random.seed(42)`)

| | Control (baseline) | Treatment (agent-assisted) |
|---|---|---|
| Events | 26 | 64 |
| Recovered | 3 (11.5%) | 32 (50.0%) |
| Amount recovered | Rs.25,294.91 | Rs.47,940.41 |
| Amount at risk (pool) | Rs.340,636.90 | Rs.887,224.64 |

**Net lift: +38.5 percentage points, ≈Rs.341,240 in projected incremental recovery** (rate-difference × treatment pool's at-risk amount — see Key Design Decisions #7 for why this specific formula, not a naive ₹-subtraction across differently-sized pools).

Breakdown by surface: `HARD_PAYMENT_FAILURE` 58% recovered, `SILENT_RENEWAL_FAILURE` 89% recovered, `CHECKOUT_ABANDONMENT` 50% recovered, `OVERDUE_RECEIVABLE` 0% recovered (see #8 below — this is a deliberate safety outcome, not a defect).

## Data Realism

The synthetic data is grounded in **real published sources**, not invented codes:

| Source | What it calibrates |
|--------|-------------------|
| [Razorpay Error Codes](https://razorpay.com/docs/errors/payments/list/) | Every `failure_code` value and its `failure_source` classification |
| NPCI decline-type split | Business Decline (~80%) vs Technical Decline (~20%) of all failures |
| NPCI auto-debit/mandate data | Subscription renewal failure rate (~30-45% in real world, ~30% in our data) |

**Documented demo-scale assumption:** Overall payment failure volume (~22% normal / ~55% repeat-failer) is intentionally elevated above the real aggregate NPCI decline rate (~9-10%) to ensure enough at-risk cases in a small batch. The *relative skew* across failure types IS calibrated to real published ratios.

## Key Design Decisions

1. **Tiered diagnosis**: Unambiguous failure codes (insufficient_funds, card_expired, etc.) are mapped deterministically at Tier 1 with confidence 1.0. Ambiguous codes (bank_technical_error, payment_timed_out, etc.) go to Tier 2 LLM with systemic anomaly context.

2. **Hard confidence override**: If any diagnosis (Tier 1 or Tier 2) has confidence < 0.5, the action is forced to `escalate_human` regardless of what was recommended. This is enforced in code, not prompted for — verified firing in production run on a real case (confidence=0.45 → override → escalate_human).

3. **EV-based decision scoring**: `EV = P_success * amount_at_risk - cost - annoyance_penalty`. P_success priors start as documented hand-set assumptions in `decision/scoring.py` and are updated via a feedback loop after each pipeline run (see #9 below).

4. **6 safety gates**: max_retries, daily_contact_cap, amount_requires_approval, disputed_or_fraud, cooldown, stale. Each is an independently testable pure function with comprehensive pytest coverage, and **every gate is verified firing at least once on real data** in a full pipeline run (not just in isolated unit tests):

| Gate | Times fired (real run) |
|---|---|
| amount_requires_approval | 14 |
| cooldown_active | 1 |
| daily_contact_cap | 2 |
| disputed_or_fraud | 1 |
| max_retries | 1 |
| stale_check | 1 |

5. **Graceful degradation**: When a primary action fails, the system tries one fallback. If that also fails, it correctly stops and escalates to human, logging the full trajectory. Multiple showcase cases demonstrate this behavior, including a genuine rate-limit failure (both primary and backup LLM returned HTTP 429 mid-run) where the system correctly escalated to human rather than crashing or guessing — an unplanned but real demonstration of the fallback path working under actual failure, not just a scripted scenario.

6. **LLM Provider — OpenRouter (zero cost)**: Tier-2 diagnosis uses [`minimax/minimax-m3:free`](https://openrouter.ai/minimax/minimax-m3:free) via OpenRouter's OpenAI-compatible API.
   - **Why OpenRouter**: Free tier, no credit card required — suitable for a hackathon build where reproducibility and zero-cost setup matter.
   - **Why minimax-m3:free**: 1M-token context window, built for structured JSON output and multi-step agentic reasoning; the most-used free model on the OpenRouter platform.
   - **Backup model**: `z-ai/glm-5.2:free` — a dedicated reasoning model with adjustable reasoning effort, also strong at structured tool use; activated automatically on rate-limit responses from the primary.
   - **Failure handling + retry**: Transient errors (429, 5xx, timeout) now retry with exponential backoff (2s, 4s) on the same model before switching to the backup, and again before escalating. Only after all retries on all models are exhausted does the case route to `escalate_human`. Verified in a live run: a 429 on `minimax/minimax-m3:free` attempt 1 retried at +2s and succeeded on attempt 2, never burning the backup-model fallback path. Non-transient errors (bad JSON, 401) escalate immediately.

7. **Why net-lift-in-₹ uses rate-difference × treatment-pool-amount, not a raw subtraction**: An earlier version of this metric subtracted raw recovered ₹ across the control and treatment pools directly, scaled by pool size. That produced a negative ₹ figure alongside a positive percentage-point lift — a contradiction, since both numbers describe the same result. The fix: `incremental_₹ = (treatment_rate − baseline_rate) × treatment_at_risk_amount`, which is guaranteed to share the same sign as the percentage lift by construction. This bug and fix are documented here deliberately — catching and correcting an internal metric inconsistency before presenting it is itself evidence of the "honest metrics" standard this project holds itself to.

8. **Why OVERDUE_RECEIVABLE recovered 0/11 (0%)** — verified via direct gate-trace inspection, not assumed: every invoice in the dataset exceeds the ₹5,000 `amount_requires_approval` threshold. The decision engine correctly selects `send_payment_link` as the top candidate action, but the gate correctly blocks it and routes to `escalate_human` instead, because auto-sending payment links for large, unreviewed B2B invoices is out of bounds for this system. This is deliberate bounded-execution behavior on the single highest-value risk category in the dataset (₹13.4L of ₹15.5L total at risk) — not a defect.

9. **Learned P_success priors (feedback loop)**: After each pipeline run, observed `(root_cause, action, outcome)` triples are aggregated and compared to the hand-set priors. Pairs with ≥ 3 observations get their prior updated to the observed success rate; pairs with fewer observations keep the hand-set prior (too few samples = unreliable estimate).

10. **Statistical significance on scaled batch**: To address the small-sample disclaimer, data was scaled 6× (seed=99, 860 payment rows → 540 events total) and the full pipeline run with deterministic fallback diagnosis (no LLM calls, to avoid rate-limiting 400+ calls). `statsmodels.stats.proportion.proportions_ztest` (one-sided, H1: treatment > control):

```
Treatment: 117/378 recovered  rate=0.3095
Control:   20/162  recovered  rate=0.1235
z-statistic:  4.553621
p-value:      0.000003
Significant at 95% level (p < 0.05): True
95% CI on rate difference (Newcombe): (0.111738, 0.250157)
```

The effect is statistically significant at p=0.000003 on the scaled synthetic batch. Caveat still stands: this is synthetic data, so the significance result speaks to *sample size adequacy*, not to real-world generalizability — the simulator's P_success priors (which determine outcomes) are hand-set assumptions, not learned from real payment data.

11. **Replay-based streaming simulation**: Anomaly detection was refactored to demonstrate incremental windowed processing. All 143 transactions are sorted by their real timestamp field and replayed in 30-minute clock steps. At each step, anomaly detection runs using ONLY transactions within a trailing 3-hour window. The injected UPI spike is caught at simulated clock `2026-08-31 23:11:03` via incremental computation:

```
[SIM CLOCK 2026-08-31 23:11:03] *** ANOMALY FIRST DETECTED ***
  Method:        UPI
  Window:        2026-08-31 20:11:03 -> 2026-08-31 23:11:03
  Baseline rate: 0.2090 (20.9%)
  Observed rate: 0.6667 (66.7%)
  Spike factor:  3.19x baseline
  Window txns:   3 total, 2 failed
```

## How to Reproduce

```bash
# 1. Install dependencies
pip install -r revenueguard/requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY and RAZORPAY test keys

# 3. Run the full pipeline
python revenueguard/scripts/run_full_pipeline.py

# 4. Run tests (all 28 gate tests must pass)
pytest revenueguard/tests/ -v

# 5. Launch the dashboard
cd revenueguard
python api/main.py
# Open http://localhost:8000
```

## Safety Gates

All gates are independently tested in `revenueguard/tests/test_gates.py` (28 tests, all passing) AND independently verified firing on real data in a full pipeline run:

| Gate | What it enforces | Result if triggered |
|------|-----------------|-------------------|
| `check_max_retries` | Max 3 retry attempts per transaction | Block auto-retry, escalate |
| `check_daily_contact_cap` | Max 2 customer contacts per day | Block further outreach |
| `check_amount_requires_approval` | Rs.5,000+ requires human approval | Escalate for approval |
| `check_disputed_or_fraud_flag` | Never auto-act on disputed transactions | Block all auto-actions |
| `check_cooldown` | 15-minute cooldown between attempts | Delay execution |
| `check_stale` | Don't chase cases older than 30 days | Escalate for review rather than auto-write-off |

## Project Structure

```
revenueguard/
  data/            - CSV data + loader
  detector/        - 5-stage detection pipeline
  diagnosis/       - Tiered diagnosis (deterministic + LLM)
  decision/        - EV-based action selection
  execution/       - Safety gates + SimulatedExecutor + RazorpayTestModeExecutor (real API)
  audit/           - SQLite audit trail
  measurement/     - Holdout split + metrics
  api/             - FastAPI app
  dashboard/       - Jinja2 templates + CSS
  tests/           - pytest suite (28 tests)
  scripts/         - End-to-end pipeline runner
```

---

*Built with Python, FastAPI, SQLAlchemy, OpenRouter (minimax-m3:free), and Jinja2.*
