import re
import json
import httpx
import asyncio
import threading
import urllib.parse
import pandas as pd
import yfinance as yf

from pathlib import Path

from app.skills.registry import register_tool

_NAME_TO_TICKER: dict[str, str] = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT",
    "amazon": "AMZN", "google": "GOOGL", "alphabet": "GOOGL",
    "meta": "META", "facebook": "META", "tesla": "TSLA",
    "netflix": "NFLX", "disney": "DIS", "intel": "INTC",
    "amd": "AMD", "qualcomm": "QCOM", "broadcom": "AVGO",
    "walmart": "WMT", "visa": "V", "mastercard": "MA",
    "jpmorgan": "JPM", "jp morgan": "JPM", "bank of america": "BAC",
    "berkshire": "BRK-B", "exxon": "XOM", "chevron": "CVX",
    "bitcoin": "BTC-USD", "ethereum": "ETH-USD", "dogecoin": "DOGE-USD",
    "solana": "SOL-USD", "cardano": "ADA-USD", "ripple": "XRP-USD",
    "s&p 500": "^GSPC", "sp500": "^GSPC", "s&p": "^GSPC",
    "dow jones": "^DJI", "dow": "^DJI", "nasdaq": "^IXIC",
    "gold": "GC=F", "silver": "SI=F", "oil": "CL=F", "crude oil": "CL=F",
}

_CACHE_FILE = Path.home() / ".wade" / "workspace" / "ticker_cache.json"
_cache_lock = threading.Lock()
_TICKER_CACHE: dict[str, str] = {}
_PREFERRED_TYPES = ("EQUITY", "ETF", "CRYPTOCURRENCY", "FUTURE", "INDEX", "CURRENCY")

def _load_ticker_cache() -> None:
    """Load the ticker cache from disk, merging with the static name-to-ticker mapping. This allows the cache to grow over time with live lookups while retaining built-in mappings for common names."""
    global _TICKER_CACHE
    disk: dict[str, str] = {}
    if _CACHE_FILE.exists():
        try:
            disk = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    _TICKER_CACHE = {**disk, **_NAME_TO_TICKER}

def _persist_ticker_cache() -> None:
    """Write the full runtime cache to disk (must be called under _cache_lock)."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(_TICKER_CACHE, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass

_load_ticker_cache()

def _search_ticker_live(name: str) -> str | None:
    """Query Yahoo Finance for a company name and return the most relevant ticker symbol."""
    try:
        encoded = urllib.parse.quote(name)
        url = (
            "https://query2.finance.yahoo.com/v1/finance/search"
            f"?q={encoded}&quotesCount=5&newsCount=0&enableFuzzyQuery=false"
        )
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

        result_list = resp.json().get("finance", {}).get("result", [])
        if not result_list:
            return None

        quotes = result_list[0].get("quotes", [])
        if not quotes:
            return None

        for preferred in _PREFERRED_TYPES:
            for q in quotes:
                if q.get("quoteType", "") == preferred:
                    symbol = q.get("symbol", "").strip()
                    if symbol:
                        return symbol

        return quotes[0].get("symbol", "").strip() or None

    except Exception:
        return None

def _resolve_symbol(raw: str) -> str:
    """Convert a user-provided name or ticker into a valid yfinance symbol, using cache and live lookup."""
    cleaned = raw.strip()
    lookup = cleaned.lower()

    with _cache_lock:
        if lookup in _TICKER_CACHE:
            return _TICKER_CACHE[lookup]

    if re.match(r"^[A-Z]{1,6}(-[A-Z]{2,4}|=X|=F)?$", cleaned.upper()):
        return cleaned.upper()

    resolved = _search_ticker_live(cleaned)
    if resolved:
        with _cache_lock:
            _TICKER_CACHE[lookup] = resolved
            _persist_ticker_cache()
        return resolved

    return cleaned.upper()

@register_tool("get_market_data")
async def get_market_data(symbol: str, period: str = "5d") -> str:
    """Fetches universal market data using yfinance."""
    try:
        def _fetch():
            safe_symbol = _resolve_symbol(symbol)

            ticker = yf.Ticker(safe_symbol)
            hist = ticker.history(period=period)

            if hist.empty:
                return (
                    f"Error: No data for '{safe_symbol}'. "
                    f"Check the ticker format (e.g. 'BTC-USD' for crypto, 'EURUSD=X' for forex)."
                )

            latest = hist.iloc[-1]
            current_price = latest["Close"]
            daily_high = latest["High"]
            daily_low = latest["Low"]
            volume = latest["Volume"]
            vol_str = "N/A" if pd.isna(volume) else f"{volume:,.0f}"

            start_price = hist.iloc[0]["Close"]
            pct_change = ((current_price - start_price) / start_price) * 100

            output = [
                f"--- Market Data for {safe_symbol} ---",
                f"Current Price: {current_price:.4f}",
                f"Today's High: {daily_high:.4f} | Today's Low: {daily_low:.4f}",
                f"Volume: {vol_str}",
                f"Momentum ({period} lookback): {pct_change:+.2f}%",
                "",
                "--- Recent Closing Prices ---",
            ]
            for date, row in hist.tail(5).iterrows():
                output.append(f"{str(date)[:10]}: {row['Close']:.4f}")

            return "\n".join(output)

        return await asyncio.to_thread(_fetch)

    except Exception as e:
        return f"Market Data Tool Error: {str(e)}"

if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: Static entry (NVDA) ---")
        print(await get_market_data("nvidia", period="1mo"))

        print("\n--- TEST 2: Live lookup (Palantir) ---")
        print(await get_market_data("palantir", period="5d"))

        print("\n--- TEST 3: Forex (EURUSD=X) ---")
        print(await get_market_data("EURUSD=X", period="5d"))

        print("\n--- TEST 4: Crypto by name ---")
        print(await get_market_data("ethereum", period="1d"))

    asyncio.run(run_test())