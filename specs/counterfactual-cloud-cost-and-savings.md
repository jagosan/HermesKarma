# SPEC-HK-003: Counterfactual Cloud Cost & Hardware Savings Engine ("Shadow Cost") for HermesKarma

> **Target Systems:** `pricing_engine.py`, `hermes_reader.py`, `analytics.py`, `app.js`, `index.html`.  
> **Platforms:** HermesKarma Web Dashboard (`https://karmes.jagosan.com`, port `:8020`).  
> **Preceding Specs:** `SPEC-HK-002` (Dynamic Cost Estimation & Frontier Pricing Engine).

---

## 1. Context & Executive Summary

HermesKarma currently tracks:
1. **Actual Cloud Spend:** Reconciled billing across commercial frontier APIs (`gemini-3.8-flash`, `gemini-3.1-pro`, `claude-*`, `gpt-*`).
2. **Local APU Token Counts:** The number of prompt, cache, and output tokens processed on homelab hardware (`chunkito` AMD Strix Halo 128GB, `beehive` AMD SER8).
3. **Local APU Cost:** Billed as strictly `$0.00 (Local APU)`.

While recording `$0.00` is accurate for cash-outflow accounting, it creates a major **visibility gap**:
- When subagents (`@tigger` running `qwen3-coder-next:262k`, `@jagular` running `qwen3.8-flash-next:262k`, `@eeyore`, etc.) execute iterative coding, AST parsing, compiler loops, and regression test suites, they process tens of millions of tokens that would otherwise incur steep commercial API costs.
- The user cannot see **how much money their local hardware investment has saved**, nor can they determine the **break-even / payback timeline (ROI)** of the Minisforum MS-S1 Max APU node (~$1,800 capital expenditure).

This specification defines the **Counterfactual Cloud Cost & Hardware Savings Engine ("Shadow Cost")** for HermesKarma. It introduces dynamic counterfactual pricing, configurable frontier comparison baselines, gross avoided cloud spend tracking, and an interactive Hardware Dividend & Payback gauge within the "Tokens & Cost" view.

---

## 2. Mathematical Model & Baseline Formulations

### 2.1 Counterfactual Cost Calculation
For every model invocation $i$ executed on local hardware where $\text{is\_local}(m_i) = \text{true}$:

$$\text{Cost}_{\text{actual}, i} = \$0.00$$

The **Counterfactual Cloud Cost** (or "Shadow Cost") $\text{Cost}_{\text{shadow}, i}$ represents the expense if the identical token volume had been routed to an equivalent commercial cloud model:

$$\text{Cost}_{\text{shadow}, i} = \frac{T_{\text{input}, i} \cdot P_{\text{in}} + T_{\text{cache}, i} \cdot P_{\text{cache}} + T_{\text{output}, i} \cdot P_{\text{out}}}{10^6}$$

Where $P_{\text{in}}$, $P_{\text{cache}}$, and $P_{\text{out}}$ are the per-million token rates of the selected comparative baseline model.

### 2.2 Comparative Baseline Rate Cards
HermesKarma shall support four standard comparative presets:

| Baseline Mode | Representative Model | Input / 1M | Output / 1M | Prompt Cache / 1M | Intended Workload Comparison |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier-Mapped (Default)** | Dynamic per Persona | *Varies* | *Varies* | *Varies* | Mapped to persona capability class (see §2.3). |
| **Frontier Coding (Sonnet)**| `claude-3.7-sonnet` | **$3.00** | **$15.00** | **$0.30** | Industry standard for agentic coding & refactors. |
| **Frontier Standard (Pro)** | `gemini-3.1-pro` | **$2.00** | **$12.00** | **$0.20** | Deep reasoning, large-context synthesis ($4/$18 >200k). |
| **High-Throughput (Flash)** | `gemini-3.8-flash` | **$0.75** | **$3.75** | **$0.075** | Fast triage, unit test loops, conservative floor. |

### 2.3 Tier-Mapped Persona Equivalence (Default Mode)
When set to **Tier-Mapped**, local tokens are valued against the cloud model that matches the persona's role:

| Persona | Local Engine | Assigned Cloud Benchmark | Benchmark Rates ($/1M in / out / cache) |
| :--- | :--- | :--- | :--- |
| 🐯 **@tigger** | `qwen3-coder-next:262k` | `claude-3.7-sonnet` | $3.00 / $15.00 / $0.30 |
| 🐆 **@jagular** | `qwen3.8-flash-next:262k` (177B) | `gemini-3.1-pro` | $2.00 / $12.00 / $0.20 |
| 🫏 **@eeyore** | `qwen3.8-27b` / adversarial | `claude-3.7-sonnet` | $3.00 / $15.00 / $0.30 |
| 🐷 **@piglet** | `qwen3.5` / fast test triage | `gemini-3.8-flash` | $0.75 / $3.75 / $0.075 |
| 🐻 **@pooh** | `qwen3-coder` / doc gardening | `gemini-3.8-flash` | $0.75 / $3.75 / $0.075 |
| 🐰 **@rabbit** | `qwen3.5` / kanban decompose | `gemini-3.8-flash` | $0.75 / $3.75 / $0.075 |
| *Other Local* | Any unmapped local model | `gemini-3.8-flash` | $0.75 / $3.75 / $0.075 |

### 2.4 Aggregate Financial Metrics
For any query time range $W$ (`today`, `7d`, `30d`, `month`, `all`):

1. **Actual Cloud Spend:**
   $$\text{Spend}_{\text{actual}} = \sum_{j \in \text{Cloud}} \text{Cost}_j$$
2. **Avoided Cloud Spend (Gross Hardware Dividend):**
   $$\text{Savings}_{\text{gross}} = \sum_{k \in \text{Local}} \text{Cost}_{\text{shadow}, k}$$
3. **Gross Cloud-Equivalent Workload Value:**
   $$\text{Value}_{\text{total}} = \text{Spend}_{\text{actual}} + \text{Savings}_{\text{gross}}$$
4. **Cloud Offload Percentage:**
   $$\text{OffloadRatio} = \frac{\text{Savings}_{\text{gross}}}{\text{Value}_{\text{total}}} \times 100\%$$
5. **Hardware Capital Payback (ROI):**
   $$\text{PaybackPct} = \frac{\text{Savings}_{\text{gross, all-time}}}{\text{CapEx}_{\text{hardware}}} \times 100\% \quad (\text{CapEx} = \$1,800.00)$$

---

## 3. Architecture & Module Modifications

### 3.1 Backend: `PricingEngine` (`api/services/pricing_engine.py`)
Add methods:
```python
def calculate_counterfactual_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    persona: Optional[str] = None,
    baseline_preset: str = "tier_mapped",  # tier_mapped | sonnet | pro | flash
) -> float:
    """Calculate the estimated cloud cost had this local invocation run on commercial cloud."""
```
- Expose `get_available_baselines() -> List[Dict[str, Any]]`.
- Add baseline rate card lookup and persona tier-mapping table.

### 3.2 Backend: `HermesReader` (`api/services/hermes_reader.py`)
Enhance `get_analytics_overview(time_range, baseline_preset="tier_mapped")`:
- Compute `shadow_cost_usd` per model row.
- Aggregate:
  - `total_avoided_cost_usd`: float
  - `gross_cloud_equivalent_usd`: float
  - `cloud_offload_pct`: float
  - `hardware_capex_usd`: 1800.0
  - `hardware_payback_pct`: float
- In `model_distribution` list, attach `shadow_cost_usd` and `counterfactual_model_name` to each local model item.

### 3.3 Backend API Route (`api/routes/analytics.py`)
Update `/api/analytics/overview`:
- Query parameter: `baseline: str = Query("tier_mapped", regex="^(tier_mapped|sonnet|pro|flash)$")`.
- Hand query parameter directly into `hermes_reader.get_analytics_overview()`.

### 3.4 Frontend UI (`api/static/index.html` & `app.js`)

#### 1. Top Analytics Controls Bar
- Add a dropdown selector alongside the Time Range buttons:
  `[ Baseline: Tier-Mapped (Default) ▾ ]` with choices:
  - `Tier-Mapped (Persona-Aware)`
  - `Claude 3.7 Sonnet ($3 / $15 / $0.30)`
  - `Gemini 3.1 Pro ($2 / $12 / $0.20)`
  - `Gemini 3.8 Flash ($0.75 / $3.75 / $0.075)`

#### 2. Hero KPI Metric Cards
Add two new high-visibility cards to the Analytics tab:
- **Card A: "Cloud Spend Avoided (Hardware Dividend)"**
  - Primary text: `+$428.50` (accent emerald green).
  - Subtext: `Gross workload value: $620.23 (69.1% offloaded to APU)`.
- **Card B: "Chunkito Hardware Payback (ROI)"**
  - Primary text: `23.8% of $1,800` (with visual progress fill bar).
  - Subtext: `$1,371.50 remaining until 100% breakeven`.

#### 3. Model Breakdown Table Expansion
Update the Model Distribution table to include:
- `Model Name` (e.g. `qwen3-coder-next:262k [Local APU]`)
- `Tokens` (Input / Output / Cache)
- `Actual Spend` (`$0.00`)
- `Shadow Cloud Value` (`$341.20`)
- `Effective Benchmark` (`claude-3.7-sonnet`)

#### 4. Historical Savings & Actual Spend Chart
- Render a dual-series bar/area chart in the Daily Activity section:
  - Blue: Actual Cloud Spend.
  - Green: Avoided Cloud Spend (Local APU).
  - Visualizing the daily and cumulative savings generated by the homelab swarm.

---

## 4. Acceptance Criteria & Verification Gate

1. **Deterministic Pricing:** For a session with 1,000,000 input tokens and 100,000 output tokens on `@tigger` under `sonnet` baseline, `shadow_cost_usd` computes to exactly `$3.00 + $1.50 = $4.50`.
2. **Zero Actual Spend Mutation:** `total_actual_cost_usd` and Google spend cap indicators remain strictly unchanged; actual financial records are never modified.
3. **Dynamic Baseline Switching:** Changing the dropdown in the UI re-fetches `/api/analytics/overview?baseline=...` and updates all KPI cards and table values in real time without a page refresh.
4. **Clean Fallbacks:** If a local model does not match any specific persona pattern, it falls back safely to `gemini-3.8-flash` rates.
5. **Unit Tests Passing:** New test cases in `tests/test_karma.py` covering:
   - `calculate_counterfactual_cost()` across all 4 baseline modes.
   - Persona-to-tier mappings.
   - Payback and offload percentage edge cases (division by zero safety).
6. **Production Service Health:** `systemctl --user restart hermes-karma.service` restarts cleanly with HTTP 200 responses on `:8020`.
