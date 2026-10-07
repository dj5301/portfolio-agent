"""Historical ETF prices from Yahoo Finance's public chart endpoint.

No API key is required. The endpoint is unofficial, so every failure mode is
classified into a message the model can act on instead of a traceback.
"""

import time
from datetime import date, datetime, timedelta, timezone

import requests

from portfolio import SUPPORTED_ETFS, normalize_symbol

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-buffer educational project)"}
TIMEOUT_S = 15
CACHE_TTL_S = 6 * 60 * 60  # a day's closes do not change within a session
MAX_YEARS = 10

SOURCE_NOTE = (
    "Yahoo Finance daily chart data (adjusted close, adjusted for splits and dividends). "
    "Delayed/historical observations, not live quotes."
)


class MarketDataError(Exception):
    """Raised with a user-actionable message; `kind` classifies the failure."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# (symbol, start, end) -> (fetched_at, result)
_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}


def parse_iso_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise MarketDataError("invalid_argument", f"{name} must be an ISO date like 2025-01-31, got '{value}'.")


def validate_period(start_date: str, end_date: str) -> tuple[date, date]:
    start = parse_iso_date(start_date, "start_date")
    end = parse_iso_date(end_date, "end_date")
    today = datetime.now(timezone.utc).date()
    if end > today:
        end = today
    if start >= end:
        raise MarketDataError("invalid_argument", f"start_date ({start}) must be before end_date ({end}).")
    if (end - start).days > 366 * MAX_YEARS:
        raise MarketDataError("invalid_argument", f"Period is longer than {MAX_YEARS} years; choose a shorter range.")
    return start, end


def fetch_symbol(symbol: str, start: date, end: date) -> dict:
    """Return {"prices": {date: adj_close}, "warnings": [...], ...} for one symbol."""
    symbol = normalize_symbol(symbol)
    if symbol not in SUPPORTED_ETFS:
        raise MarketDataError(
            "unsupported_symbol",
            f"'{symbol}' is not in the supported ETF list: {', '.join(sorted(SUPPORTED_ETFS))}.",
        )

    key = (symbol, start.isoformat(), end.isoformat())
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL_S:
        return cached[1]

    # Yahoo's period2 is exclusive-ish; add a day so `end` itself is included.
    period1 = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())

    try:
        resp = requests.get(
            CHART_URL.format(symbol=symbol),
            params={"period1": period1, "period2": period2, "interval": "1d", "events": "div,splits"},
            headers=HEADERS,
            timeout=TIMEOUT_S,
        )
    except requests.Timeout:
        raise MarketDataError("service_unavailable", f"Market data request for {symbol} timed out. Try again shortly.")
    except requests.RequestException as e:
        raise MarketDataError("service_unavailable", f"Could not reach the market data service for {symbol}: {type(e).__name__}.")

    if resp.status_code == 429:
        raise MarketDataError("rate_limited", "Market data provider rate limit reached. Wait a minute before fetching again.")
    if resp.status_code in (401, 403):
        raise MarketDataError("authentication", "Market data provider refused the request (authentication/access). Try again later.")
    if resp.status_code == 404:
        raise MarketDataError("unsupported_symbol", f"Provider has no data for '{symbol}'.")
    if resp.status_code >= 500:
        raise MarketDataError("service_unavailable", f"Market data provider error ({resp.status_code}). Try again later.")
    if resp.status_code != 200:
        raise MarketDataError("service_unavailable", f"Unexpected provider response ({resp.status_code}) for {symbol}.")

    try:
        chart = resp.json()["chart"]
    except (ValueError, KeyError):
        raise MarketDataError("service_unavailable", f"Provider returned an unreadable response for {symbol}.")
    if chart.get("error"):
        code = chart["error"].get("code", "")
        desc = chart["error"].get("description", "")
        if "Not Found" in code or "No data" in desc:
            raise MarketDataError("unsupported_symbol", f"Provider has no data for '{symbol}': {desc}")
        raise MarketDataError("service_unavailable", f"Provider error for {symbol}: {code} {desc}".strip())

    results = chart.get("result") or []
    if not results:
        raise MarketDataError("unavailable_period", f"No observations for {symbol} between {start} and {end}.")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators", {})
    adj = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
    close = (indicators.get("quote") or [{}])[0].get("close") or []

    warnings: list[str] = []
    prices: dict[str, float] = {}
    used_unadjusted = 0
    skipped = 0
    for i, ts in enumerate(timestamps):
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if d < start or d > end:
            continue
        px = adj[i] if i < len(adj) else None
        if px is None:
            px = close[i] if i < len(close) else None
            if px is not None:
                used_unadjusted += 1
        if px is None:
            skipped += 1
            continue
        prices[d.isoformat()] = float(px)

    if not prices:
        raise MarketDataError(
            "unavailable_period",
            f"{symbol} has no price observations between {start} and {end}. "
            f"First trade date on record: {_first_trade(result)}.",
        )
    if skipped:
        warnings.append(f"{skipped} day(s) had no price and were dropped.")
    if used_unadjusted:
        warnings.append(f"{used_unadjusted} day(s) lacked an adjusted close; unadjusted close used instead.")
    first, last = min(prices), max(prices)
    if date.fromisoformat(first) > start + timedelta(days=7):
        warnings.append(f"Data starts {first}, later than the requested {start} (fund may be newer than the request).")
    if date.fromisoformat(last) < end - timedelta(days=7):
        warnings.append(f"Data ends {last}, earlier than the requested {end}.")

    out = {
        "symbol": symbol,
        "name": SUPPORTED_ETFS[symbol],
        "currency": result.get("meta", {}).get("currency", "USD"),
        "available_range": {"start": first, "end": last, "trading_days": len(prices)},
        "first_price": round(prices[first], 4),
        "last_price": round(prices[last], 4),
        "warnings": warnings,
        "prices": prices,
    }
    _cache[key] = (time.time(), out)
    return out


def _first_trade(result: dict) -> str:
    ts = result.get("meta", {}).get("firstTradeDate")
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else "unknown"
