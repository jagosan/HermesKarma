# SPEC-HK-002: Dynamic Cost Estimation & Frontier Pricing Engine for HermesKarma

## 1. Context & Executive Summary
HermesKarma provides local-first telemetry, session inspection, and token analytics across local APU hardware (`chunkito`, `beehive`) and commercial cloud APIs.

Recent audit of Google AI Studio spend vs HermesKarma cost analytics revealed severe discrepancies:
- **Google AI Studio Spend (28-day billing window Aug 19 – Sep 15, 2026):**
  - `gemini-3.8-flash`: **$149.22**
  - `gemini-3.7-flash`: **$85.65**
  - Monthly Spend Cap Card: **$191.73 / $250.00**
- **HermesKarma Current Reporting:**
  - `gemini-3.8-flash`: **$123.2806** (underreported by ~$26)
  - `gemini-3.7-flash`: **$0.5119** (underreported by >$85; displayed as near $0.00)
  - Overall Dashboard Cost: **$134.17** (instead of >$235 total / $191.73 current month)

### Root Cause Analysis
1. **SQLite WAL Stale Historical Records (`session_model_usage`)**:
   Prior to 2026-09-02 (commit `b0adce1cbf` in `hermes-agent`), pricing snapshots for `gemini-3.7-flash` and `gemini-3.8-flash` were not yet registered in `agent/usage_pricing.py`. Consequently, Hermes recorded `estimated_cost_usd = 0.0` with `cost_source = 'none'` or `NULL` across 12,239 API calls for `gemini-3.7-flash` (74.3M prompt tokens, 519M cache-read tokens) and 721 calls for `gemini-3.8-flash`.
2. **Passive Aggregation in HermesKarma**:
   `hermes_reader.py` blindly executes `SUM(estimated_cost_usd)`. It does not possess its own pricing rules or dynamic reconciliation logic. When historical rows contain `0.0`, cloud tokens are displayed as `$0.00 (APU)` or sub-cent amounts.
3. **Cache Read Rate Impact**:
   Gemini 3.7 and 3.8 Flash utilize prompt caching billed at **$0.075 / 1M tokens** (10% of the uncached prompt rate of **$0.75 / 1M tokens**). Across 1.6+ billion cached tokens, this represents a major portion of real billing (~$120+) that was ignored in historical WAL logs.
4. **Lack of Monthly Spend Cap & Cycle Alignment**:
   Google AI Studio tracks spending on a calendar-month boundary (resets on 1st of month PST/PDT). HermesKarma only provided rolling windows (24h, 7d, 30d, all-time) without calendar-month spend cap tracking.

---

## 2. Technical Requirements & Architecture

### 2.1 Pricing Engine Module (`api/services/pricing_engine.py`)
A standalone pricing calculation engine with official published rate cards:
1. **Frontier Model Catalog**:
   - `gemini-3.8-flash`, `gemini-3.7-flash`:
     - Input: `$0.75` per 1M tokens (`0.00000075`)
     - Output: `$3.75` per 1M tokens (`0.00000375`)
     - Cache Read: `$0.075` per 1M tokens (`0.000000075`)
     - Cache Write: `$0.00`
   - `gemini-3.6-flash`: Input `$1.50`, Output `$7.50`, Cache Read `$0.15` per 1M
   - `gemini-3.5-flash`: Input `$1.50`, Output `$9.00`, Cache Read `$0.15` per 1M
   - `gemini-3.5-flash-lite`: Input `$0.30`, Output `$2.50`, Cache Read `$0.03` per 1M
   - `gemini-3.1-pro`: Input `$2.00` ($4.00 >200k), Output `$12.00` ($18.00 >200k), Cache Read `$0.20` ($0.40 >200k)
   - Anthropic Claude: `claude-opus-4-8` ($5/$25/$0.50/$6.25), `claude-sonnet-4-6` & `claude-sonnet-5` ($3/$15/$0.30/$3.75), `claude-haiku-4-5` ($1/$5/$0.10/$1.25)
   - OpenAI: `gpt-5.6-sol` ($5/$30), `gpt-5.6-terra` ($2.50/$15), `gpt-5.6-luna` ($1/$6), `gpt-4o` ($2.50/$10)
   - DeepSeek: `deepseek-v4-flash` ($0.15/$0.60/$0.003), `deepseek-v4-pro` ($0.66/$1.98/$0.022)
   - Local APU / Self-Hosted: `qwen3-coder-next:*`, `qwen3.8-flash-next:*`, `deepseek-v4:96k`, `gemma4:*`, `gpt-oss:*`, etc. -> Fixed **$0.00 (Local APU)**.
2. **Dynamic Reconciliation Logic**:
   - `calculate_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens) -> Decimal`
   - `reconcile_usage(row) -> dict`:
     - If `is_local`: cost is strictly `$0.00`.
     - If `not is_local`:
       - If stored `estimated_cost_usd > 0`: evaluate if stored cost aligns with token math; if within reasonable bounds, accept or provide reconciled figure.
       - If stored `estimated_cost_usd == 0` (or null) and `(input_tokens + output_tokens) > 0`: dynamically compute the correct cost from the pricing card and mark `reconciled = True`.

### 2.2 HermesReader Integration (`api/services/hermes_reader.py`)
1. In `get_analytics_overview(time_range)`:
   - For all model rows in `session_model_usage` (and `sessions` fallback):
     - Compute accurate reconciled costs.
     - Return `total_estimated_cost_usd` reflecting actual spend.
     - Add `reconciled_cost_usd` and `reconciliation_delta_usd`.
     - In `model_rows` / `enhanced_models`, include `reconciled: bool` flag and `cost_usd`.
   - Add **Calendar Month Spend Metric** (`current_month_cost_usd`, `spend_cap_usd = 250.0`, `spend_cap_pct`).
   - Add time range support for `"month"` (current calendar month in user's timezone) alongside `"today"`, `"7d"`, `"30d"`, `"all"`.
2. In `get_sessions()` and `get_session_by_id()`:
   - If a session's recorded `estimated_cost_usd` is 0 but tokens > 0 on a cloud model, show the dynamically reconciled cost so individual session drill-downs reflect honest pricing.

### 2.3 Frontend Enhancements (`api/static/index.html` & `app.js`)
1. **Top Metric Cards**:
   - Update `Cost & Zero-Cost APU` card to display total reconciled spend.
   - Add a sub-badge or secondary indicator showing monthly cycle spend (e.g. `$191.73 / $250.00 cap`).
2. **Comprehensive Multi-Model Usage Breakdown Table**:
   - For rows where cost was reconciled from zero (like `gemini-3.7-flash`), display the verified cost with a `⚡ Reconciled` tag next to it.
   - For local APU models, keep the clean green `$0.00 (APU)` tag.
3. **Time Filter Controls**:
   - Add `"This Month"` pill button next to `Today (24h)`, `Past 7 Days`, `Past 30 Days`, `All Time`.

---

## 3. Verification & Acceptance Criteria
1. **Reconciliation Accuracy**:
   - `gemini-3.7-flash` cost reflects **~$85.40 - $85.65** across the historical usage window instead of **$0.5119**.
   - `gemini-3.8-flash` cost reflects **~$149.22 - $150.79** instead of **$123.28**.
   - Total cloud API spend aligns with Google AI Studio invoice spend (~$191 current month, ~$235+ all-time).
2. **Local Zero-Cost Invariance**:
   - Local hardware models (`qwen3-coder-next:q8_0`, `qwen3.8-flash-next:262k`, `deepseek-v4:96k`) remain strictly **$0.00 (APU)** and contribute 100% to local free token savings.
3. **Automated Test Suite**:
   - All tests in `tests/test_karma.py` pass cleanly (`PYTHONPATH=. .venv/bin/pytest`).
   - New unit tests validating `PricingEngine`, cost calculations with cache reads, and dynamic reconciliation.
