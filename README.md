# Portfolio Buffer

**A conversational portfolio analysis agent that connects investment allocations with real-life cash needs.**

> Project status: implemented and running locally; cloud deployment pending (see *Setup and deployment status*). This README is an AI-assisted working draft for the author to review and rewrite in their own words before submission, consistent with the course's writing policy.

## The problem

A portfolio can look diversified while still leaving its owner short of cash when an expense arrives. Students and early-career investors may need to pay tuition, move apartments, or cover another large expense during a market downturn.

Portfolio Buffer helps users explore a concrete question:

**"If my investments fall in value and I need cash at the same time, what happens to my portfolio?"**

The target user is a beginner investor who holds a small portfolio of US-listed ETFs and cash. The agent explains the current allocation, calculates historical risk measures, compares a user-defined target allocation, and tests hypothetical cash needs under market shocks.

## The experience

1. Enter ETF holdings and cash balances in US dollars.
2. Ask the agent to analyze the portfolio over a historical period.
3. Compare the current portfolio with target weights you choose.
4. Specify hypothetical asset declines and a cash expense.
5. Inspect the calculations, assumptions, source dates, and tool calls behind every answer.

The agent remembers holdings and assumptions within a session, so follow-ups such as "What if I need another $1,000?" work without restating the portfolio. Separate sessions keep separate histories and portfolio state.

## Tools

All financial arithmetic runs in deterministic Python (`portfolio.py`). The model only chooses tools and explains their outputs. Every tool returns JSON; errors are returned as `{"error": {"kind": ..., "message": ...}}` with a message the model can act on, never a traceback.

### 1. `fetch_historical_prices`

Retrieve daily historical ETF prices and cache them in the session.

- **Inputs:** `symbols` (list of tickers), `start_date`, `end_date` (ISO dates).
- **Outputs:** per symbol, the available date range, first/last price, and missing-data warnings; plus the data source and adjustment methodology. Full series are cached server-side so the model's context only sees summaries.
- **Source:** Yahoo Finance's public daily chart endpoint, adjusted close (splits and dividends). No API key. This is an unofficial endpoint; the app labels data as delayed/historical, never live.
- **Errors:** distinguishes `unsupported_symbol`, `invalid_argument` (bad dates), `unavailable_period`, `authentication`, `rate_limited`, and `service_unavailable`.

### 2. `analyze_portfolio`

Allocation and historical risk statistics from the cached prices.

- **Inputs:** `holdings` (`{"VTI": 6000, ...}`) and `cash`; both optional once the session has a stored portfolio.
- **Outputs:** total value, weights (including cash), annualized volatility, maximum drawdown, and total return for the portfolio and each ETF; the actual observation period; data sources and warnings; stated assumptions.
- **Method:** daily returns on the intersection of trading days across held ETFs; constant supplied weights with daily rebalancing; 252 trading days per year; cash returns 0%.
- **Errors:** negative holdings, zero-value portfolio, fewer than 21 overlapping observations, or prices not yet fetched.

This is a hypothetical historical illustration using supplied weights, not a reconstruction of the user's account performance or a forecast.

### 3. `simulate_rebalance`

Dollar trades to move from current holdings to user-specified target weights.

- **Inputs:** `target_weights` in percent including `CASH` (e.g. `{"VTI": 50, "BND": 30, "CASH": 20}`); optional `holdings`/`cash`.
- **Outputs:** current and target dollar values, buy/sell/hold per asset, resulting weights.
- **Assumptions (returned with the result):** fractional shares, no external cash flows, no taxes, fees, or slippage.
- **Errors:** negative weights, weights not summing to 100% within ±0.01, unsupported symbols.

The tool compares the user's proposal; it does not choose an "optimal" allocation or place trades.

### 4. `stress_test_cash_need` — original tool focus

Combine a hypothetical market shock with an immediate cash requirement.

- **Inputs:** `shocks_pct` for every held ETF (e.g. `{"VTI": -25, "BND": -8}`), `cash_need`, `liquidation_rule` (enum; currently `cash_first_then_proportional`); optional `holdings`/`cash`.
- **Outputs:** before/after-shock values, cash used, ETF sales per fund, whether the expense is covered, any uncovered shortfall, and the remaining allocation after payment.
- **Liquidation rule:** spend existing cash first, then sell ETFs proportionally to their post-shock values. The rule is stated in the result and the agent explains it before interpreting numbers.
- **Errors:** missing shock for a held ETF, declines below -100%, negative cash need, unsupported rule. If assets cannot cover the expense, the remaining shortfall is reported instead of impossible trades.

Both simulation tools return a note that the stored portfolio is unchanged, so "that same portfolio" in a follow-up refers to the user's real holdings, not a scenario outcome.

### Session state

Each session holds the conversation plus a portfolio state: holdings, cash, cached price series, and the latest analysis/rebalance/stress-test results. Tools receive this state, so the model does not have to repeat holdings it already supplied (following the course's "offload work from the model" guidance). The UI reads the same state through `GET /state`.

## Three sample queries

Grader test cases. Amounts, weights, and shocks are demonstration inputs, not investment recommendations. All three are available as one-click suggestions in the UI.

1. **Portfolio analysis** — "I hold $6,000 of VTI, $2,000 of BND, and $2,000 in cash. Analyze my allocation and historical risk over the last year. Show the data period and assumptions."

   Behavior: calls `fetch_historical_prices` for VTI and BND over the trailing year, then `analyze_portfolio`; reports the 60% / 20% / 20% allocation, volatility, drawdown, the actual data period, source, warnings, and assumptions.

2. **Rebalancing follow-up** — "Using that same portfolio, compare it with a target of 50% VTI, 30% BND, and 20% cash. How many dollars would I buy or sell?"

   Behavior: reuses the stored portfolio and reports a $1,000 VTI sale and $1,000 BND purchase under frictionless assumptions.

3. **Cash-need stress test** — "Stress-test my original $6,000 VTI, $2,000 BND, and $2,000 cash portfolio. Assume VTI falls 25%, BND falls 8%, and I must pay $4,000 in tuition immediately afterward. Use cash first, then proportional ETF sales."

   Behavior: $4,500 VTI, $1,840 BND, $2,000 cash after the shock; the $4,000 expense uses all cash, leaving a $2,000 gap covered by proportional sales ($1,419.56 VTI, $580.44 BND); remaining portfolio $4,340. States that the shock is hypothetical and the sales simulated.

Verified: the arithmetic for all three (and the shortfall case) is checked against these numbers in the author's tests, and all three ran end-to-end through the chat interface.

## Interface and agent architecture

- **Backend:** `app.py` adapts the course's `gemini-web-tool-calling` starter: FastAPI, LiteLLM, and the same bounded tool loop (`MAX_TOOL_ROUNDS = 6`). The model is selected with the `MODEL` environment variable (default `vertex_ai/gemini-2.5-flash`).
- **Tools:** `tools.py` (JSON schemas + dispatcher with session state), `portfolio.py` (pure calculations), `market_data.py` (price fetching, caching, error classification).
- **Frontend:** `index.html` shows a chat panel with suggested questions and a purpose statement; a holdings summary with an allocation donut; historical-risk cards labeled with the data period; before/after scenario tables for stress tests and rebalances; and an expandable record of each tool call (name, arguments, result) above the answer it informed.

The `/chat` response preserves the starter contract:

```json
{
  "response": "Explanation of the result",
  "session_id": "session-identifier",
  "tool_calls": [{ "name": "simulate_rebalance", "args": {}, "result": "{...json...}" }]
}
```

Additional endpoints: `GET /state?session_id=` (portfolio state for the UI panels, no price series), `POST /clear?session_id=`, `GET /healthz`.

Sessions live in process memory. On Cloud Run this requires a single instance (`--max-instances 1`) or a shared store such as Firestore; the first version uses the single-instance setting. External data and user text are treated as data, not instructions (stated in the system prompt; tool results are JSON only).

## Scope and limitations

Supported assets: cash plus these US-listed ETFs — AGG, BND, GLD, IWM, QQQ, SCHD, SHY, SPY, TIP, TLT, VEA, VNQ, VOO, VTI, VTV, VUG, VWO, VXUS. All values in USD. Not supported: brokerage connections, order execution, individual stocks, options, leverage, tax optimization, return predictions.

Known limitations: the price source is an unofficial endpoint and may rate-limit or change; the most recent trading day sometimes lacks an adjusted close and is dropped (reported as a warning); the historical illustration assumes daily rebalancing to constant weights; only one liquidation rule is implemented.

Portfolio Buffer is an educational analysis and scenario tool, not personalized financial advice. Historical outcomes do not establish future performance. Hypothetical shocks are user inputs, not estimated probabilities.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Python ≥ 3.10.

```bash
uv sync
```

**With Gemini on Vertex AI (course default):** a GCP project with billing and the Agent Platform (Vertex AI) API enabled, then

```bash
gcloud auth application-default login
uv run app.py            # http://localhost:8000
```

**Without GCP (local Ollama):** any Ollama model that supports tool calling, e.g.

```bash
ollama pull gpt-oss:20b
MODEL="ollama_chat/gpt-oss:20b" uv run app.py
```

Environment variables: `MODEL` (LiteLLM model string), `VERTEX_LOCATION` (default `global`), `VERTEXAI_PROJECT` (GCP project ID; needed locally if your ADC quota project differs), `PORT` (default 8000).

## Setup and deployment status

Not yet deployed. Planned: GitHub continuous deployment to GCP Cloud Run per the course guide (buildpack, entrypoint `uvicorn app:app --host 0.0.0.0 --port $PORT`, IAP restricted to Columbia accounts, max instances 1). Credentials are supplied through the Cloud Run service account, not committed to the repository.

Before submission: replace this section with the live URL, fill `submission.json` with the real deployment URL and the author's UNI/email, and keep the service running until grades are released.

## Verification before submission

- [x] Three sample queries run end-to-end in the browser interface (local).
- [x] Allocation, rebalance, and cash-shortfall arithmetic checked against known examples.
- [x] Follow-up questions retain state; independent sessions stay separate.
- [x] Every executed tool appears in the `/chat` response and the UI.
- [x] Invalid symbols, missing inputs, invalid weights, insufficient assets return actionable errors.
- [ ] Repeat the above against the deployed URL with Gemini.
- [ ] Rewrite design-stage statements in the author's own words; record remaining limitations.
