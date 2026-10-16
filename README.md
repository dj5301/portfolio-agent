# Portfolio Buffer

**A conversational portfolio analysis agent that connects investment allocations with real-life cash needs.**

## The problem

I created this project because, while investing on my own, I realized I wasn’t sure how to allocate my funds. I wanted a tool that would first let me see my current portfolio allocations and historical risk, and then compare how different allocations would affect my portfolio. That’s why I built Portfolio Buffer, which allows me to analyze my portfolio through a conversational interface and calculate how much I need to buy or sell to adjust my allocations. I also added a stress test that simulates a market downturn coinciding with a cash need, to help me understand the consequences of a particular allocation when I need to access funds. Its purpose is to help me understand my options and trade-offs; the final allocation is still up to me.


## The experience

When I used it myself, I entered a set of holdings: $6,000 in VTI, $2,000 in BND, and $2,000 in cash, and had the agent analyze the allocation and historical risk over the past year. I wanted to know not only the respective allocations of 60%, 20%, and 20% for each asset, but also how volatile this portfolio had been over the past year and what the maximum drawdown was from peak to trough. The agent first retrieves the historical prices for VTI and BND, then performs calculations using Python tools, clearly specifying the actual data range, sources, and calculation assumptions. This allows me to map the abstract concept of “risk” to concrete metrics, rather than just hearing that “this allocation is relatively stable.” I can also continue to propose different target allocations, have it calculate the amounts to buy or sell, and use that to understand the adjustment options before deciding for myself whether to change the allocation.


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

- **Inputs:** `shocks_pct` for every held ETF (e.g. `{"VTI": -25, "BND": -8}`), `cash_need`, `liquidation_rule` (enum, see below), `protect` (list of ETFs the user wants to keep); optional `holdings`/`cash`.
- **Outputs:** before/after-shock values, cash used, ETF sales per fund (in dollars and as a percent of each post-shock holding), the order they were tapped (`sale_order`) and a plain-English `sale_order_reason` computed by the code (to provide an explicit rationale for the model to report), whether the expense is covered, any uncovered shortfall, the remaining allocation after payment, and `warnings`.
- **Liquidation rules:** both spend existing cash first, then differ in how ETFs are sold.
  - `cash_first_then_proportional` (default): sell every ETF in proportion to its post-shock value.
  - `cash_first_then_least_fallen_first`: sell the ETF that fell least (or rose most) until it is gone, then the next. This delays selling the hardest-hit ETF, but it may still need to be sold if the remaining cash need cannot otherwise be covered. In the sample scenario this sells all $1,840 of BND and only $160 of VTI, versus $1,419.56 of VTI under the proportional rule. Ties (equal shocks) go to the larger position, then alphabetical, so the answer does not depend on the order the user listed the funds.

  The rule used is stated in the result; when asked how to avoid selling the worst performer, the agent runs both and contrasts them.


- **Protecting a holding:** The `protect` parameter gives selected ETFs lower priority for selling. The simulation uses cash and unprotected ETFs first. If those assets cannot cover the expense, it may still sell protected ETFs and return a warning. Protection means “sell last,” not “never sell.”
- **Errors:** missing shock for a held ETF, declines below -100%, negative cash need, unsupported rule, protecting an ETF not in the portfolio. If assets cannot cover the expense, the remaining shortfall is reported instead of impossible trades.
Both simulation tools return a note that the stored portfolio is unchanged, so "that same portfolio" in a follow-up refers to the user's real holdings, not a scenario outcome.

### Session state

Each session holds the conversation plus a portfolio state: holdings, cash, cached price series, and the latest analysis/rebalance/stress-test results. Tools receive this state, so the model does not have to repeat holdings it already supplied (following the course's "offload work from the model" guidance). The UI reads the same state through `GET /state`.

## Three sample queries

Run the three main queries in order within the same session, then try the follow-up questions.

Grader test cases. Amounts, weights, and shocks are demonstration inputs, not investment recommendations. All three are available as one-click suggestions in the UI.

1. **Portfolio analysis** — "I hold $6,000 of VTI, $2,000 of BND, and $2,000 in cash. Analyze my allocation and historical risk over the last year. Show the data period and assumptions."

   Behavior: calls `fetch_historical_prices` for VTI and BND over the trailing year, then `analyze_portfolio`; reports the 60% / 20% / 20% allocation, volatility, drawdown, the actual data period, source, warnings, and assumptions.

2. **Rebalancing follow-up** — "Using that same portfolio, compare it with a target of 50% VTI, 30% BND, and 20% cash. How many dollars would I buy or sell?"

   Behavior: reuses the stored portfolio and reports a $1,000 VTI sale and $1,000 BND purchase under frictionless assumptions.

3. **Cash-need stress test** — "Stress-test my original $6,000 VTI, $2,000 BND, and $2,000 cash portfolio. Assume VTI falls 25%, BND falls 8%, and I must pay $4,000 in tuition immediately afterward. Use cash first, then proportional ETF sales."

   Behavior: $4,500 VTI, $1,840 BND, $2,000 cash after the shock; the $4,000 expense uses all cash, leaving a $2,000 gap covered by proportional sales ($1,419.56 VTI, $580.44 BND); remaining portfolio $4,340. States that the shock is hypothetical and the sales simulated.

Verified: the arithmetic for all three (and the shortfall case) is checked against these numbers in the author's tests, and all three ran end-to-end through the chat interface.

**Follow-ups tested in the same session:**

1. Using the same original portfolio and market shocks, increase
   the tuition expense to $5,000. Keep the proportional liquidation
   rule. How much of each ETF would I need to sell?

2. Compare proportional selling with selling the least-fallen ETF
   first, using the same original portfolio, shocks, and $5,000 expense.

3. Repeat the scenario using the proportional rule, but protect VTI:
   sell it only after cash and all BND are exhausted.
   Explain whether any protected VTI still needs to be sold.

## Interface and agent architecture

- **Backend:** `app.py` adapts the course's `gemini-web-tool-calling` starter: FastAPI, LiteLLM, and the same bounded tool loop (`MAX_TOOL_ROUNDS = 6`). The model is selected with the `MODEL` environment variable (default `vertex_ai/gemini-2.5-flash`).

- **Harness guard:** During testing, I wanted to confirm exactly what “protecting a specific ETF” meant. I compared the results of selling proportionally versus protecting VTI using the same portfolio and found that protecting VTI caused the program to first use up all its cash and BND, but it would still sell a portion of VTI when funds ran low. Therefore, I clearly distinguished between “sell last” and “never sell” in the instructions. The program also checks whether the numbers in the responses have a source, though this does not guarantee that the model’s interpretation of the numbers is necessarily correct.

- **Tools:** `tools.py` (JSON schemas + dispatcher with session state), `portfolio.py` (pure calculations), `market_data.py` (price fetching, caching, error classification).

- **Frontend:** `index.html` shows a chat panel with suggested questions and a purpose statement; a holdings summary with an allocation donut; historical-risk cards labeled with the data period; before/after scenario tables for stress tests and rebalances; and an expandable record of each tool call (name, arguments, result) above the answer it informed. On load it pings `/health` and shows "Waking up the server…" while a scaled-to-zero Cloud Run instance starts. Every answer opens with one bold sentence stating what the numbers mean for the user before the details.

The `/chat` response preserves the starter contract:

```json
{
  "response": "Explanation of the result",
  "session_id": "session-identifier",
  "tool_calls": [{ "name": "simulate_rebalance", "args": {}, "result": "{...json...}" }]
}
```

Additional endpoints: `GET /state?session_id=` (portfolio state for the UI panels, no price series), `POST /clear?session_id=`, `GET /health`.

Sessions are stored in Python process memory. The deployment uses a single Cloud Run instance, but conversation history and portfolio state are lost if the process restarts or the instance is replaced. Persistent session storage is not implemented. The system prompt instructs the model to treat external content as data, and tools return structured JSON. These measures help organize and separate tool results, but they do not guarantee protection against prompt injection.

## Scope and limitations

Supported assets: cash plus these US-listed ETFs — AGG, BND, GLD, IWM, QQQ, SCHD, SHY, SPY, TIP, TLT, VEA, VNQ, VOO, VTI, VTV, VUG, VWO, VXUS. All values in USD. Not supported: brokerage connections, order execution, individual stocks, options, leverage, tax optimization, return predictions.

Known limitations: the price source is an unofficial endpoint and may rate-limit or change. When adjusted close is unavailable, the app falls back to unadjusted close and reports a warning. Observations are dropped only when neither price is available. The historical illustration assumes daily rebalancing to constant weights; liquidation rules ignore taxes and order size.

Portfolio Buffer is an educational analysis and scenario tool, not personalized financial advice. Historical outcomes do not establish future performance. Hypothetical shocks are user inputs, not estimated probabilities.

The scenario panel currently shows only the latest stress-test result, so I cannot keep both liquidation rules visible side by side there, even though the chat can compare them. The numerical guard checks whether figures appeared in user inputs or tool results, but it does not verify that the explanation uses them correctly. For example, it cannot reliably distinguish a percentage of one ETF from a percentage of the entire portfolio. I still need to improve scenario comparison and test the accuracy of the model’s explanations more thoroughly. I want to continue this project because comparing how different allocations handle a cash expense helps me understand the consequences of my investment choices.

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

## Deployment

Live: **https://portfolio-agent-git-1060383691779.europe-west1.run.app**

- GCP Cloud Run (`europe-west1`), built with Google Cloud buildpacks from this repository; the `Procfile` sets the entrypoint `uvicorn app:app --host 0.0.0.0 --port $PORT`.
- `--max-instances 1` because sessions are held in process memory.
- Gemini is called through the Cloud Run service account (`roles/aiplatform.user`); no credentials are committed. `VERTEXAI_PROJECT` is set as a service environment variable.
- Source: https://github.com/dj5301/portfolio-agent. Continuous deployment from `main` via Developer Connect per the course guide.
