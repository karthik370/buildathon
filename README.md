# RevenueGuard — AI Revenue Recovery Agent

> **Built for the Razorpay Buildathon 2026**

RevenueGuard is an end-to-end AI agent that detects revenue at risk across four merchant surfaces — failed payments, abandoned checkouts, silent subscription renewal failures, and overdue invoices — diagnoses each case using a tiered deterministic + LLM approach, selects the highest expected-value recovery action within strict safety bounds, executes it (including **real Razorpay API calls in test mode**), logs a complete audit trail, and measures lift against a holdout baseline.

---

## Results (reproducible — `random.seed(42)`)

| | Control (holdout — no agent) | Treatment (agent-assisted) |
|---|---|---|
| Events | 26 | 64 |
| Recovered | 3 **(11.5%)** | 32 **(50.0%)** |
| Amount recovered | ₹25,294.91 | ₹47,940.41 |
| Amount at risk (pool) | ₹3,40,636.90 | ₹8,87,224.64 |

**Net lift: +38.5 percentage points · ₹3,41,240 projected incremental recovery**

> Breakdown by surface: `HARD_PAYMENT_FAILURE` 58% · `SILENT_RENEWAL_FAILURE` 89% · `CHECKOUT_ABANDONMENT` 50% · `OVERDUE_RECEIVABLE` 0% (deliberate — see Safety Gates)

---

## Architecture

```
                              REVENUEGUARD PIPELINE
+--------------------------------------------------------------------+
|                                                                    |
|  [CSV Data]  →  [DETECTOR]     →  [DIAGNOSIS]   →  [DECISION]     |
|   5 tables       ingestion         Tier 1: map       candidates    |
|   80 customers   rules             Tier 2: LLM       EV scoring    |
|   143 payments   anomaly           confidence        ranking       |
|   103 checkouts  triage            override                        |
|   40 subs        suppression                                       |
|   35 invoices                                                      |
|                                                                    |
|  →  [SAFETY GATES]  →  [EXECUTION]         →  [AUDIT]             |
|      6 hard limits      Real Razorpay API       SQLite             |
|      per-case checks    test-mode calls         JSON timeline      |
|      amount threshold   OR Simulated            per-case           |
|                         (retry actions)                            |
|                                                                    |
|  →  [HUMAN APPROVAL QUEUE]   →  [MEASUREMENT]                     |
|      FastAPI + Jinja2 UI          70/30 holdout                    |
|      Approve → real API call      baseline vs agent lift           |
|      Reject  → close + log        statistical significance         |
|                                                                    |
+--------------------------------------------------------------------+
                             ↓
              [FastAPI Dashboard — localhost:8000]
```

---

## Full Feature Breakdown

### 1 · Detection (5-Stage Pipeline)

| Stage | What it does |
|-------|-------------|
| **Ingestion** | Loads 5 CSV tables (customers, payments, checkouts, subscriptions, invoices) |
| **Rules** | Flags hard failures: `failure_code`, `status=failed`, overdue dates |
| **Anomaly detection** | Replay-based streaming — sorts events by timestamp, replays in 30-min clock steps, detects UPI spike within a trailing 3-hour window at `2026-08-31 23:11:03` (3.19× baseline) |
| **Triage** | Scores each flagged event by urgency and amount |
| **Suppression** | Deduplicates, removes already-resolved or recently-contacted cases |

---

### 2 · Diagnosis (Tiered LLM)

**Tier 1 — Deterministic map** (confidence = 1.0, no LLM quota used):
- Clear failure codes → direct root-cause: `insufficient_funds`, `card_expired`, `otp_entry_error`, etc.

**Tier 2 — LLM via OpenRouter** (ambiguous codes):
- Model: [`minimax/minimax-m3:free`](https://openrouter.ai/minimax/minimax-m3:free) (1M context, JSON output)
- Backup: `z-ai/glm-5.2:free` (activated on 429/5xx from primary)
- Retry with exponential backoff: 2s → 4s before switching backup
- Returns structured JSON: `{ root_cause, recommended_action, confidence, reasoning }`
- **Hard confidence override**: `confidence < 0.5` → force `escalate_human` (enforced in code, not prompt)

---

### 3 · Decision Engine (EV Scoring)

```
EV = P_success(action, root_cause) × amount_at_risk − cost − annoyance_penalty
```

- P_success priors start hand-set in `decision/scoring.py`
- **Feedback loop**: After each run, observed `(root_cause, action, outcome)` triples update priors for cells with ≥ 3 observations

---

### 4 · Safety Gates (6 Hard Limits)

Every case passes through 6 independently-tested pure-function gates **before** any action fires:

| Gate | Threshold | Fires (real run) | Effect |
|------|-----------|-----------------|--------|
| `check_max_retries` | 3 attempts | 1× | Block auto-retry, escalate |
| `check_daily_contact_cap` | 2/day | 2× | Block further outreach |
| `check_amount_requires_approval` | **₹5,000+** | **14×** | → Human approval queue |
| `check_disputed_or_fraud_flag` | Any dispute | 1× | Block all auto-actions |
| `check_cooldown` | 15-min cooldown | 1× | Delay execution |
| `check_stale` | 30-day max age | 1× | Escalate for review |

> **All 6 gates verified firing on real data** in a full pipeline run — not just in unit tests.

---

### 5 · Execution — Real Razorpay API Integration

The execution layer has a swappable interface (`ActionExecutor` ABC in `execution/executor_interface.py`). Two concrete implementations:

#### `RazorpayTestModeExecutor` — Real API calls

| Action | API call | Status |
|--------|----------|--------|
| `send_payment_link` | `POST /v1/payment_links` | ✅ **REAL** — creates live test-mode link, returns `rzp.io/…` short URL |
| `offer_alt_method` | `POST /v1/orders` | ✅ **REAL** — creates test-mode order for alt-payment flow |
| `retry_immediately` / `retry_later` | None | ⚠️ **Simulated** — structural industry limitation: no merchant API to force-retry a customer's payment (only the customer can retry their own checkout) |
| `escalate_human` | None | Routes to approval queue UI |

**Verified payment links created from a real pipeline run:**

| Entity ID | Amount | Razorpay ID | Short URL |
|-----------|--------|-------------|-----------|
| `txn_00082` | ₹412.16 | `plink_TYPzPbE1QtA6pN` | https://rzp.io/rzp/Y1OV6oE |
| `sess_00058` | ₹2,830.26 | `plink_TYPzREA274B5dv` | https://rzp.io/rzp/1JDiGuCE |
| `sess_00030` | ₹623.47 | `plink_TYPzSa0y2pjmEW` | https://rzp.io/rzp/2rt44O3 |
| `sess_00041` | ₹244.85 | `plink_TYPzUByj0w1eqR` | https://rzp.io/rzp/uat7ub8f |
| `sess_00029` | ₹709.54 | `plink_TYPzWF7g7zOqza` | https://rzp.io/rzp/S7JwacP |
| `txn_00125` | ₹795.93 | `plink_TYQ0U8YsH3PnYc` | https://rzp.io/rzp/VOdItGw |
| `txn_00116` | ₹839.18 | `plink_TYQ0VQltONhwTJ` | https://rzp.io/rzp/f9IKvYc |
| `sess_00068` | ₹212.61 | `plink_TYQ0WlqjNGg6Lg` | https://rzp.io/rzp/coBafpXJ |
| `sess_00010` | ₹2,157.50 | `plink_TYQ0ZJDgUQzF2e` | https://rzp.io/rzp/ukI0hYgO |
| `sess_edge_01` | ₹2,100.00 | `plink_TYQ1Yqjc3KzmF9` | https://rzp.io/rzp/XBzremEr |

> **Honest outcome note:** `payment_link_sent` ≠ `recovered`. Creating a link means the URL exists on Razorpay's servers and is ready for the customer to pay. Actual recovery only happens when the customer clicks and completes payment — which cannot happen inside a batch pipeline run. The dashboard reports `payment_link_sent (pending)` for real-mode links, not "recovered".

**Rate-limit handling:**  
Razorpay's test-mode Payment Links API has a burst rate limit. The executor adds a 0.6s pre-call delay + a 5s backoff retry on 429. When both are exhausted, it falls back to `offer_alt_method` (Orders API — higher limit) and logs the event explicitly.

#### `SimulatedExecutor` — default for pipeline runs

- Preserves Razorpay API quota during automated batch runs
- Uses deterministic SHA-256 keyed simulation against hand-set P_success priors
- Returns `recovered` or `failed` outcomes for all actions
- **The Approve button in the dashboard always bypasses this — it always calls the real Razorpay API**

---

### 6 · Human Approval Queue — Full Flow

Cases where any safety gate blocks auto-execution are routed to a **human-review queue** instead of being dropped.

**What gets queued:**
- Any case where `check_amount_requires_approval` fires (₹5,000+)
- Any case where `confidence < 0.5` (low-certainty diagnosis)
- Any case where both primary and fallback actions fail (`escalated_after_failures`)

**Dashboard UI (`/approvals`):**
- Lists all pending escalated cases sorted by amount at risk (highest first)
- Shows: event type, entity ID, customer, root cause, AI reasoning, chosen action, blocking gate
- Two buttons per case:

| Action | What happens |
|--------|-------------|
| ✅ **Approve** | Calls `POST /approvals/{case_id}/approve` → fires **real Razorpay API** (`send_payment_link` or `offer_alt_method`) → creates live `rzp.io/…` link → appends `human_review` stage to audit trail |
| ❌ **Reject** | Calls `POST /approvals/{case_id}/reject` → closes case with logged reason → no further automated action |

**Approval flow internals:**
- Always uses `RazorpayTestModeExecutor` — regardless of `EXECUTOR_MODE` env var
- Re-executes the originally chosen action, bypassing **only** the gate that blocked it (all other gates remain)
- Full audit trail stage appended: reviewer ID, decision, re-executed action, outcome, amount

---

### 7 · Audit Trail

Every case gets a complete immutable JSON timeline stored in SQLite:

```
detect → diagnose → decide → gate_check → execute → [human_review]
```

Each stage records timestamp, all inputs, outputs, confidence, gate results, and API response details. Accessible via:
- Dashboard case-detail view (`/cases/{id}`)
- REST API (`GET /cases`, `GET /cases/{id}`)

---

### 8 · Measurement

- **70/30 holdout split** — control group never receives agent interventions
- **Metrics**: recovery rate, amount recovered, lift over baseline, projected incremental revenue
- **Statistical significance** (scaled 6× batch, seed=99):

```
Treatment: 117/378 recovered  rate=0.3095
Control:   20/162  recovered  rate=0.1235
z-statistic:  4.5536
p-value:      0.000003
95% CI on rate difference (Newcombe): (0.1117, 0.2502)
```

---

## Data Sources

The 5 synthetic CSV datasets are calibrated against **real published industry data**:

| Source | What it calibrates |
|--------|-------------------|
| [Razorpay Error Codes](https://razorpay.com/docs/errors/payments/list/) | Every `failure_code` and `failure_source` value in the payments dataset |
| NPCI decline-type split | **80% business declines** (balance/limit) vs **20% technical declines** (gateway/timeout) |
| NPCI auto-debit/mandate data | Subscription renewal failure rate (~30–45% real, ~30% in our data) |

**Dataset scale:**

| Table | Rows |
|-------|------|
| `customers.csv` | 80 |
| `payment_transactions.csv` | 143 |
| `checkout_sessions.csv` | 103 |
| `subscriptions.csv` | 40 |
| `invoices.csv` | 35 |

> Payment failure volume (~22–55%) is elevated above real NPCI aggregate rates (~9–10%) deliberately — to ensure enough at-risk cases in a small synthetic batch. The **relative distribution across failure types** matches real NPCI-published ratios.

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/karthik370/buildathon.git
cd buildathon
pip install -r revenueguard/requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — add OPENROUTER_API_KEY and RAZORPAY_TEST_KEY_ID / SECRET

# 3. Run full pipeline
cd revenueguard
python scripts/run_full_pipeline.py

# 4. Run tests (28 gate tests)
pytest tests/ -v

# 5. Launch dashboard
python api/main.py
# Open http://localhost:8000            — main dashboard
# Open http://localhost:8000/approvals  — human approval queue
```

### Environment Variables

| Variable | Required | Where to get it |
|----------|----------|----------------|
| `OPENROUTER_API_KEY` | Yes (for Tier-2 LLM) | [openrouter.ai/keys](https://openrouter.ai/keys) — free, no credit card |
| `RAZORPAY_TEST_KEY_ID` | Yes (for real API calls) | [dashboard.razorpay.com → Settings → API Keys](https://dashboard.razorpay.com/#/app/keys) |
| `RAZORPAY_TEST_KEY_SECRET` | Yes | Same as above |
| `EXECUTOR_MODE` | No | `simulated` (default) or `razorpay_test` |

> The **Approve button** in the dashboard always uses the real Razorpay API regardless of `EXECUTOR_MODE`.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Main dashboard UI |
| `POST` | `/run` | Trigger full pipeline |
| `GET` | `/summary` | Metrics JSON |
| `GET` | `/cases` | All cases list |
| `GET` | `/cases/{id}` | Full audit timeline for one case |
| `GET` | `/approvals` | Human approval queue UI |
| `GET` | `/approvals/list` | Escalated cases as JSON |
| `POST` | `/approvals/{id}/approve` | Approve → fires real Razorpay API |
| `POST` | `/approvals/{id}/reject` | Reject → close case with logged reason |

---

## Project Structure

```
revenueguard/
├── data/               CSV datasets + data generator
├── detector/           5-stage detection pipeline
├── diagnosis/          Tiered diagnosis (deterministic + LLM)
├── decision/           EV scoring + P_success priors + feedback loop
├── execution/
│   ├── gates.py                  6 safety gate pure functions
│   ├── executor_interface.py     ActionExecutor ABC
│   ├── executors.py              SimulatedExecutor
│   ├── razorpay_test_executor.py RazorpayTestModeExecutor (real API)
│   ├── execution_engine.py       Gate → execute → fallback orchestration
│   └── channel_history.py        Multi-channel contact tracking
├── audit/
│   ├── audit_log.py    SQLite timeline storage
│   └── approvals.py    Escalation queue logic
├── measurement/        Holdout split + metrics + significance test
├── api/                FastAPI app (dashboard + approvals REST API)
├── dashboard/          Jinja2 templates + CSS
├── tests/              pytest suite — 28 gate tests
└── scripts/            Pipeline runner + verification scripts
```

---

## Honest Limitations

- `retry_immediately` / `retry_later` are **simulated** — no payment network exposes a merchant-facing retry API; only customers can retry their own checkout. This is stated plainly in `execution/razorpay_test_executor.py`.
- Significance result (p=0.000003) is on **scaled synthetic data** — speaks to sample-size adequacy, not real-world generalizability.
- P_success priors are hand-set — feedback loop exists and updates them per batch, but observed rates on small N are overfit to simulator randomness.
- Streaming anomaly detection is **replay-based** — replacing the replay loop with a Kafka/Pub-Sub consumer would produce identical detection with no code changes.

---

## What's Next

1. **Live keys** — swap `rzp_test_*` for live credentials in `.env`; zero other code changes needed (ActionExecutor ABC is already the swap point)
2. **Calibrated priors** — replace simulator outcomes with real production recovery data, retrain P_success priors on ≥ 30 observations per (root_cause, action) cell
3. **Live streaming** — replace the replay loop in `detector/anomaly.py` with a Kafka/Pub-Sub consumer
4. **Production A/B** — scale holdout to production traffic with power analysis for the target effect size

---

*Built with Python · FastAPI · SQLAlchemy · OpenRouter (`minimax-m3:free`) · Jinja2 · Razorpay Test Mode API*
