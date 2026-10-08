"""Deterministic portfolio math. No model, no network: just numbers in, numbers out.

Every public function returns a plain dict and raises ValueError with a message
the model can act on. The tool layer (tools.py) turns those into JSON.
"""

import math
from statistics import pstdev

TRADING_DAYS_PER_YEAR = 252
MIN_OBSERVATIONS = 20
WEIGHT_TOLERANCE = 0.01  # target weights must sum to 100% within this many points
CASH = "CASH"

# The documented universe. Values are display names; the agent refuses anything else.
SUPPORTED_ETFS = {
    "VTI": "Vanguard Total Stock Market ETF",
    "VOO": "Vanguard S&P 500 ETF",
    "SPY": "SPDR S&P 500 ETF Trust",
    "QQQ": "Invesco QQQ Trust (Nasdaq-100)",
    "IWM": "iShares Russell 2000 ETF",
    "VTV": "Vanguard Value ETF",
    "VUG": "Vanguard Growth ETF",
    "SCHD": "Schwab U.S. Dividend Equity ETF",
    "VXUS": "Vanguard Total International Stock ETF",
    "VEA": "Vanguard FTSE Developed Markets ETF",
    "VWO": "Vanguard FTSE Emerging Markets ETF",
    "BND": "Vanguard Total Bond Market ETF",
    "AGG": "iShares Core U.S. Aggregate Bond ETF",
    "TLT": "iShares 20+ Year Treasury Bond ETF",
    "SHY": "iShares 1-3 Year Treasury Bond ETF",
    "TIP": "iShares TIPS Bond ETF",
    "VNQ": "Vanguard Real Estate ETF",
    "GLD": "SPDR Gold Shares",
}


# --- Validation helpers ---


def normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().upper()


def validate_holdings(holdings: dict, cash: float) -> tuple[dict[str, float], float]:
    """Return (clean_holdings, cash) or raise ValueError."""
    if not isinstance(holdings, dict):
        raise ValueError("holdings must be an object mapping ETF symbol to dollar value, e.g. {\"VTI\": 6000}.")
    clean: dict[str, float] = {}
    for raw_symbol, raw_value in holdings.items():
        symbol = normalize_symbol(raw_symbol)
        if symbol == CASH:
            raise ValueError("Put cash in the separate 'cash' field, not inside holdings.")
        if symbol not in SUPPORTED_ETFS:
            raise ValueError(
                f"Unsupported symbol '{symbol}'. Supported ETFs: {', '.join(sorted(SUPPORTED_ETFS))}."
            )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"Holding value for {symbol} must be a number in US dollars.")
        if math.isnan(value) or value < 0:
            raise ValueError(f"Holding value for {symbol} must be zero or positive, got {raw_value}.")
        clean[symbol] = value
    try:
        cash = float(cash)
    except (TypeError, ValueError):
        raise ValueError("cash must be a number in US dollars.")
    if math.isnan(cash) or cash < 0:
        raise ValueError(f"cash must be zero or positive, got {cash}.")
    if sum(clean.values()) + cash <= 0:
        raise ValueError("The portfolio has zero total value. Provide at least one positive holding or cash balance.")
    return clean, cash


def _round_money(x: float) -> float:
    return round(x + 0.0, 2)


def _round_pct(x: float) -> float:
    return round(x + 0.0, 2)


def _weights(holdings: dict[str, float], cash: float) -> dict[str, float]:
    total = sum(holdings.values()) + cash
    weights = {s: _round_pct(100 * v / total) for s, v in holdings.items()}
    weights[CASH] = _round_pct(100 * cash / total)
    return weights


# --- Tool 2: analyze_portfolio ---


def align_prices(prices: dict[str, dict[str, float]]) -> tuple[list[str], dict[str, list[float]]]:
    """Intersect the dates of every symbol's {date: adj_close} series.

    Returns (sorted_dates, {symbol: [price_on_each_date]}).
    """
    if not prices:
        return [], {}
    common = None
    for series in prices.values():
        dates = set(series)
        common = dates if common is None else common & dates
    dates = sorted(common or [])
    aligned = {s: [series[d] for d in dates] for s, series in prices.items()}
    return dates, aligned


def daily_returns(series: list[float]) -> list[float]:
    return [series[i] / series[i - 1] - 1 for i in range(1, len(series))]


def max_drawdown(returns: list[float]) -> float:
    """Largest peak-to-trough decline of the cumulative value, as a negative fraction."""
    value, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        value *= 1 + r
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def annualized_volatility(returns: list[float]) -> float:
    return pstdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def analyze_portfolio(holdings: dict, cash: float, prices: dict[str, dict[str, float]], sources: dict | None = None) -> dict:
    """Allocation and historical risk statistics for supplied weights.

    `prices` maps symbol -> {ISO date -> adjusted close}. Only symbols in holdings
    with a positive value need price data.
    """
    holdings, cash = validate_holdings(holdings, cash)
    total = sum(holdings.values()) + cash
    weights = _weights(holdings, cash)

    invested = {s: v for s, v in holdings.items() if v > 0}
    missing = [s for s in invested if s not in prices or not prices[s]]
    if missing:
        raise ValueError(
            f"No price data loaded for {', '.join(missing)}. Call fetch_historical_prices for these symbols first."
        )

    dates, aligned = align_prices({s: prices[s] for s in invested})
    if len(dates) < MIN_OBSERVATIONS + 1:
        raise ValueError(
            f"Only {len(dates)} overlapping trading days across {', '.join(invested)}; "
            f"need at least {MIN_OBSERVATIONS + 1}. Fetch a longer or better-aligned period."
        )

    per_etf_returns = {s: daily_returns(series) for s, series in aligned.items()}
    n = len(dates) - 1
    # Constant weights, rebalanced daily, cash earns 0%.
    frac = {s: v / total for s, v in invested.items()}
    port_returns = [sum(frac[s] * per_etf_returns[s][i] for s in invested) for i in range(n)]

    cumulative = 1.0
    for r in port_returns:
        cumulative *= 1 + r

    def stats(returns: list[float]) -> dict:
        cum = 1.0
        for r in returns:
            cum *= 1 + r
        return {
            "annualized_volatility_pct": _round_pct(100 * annualized_volatility(returns)),
            "max_drawdown_pct": _round_pct(100 * max_drawdown(returns)),
            "total_return_pct": _round_pct(100 * (cum - 1)),
        }

    return {
        "total_value_usd": _round_money(total),
        "holdings_usd": {**{s: _round_money(v) for s, v in holdings.items()}, CASH: _round_money(cash)},
        "weights_pct": weights,
        "portfolio_risk": stats(port_returns),
        "per_etf_risk": {s: stats(r) for s, r in per_etf_returns.items()},
        "observation_period": {"start": dates[0], "end": dates[-1], "trading_days": len(dates)},
        "data_sources": sources or {},
        "assumptions": [
            "Hypothetical illustration using the supplied weights held constant with daily rebalancing.",
            "Cash earns 0% return.",
            f"Volatility annualized with {TRADING_DAYS_PER_YEAR} trading days per year, using adjusted closes.",
            "Not a reconstruction of the user's actual account history and not a forecast.",
        ],
    }


# --- Tool 3: simulate_rebalance ---


def simulate_rebalance(holdings: dict, cash: float, target_weights: dict) -> dict:
    """Dollar trades to move from current holdings to target weights (percent, including CASH)."""
    holdings, cash = validate_holdings(holdings, cash)
    if not isinstance(target_weights, dict) or not target_weights:
        raise ValueError("target_weights must be an object mapping symbol (and 'CASH') to percent, e.g. {\"VTI\": 50, \"BND\": 30, \"CASH\": 20}.")

    targets: dict[str, float] = {}
    for raw_symbol, raw_pct in target_weights.items():
        symbol = normalize_symbol(raw_symbol)
        if symbol != CASH and symbol not in SUPPORTED_ETFS:
            raise ValueError(f"Unsupported symbol '{symbol}' in target_weights. Supported ETFs: {', '.join(sorted(SUPPORTED_ETFS))}.")
        try:
            pct = float(raw_pct)
        except (TypeError, ValueError):
            raise ValueError(f"Target weight for {symbol} must be a number (percent).")
        if pct < 0:
            raise ValueError(f"Target weight for {symbol} is negative ({pct}%). Weights must be zero or positive.")
        targets[symbol] = pct
    targets.setdefault(CASH, 0.0)

    weight_sum = sum(targets.values())
    if abs(weight_sum - 100) > WEIGHT_TOLERANCE:
        raise ValueError(
            f"Target weights sum to {weight_sum:.2f}%, not 100% (tolerance ±{WEIGHT_TOLERANCE}). "
            "Include CASH so the weights cover the whole portfolio."
        )

    total = sum(holdings.values()) + cash
    current = {**holdings, CASH: cash}
    symbols = sorted(set(current) | set(targets), key=lambda s: (s == CASH, s))

    trades = {}
    target_values = {}
    for s in symbols:
        cur = current.get(s, 0.0)
        tgt = total * targets.get(s, 0.0) / 100
        target_values[s] = _round_money(tgt)
        delta = tgt - cur
        trades[s] = {
            "current_usd": _round_money(cur),
            "target_usd": _round_money(tgt),
            "action": "buy" if delta > 0.005 else "sell" if delta < -0.005 else "hold",
            "amount_usd": _round_money(abs(delta)) if s != CASH else _round_money(abs(delta)),
        }
    trades[CASH]["action"] = {"buy": "increase", "sell": "decrease", "hold": "hold"}[trades[CASH]["action"]]

    resulting_holdings = {s: target_values[s] for s in symbols if s != CASH}
    resulting_weights = _weights(resulting_holdings, target_values[CASH])

    return {
        "total_value_usd": _round_money(total),
        "current_weights_pct": _weights(holdings, cash),
        "target_weights_pct": {s: _round_pct(targets.get(s, 0.0)) for s in symbols},
        "trades": trades,
        "resulting_weights_pct": resulting_weights,
        "assumptions": [
            "Fractional shares allowed; trades are expressed in dollars.",
            "No external cash flows: total portfolio value is unchanged.",
            "No taxes, commissions, bid-ask spread, or slippage.",
            "This compares the user's proposed allocation; it does not recommend one or place trades.",
        ],
    }


# --- Tool 4: stress_test_cash_need ---

LIQUIDATION_RULES = {
    "cash_first_then_proportional": "Spend existing cash first, then sell ETF holdings in proportion to their post-shock values.",
    "cash_first_then_least_fallen_first": (
        "Spend existing cash first, then sell the ETF that fell the least (or rose the most) until it is gone, "
        "then the next-least-fallen, and so on. Avoids selling the hardest-hit asset at its low."
    ),
}


def _plan_sales(rule: str, need: float, post: dict[str, float], shocks: dict[str, float]) -> dict[str, float]:
    """Dollar sales per ETF to raise `need` from post-shock values. Caller handles the case where assets fall short."""
    if rule == "cash_first_then_proportional":
        total = sum(post.values())
        return {s: need * v / total for s, v in post.items()}
    if rule == "cash_first_then_least_fallen_first":
        sales = {s: 0.0 for s in post}
        for s in sorted(post, key=lambda s: -shocks.get(s, 0.0)):  # best performer first
            if need <= 0:
                break
            sales[s] = min(post[s], need)
            need -= sales[s]
        return sales
    raise ValueError(f"Unsupported liquidation_rule '{rule}'.")


def stress_test_cash_need(
    holdings: dict,
    cash: float,
    shocks_pct: dict,
    cash_need: float,
    liquidation_rule: str = "cash_first_then_proportional",
) -> dict:
    """Apply percentage shocks to each ETF, then meet an immediate cash expense."""
    holdings, cash = validate_holdings(holdings, cash)

    if liquidation_rule not in LIQUIDATION_RULES:
        raise ValueError(f"Unsupported liquidation_rule '{liquidation_rule}'. Supported: {list(LIQUIDATION_RULES)}.")
    try:
        cash_need = float(cash_need)
    except (TypeError, ValueError):
        raise ValueError("cash_need must be a number in US dollars.")
    if cash_need < 0:
        raise ValueError(f"cash_need must be zero or positive, got {cash_need}.")
    if not isinstance(shocks_pct, dict):
        raise ValueError("shocks_pct must be an object mapping ETF symbol to percent change, e.g. {\"VTI\": -25, \"BND\": -8}.")

    shocks: dict[str, float] = {}
    for raw_symbol, raw_pct in shocks_pct.items():
        symbol = normalize_symbol(raw_symbol)
        if symbol == CASH:
            raise ValueError("Cash cannot be shocked; remove 'CASH' from shocks_pct.")
        if symbol not in holdings:
            raise ValueError(f"Shock given for {symbol}, which is not in the portfolio ({', '.join(sorted(holdings)) or 'no ETFs'}).")
        try:
            pct = float(raw_pct)
        except (TypeError, ValueError):
            raise ValueError(f"Shock for {symbol} must be a number (percent change, negative for a decline).")
        if pct < -100:
            raise ValueError(f"Shock for {symbol} is {pct}%; an asset cannot lose more than 100%.")
        shocks[symbol] = pct
    invested = [s for s, v in holdings.items() if v > 0]
    missing = [s for s in invested if s not in shocks]
    if missing:
        raise ValueError(
            f"Missing shock assumption for {', '.join(missing)}. Give a percent change for every ETF held (use 0 for no change)."
        )

    pre_total = sum(holdings.values()) + cash
    post = {s: v * (1 + shocks.get(s, 0.0) / 100) for s, v in holdings.items()}
    post_etf_total = sum(post.values())
    post_total = post_etf_total + cash

    # Liquidation: cash first, then ETF sales according to the chosen rule.
    cash_used = min(cash, cash_need)
    remaining_need = cash_need - cash_used
    sales = {s: 0.0 for s in holdings}
    shortfall = 0.0
    if remaining_need > 0:
        if post_etf_total <= 0:
            shortfall = remaining_need
        else:
            sold = min(remaining_need, post_etf_total)
            sales = _plan_sales(liquidation_rule, sold, post, shocks)
            shortfall = remaining_need - sold

    remaining_holdings = {s: post[s] - sales[s] for s in holdings}
    remaining_cash = cash - cash_used
    remaining_total = sum(remaining_holdings.values()) + remaining_cash
    remaining_weights = _weights(remaining_holdings, remaining_cash) if remaining_total > 0 else {}

    return {
        "before_shock": {
            "total_value_usd": _round_money(pre_total),
            "holdings_usd": {**{s: _round_money(v) for s, v in holdings.items()}, CASH: _round_money(cash)},
            "weights_pct": _weights(holdings, cash),
        },
        "shocks_pct": {s: _round_pct(p) for s, p in shocks.items()},
        "after_shock": {
            "total_value_usd": _round_money(post_total),
            "holdings_usd": {**{s: _round_money(v) for s, v in post.items()}, CASH: _round_money(cash)},
            "loss_usd": _round_money(pre_total - post_total),
            "loss_pct": _round_pct(100 * (post_total - pre_total) / pre_total),
        },
        "cash_need_usd": _round_money(cash_need),
        "liquidation_rule": liquidation_rule,
        "liquidation_rule_description": LIQUIDATION_RULES[liquidation_rule],
        "funding": {
            "cash_used_usd": _round_money(cash_used),
            "cash_shortfall_after_cash_usd": _round_money(remaining_need),
            "etf_sales_usd": {s: _round_money(v) for s, v in sales.items()},
            "total_etf_sales_usd": _round_money(sum(sales.values())),
            "expense_covered": shortfall <= 0.005,
            "uncovered_shortfall_usd": _round_money(shortfall),
        },
        "after_payment": {
            "total_value_usd": _round_money(remaining_total),
            "holdings_usd": {**{s: _round_money(v) for s, v in remaining_holdings.items()}, CASH: _round_money(remaining_cash)},
            "weights_pct": remaining_weights,
        },
        "assumptions": [
            "The shock percentages are user-supplied hypotheticals, not estimated probabilities.",
            "Sales are simulated at post-shock values with no taxes, fees, or slippage.",
            LIQUIDATION_RULES[liquidation_rule],
        ],
    }
