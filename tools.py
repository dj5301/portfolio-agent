"""The tools the harness can run, and the JSON that describes them to the model.

Each tool receives the session's `state` dict so the model does not have to
repeat holdings it already gave us: pass `holdings`/`cash` to set or replace the
remembered portfolio, omit them to reuse it. Full price series live in state,
not in the model's context.
"""

import json

import portfolio
from market_data import SOURCE_NOTE, MarketDataError, fetch_symbol, validate_period
from portfolio import SUPPORTED_ETFS

MAX_SYMBOLS_PER_FETCH = 8


def new_state() -> dict:
    return {
        "holdings": None,  # {symbol: usd}
        "cash": None,  # usd
        "prices": {},  # symbol -> {date: adj_close}
        "price_meta": {},  # symbol -> summary without the series
        "last_analysis": None,
        "last_rebalance": None,
        "last_stress_test": None,
    }


def _err(kind: str, message: str, **extra) -> str:
    return json.dumps({"error": {"kind": kind, "message": message, **extra}})


def _resolve_portfolio(state: dict, holdings, cash) -> tuple[dict, float]:
    """Use supplied holdings/cash (and remember them) or fall back to the session's."""
    if holdings is None and cash is None:
        if state["holdings"] is None:
            raise ValueError(
                "No portfolio is stored in this session yet. Ask the user for their ETF holdings and cash balance in USD, then pass `holdings` and `cash`."
            )
        return state["holdings"], state["cash"]
    if holdings is None:
        holdings = state["holdings"] or {}
    if cash is None:
        cash = state["cash"] if state["cash"] is not None else 0.0
    clean_holdings, clean_cash = portfolio.validate_holdings(holdings, cash)
    state["holdings"], state["cash"] = clean_holdings, clean_cash
    return clean_holdings, clean_cash


# --- Tool 1 ---


def fetch_historical_prices(state: dict, symbols: list, start_date: str, end_date: str) -> str:
    if not isinstance(symbols, list) or not symbols:
        return _err("invalid_argument", "symbols must be a non-empty list of ETF tickers, e.g. [\"VTI\", \"BND\"].")
    if len(symbols) > MAX_SYMBOLS_PER_FETCH:
        return _err("invalid_argument", f"At most {MAX_SYMBOLS_PER_FETCH} symbols per call.")
    try:
        start, end = validate_period(start_date, end_date)
    except MarketDataError as e:
        return _err(e.kind, str(e))

    fetched, errors = {}, {}
    for raw in symbols:
        symbol = portfolio.normalize_symbol(raw)
        try:
            data = fetch_symbol(symbol, start, end)
        except MarketDataError as e:
            errors[symbol] = {"kind": e.kind, "message": str(e)}
            continue
        state["prices"][symbol] = data["prices"]
        meta = {k: v for k, v in data.items() if k != "prices"}
        state["price_meta"][symbol] = {**meta, "requested_period": {"start": start.isoformat(), "end": end.isoformat()}}
        fetched[symbol] = meta

    if not fetched:
        return _err("fetch_failed", "No symbols could be fetched.", per_symbol=errors)

    return json.dumps({
        "source": SOURCE_NOTE,
        "requested_period": {"start": start.isoformat(), "end": end.isoformat()},
        "fetched": fetched,
        "failed": errors,
        "note": "Full daily series are cached in this session for analyze_portfolio; only summaries are shown here.",
    })


# --- Tool 2 ---


def analyze_portfolio(state: dict, holdings: dict | None = None, cash: float | None = None) -> str:
    try:
        holdings, cash = _resolve_portfolio(state, holdings, cash)
        sources = {
            s: {
                "source": SOURCE_NOTE,
                "available_range": state["price_meta"][s]["available_range"],
                "warnings": state["price_meta"][s]["warnings"],
            }
            for s in holdings
            if s in state["price_meta"]
        }
        result = portfolio.analyze_portfolio(holdings, cash, state["prices"], sources)
    except ValueError as e:
        return _err("invalid_argument", str(e))
    state["last_analysis"] = result
    return json.dumps(result)


# --- Tool 3 ---


def simulate_rebalance(state: dict, target_weights: dict, holdings: dict | None = None, cash: float | None = None) -> str:
    try:
        holdings, cash = _resolve_portfolio(state, holdings, cash)
        result = portfolio.simulate_rebalance(holdings, cash, target_weights)
    except ValueError as e:
        return _err("invalid_argument", str(e))
    state["last_rebalance"] = result
    return json.dumps({**result, "note": _simulation_note(state)})


def _simulation_note(state: dict) -> str:
    return (
        "This is a simulation; no trades were made. The session's stored portfolio is unchanged: "
        f"{json.dumps(state['holdings'])} plus cash {state['cash']}. Omit holdings/cash in later calls to reuse it."
    )


# --- Tool 4 ---


def stress_test_cash_need(
    state: dict,
    shocks_pct: dict,
    cash_need: float,
    liquidation_rule: str = "cash_first_then_proportional",
    protect: list | None = None,
    holdings: dict | None = None,
    cash: float | None = None,
) -> str:
    try:
        holdings, cash = _resolve_portfolio(state, holdings, cash)
        result = portfolio.stress_test_cash_need(holdings, cash, shocks_pct, cash_need, liquidation_rule, protect)
    except ValueError as e:
        return _err("invalid_argument", str(e))
    state["last_stress_test"] = result
    return json.dumps({**result, "note": _simulation_note(state)})


# --- What the model sees ---

_HOLDINGS_PARAM = {
    "type": "object",
    "description": (
        "Current ETF holdings as dollar values, e.g. {\"VTI\": 6000, \"BND\": 2000}. "
        "Omit to reuse the portfolio already stored in this session; include to set or replace it."
    ),
    "additionalProperties": {"type": "number"},
}
_CASH_PARAM = {
    "type": "number",
    "description": "Cash balance in US dollars, e.g. 2000. Omit to reuse the stored value.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_historical_prices",
            "description": (
                "Download daily historical adjusted-close prices for US-listed ETFs from the market-data provider and "
                "cache them in the session. Call this BEFORE analyze_portfolio whenever historical risk is requested or "
                "the period changes. Returns the data source, available date range, and warnings per symbol."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"ETF tickers to fetch, e.g. [\"VTI\", \"BND\"]. Supported: {', '.join(sorted(SUPPORTED_ETFS))}.",
                    },
                    "start_date": {"type": "string", "description": "Period start, ISO format YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Period end, ISO format YYYY-MM-DD (today for 'the last year')."},
                },
                "required": ["symbols", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_portfolio",
            "description": (
                "Compute the portfolio's total value, allocation weights (including cash), and historical risk over the "
                "prices already fetched: annualized volatility, maximum drawdown, and total return for the portfolio and "
                "each ETF. Requires fetch_historical_prices to have been called for every held ETF."
            ),
            "parameters": {
                "type": "object",
                "properties": {"holdings": _HOLDINGS_PARAM, "cash": _CASH_PARAM},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_rebalance",
            "description": (
                "Compute the dollar buys and sells needed to move the current portfolio to user-specified target "
                "weights, and the resulting allocation. Frictionless: no taxes, fees, or slippage. Use when the user "
                "proposes a target allocation; do not invent targets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_weights": {
                        "type": "object",
                        "description": (
                            "Target weights in PERCENT that sum to 100, including cash under the key \"CASH\", "
                            "e.g. {\"VTI\": 50, \"BND\": 30, \"CASH\": 20}."
                        ),
                        "additionalProperties": {"type": "number"},
                    },
                    "holdings": _HOLDINGS_PARAM,
                    "cash": _CASH_PARAM,
                },
                "required": ["target_weights"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stress_test_cash_need",
            "description": (
                "Apply a hypothetical percentage shock to each ETF, then fund an immediate cash expense using the "
                "chosen liquidation rule. Returns post-shock values, cash shortfall, required ETF sales, whether the "
                "expense is covered, and the remaining allocation. Use for questions like 'if X falls and I must pay Y'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shocks_pct": {
                        "type": "object",
                        "description": (
                            "Percent change for EVERY held ETF; negative for declines, 0 for unchanged. "
                            "e.g. {\"VTI\": -25, \"BND\": -8}."
                        ),
                        "additionalProperties": {"type": "number"},
                    },
                    "cash_need": {"type": "number", "description": "Cash required right after the shock, in US dollars, e.g. 4000."},
                    "liquidation_rule": {
                        "type": "string",
                        "enum": list(portfolio.LIQUIDATION_RULES),
                        "description": (
                            "How to raise cash after spending existing cash. cash_first_then_proportional: sell every ETF in "
                            "proportion to its post-shock value (default; use when the user does not specify). "
                            "cash_first_then_least_fallen_first: sell the best-performing ETF first, exhausting it before the next, "
                            "so the hardest-hit asset is not sold at its low (ties: larger position first). Use when the user asks "
                            "to avoid selling what fell most, to sell bonds/winners first, or to compare rules."
                        ),
                    },
                    "protect": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "ETFs the user does NOT want to sell, e.g. [\"QQQ\"]. They are sold only if cash plus every other ETF "
                            "cannot cover the need (the result then carries a warning). Use whenever the user names a holding to "
                            "keep ('don't touch QQQ', 'I want to hold on to my bonds'). Works with either liquidation rule."
                        ),
                    },
                    "holdings": _HOLDINGS_PARAM,
                    "cash": _CASH_PARAM,
                },
                "required": ["shocks_pct", "cash_need"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "fetch_historical_prices": fetch_historical_prices,
    "analyze_portfolio": analyze_portfolio,
    "simulate_rebalance": simulate_rebalance,
    "stress_test_cash_need": stress_test_cash_need,
}


def run_tool(name: str, args: dict, state: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return _err("unknown_tool", f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}")
    try:
        return TOOL_MAP[name](state, **args)
    except TypeError as e:
        return _err("invalid_argument", f"Bad arguments for {name}: {e}")
    except Exception as e:  # last line of defense: the model must see something it can reason about
        return _err("internal", f"{name} failed: {type(e).__name__}. Check the inputs and try again.")
