from __future__ import annotations

import datetime
import json
import os
import urllib.request
from typing import Any


MD_BASE = "https://api.marketdata.app/v1"


def marketdata_key() -> str:
    return os.environ.get("MARKETDATA_API_KEY", "")


def _first_value(data: dict[str, Any], key: str) -> Any:
    value = data.get(key)
    return value[0] if isinstance(value, list) and value else value


def fetch_option_quote(symbol: str, api_key: str | None = None, logger: Any = None) -> dict | None:
    """Fetch one option quote from MarketData.app."""
    token = api_key if api_key is not None else marketdata_key()
    if not token:
        return None
    try:
        url = f"{MD_BASE}/options/quotes/{symbol}/?token={token}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("s") != "ok":
            if logger:
                logger.warning(f"MD quote {symbol}: {data.get('errmsg', '')}")
            return None
        return {
            "mid": _first_value(data, "mid"),
            "last": _first_value(data, "last"),
            "bid": _first_value(data, "bid"),
            "ask": _first_value(data, "ask"),
            "change": _first_value(data, "change"),
            "changepct": _first_value(data, "changepct"),
            "iv": _first_value(data, "iv"),
            "delta": _first_value(data, "delta"),
            "gamma": _first_value(data, "gamma"),
            "theta": _first_value(data, "theta"),
            "vega": _first_value(data, "vega"),
        }
    except Exception as exc:
        if logger:
            logger.warning(f"MD quote {symbol} error: {exc}")
        return None


def fetch_underlying_prices(tickers_tuple: tuple, logger: Any = None) -> dict:
    """Fetch latest underlying prices through yfinance."""
    import yfinance as yf

    result = {}
    try:
        data = yf.download(list(tickers_tuple), period="1d", progress=False, auto_adjust=True)
        closes = data["Close"] if "Close" in data else data
        for ticker in tickers_tuple:
            try:
                value = closes[ticker].dropna().iloc[-1] if ticker in closes.columns else None
                if value is not None:
                    result[ticker] = float(value)
            except Exception:
                pass
    except Exception as exc:
        if logger:
            logger.warning(f"yfinance underlying prices error: {exc}")
    return result


def get_spot_prices_batch(tickers: tuple) -> dict[str, float]:
    """Fetch spot prices through yfinance fast_info."""
    import yfinance as yf

    prices = {}
    for ticker in tickers:
        try:
            price = yf.Ticker(ticker).fast_info.last_price
            if price:
                prices[ticker] = float(price)
        except Exception:
            pass
    return prices


def get_vix_snapshot() -> dict:
    """Fetch the VIX level and daily percentage change."""
    import yfinance as yf

    try:
        vix = yf.Ticker("^VIX")
        fast_info = vix.fast_info
        last = float(fast_info.last_price or 0)
        prev = float(fast_info.previous_close or last)
        change_pct = (last - prev) / prev * 100 if prev else 0
        return {"vix": round(last, 2), "change_pct": round(change_pct, 2)}
    except Exception:
        return {"vix": None, "change_pct": None}


def get_atm_iv_batch(tickers: tuple, api_key: str | None = None) -> dict[str, dict]:
    """
    Fetch near-30 DTE ATM IV.

    MarketData.app is tried first; yfinance option chains are used as fallback.
    Returns {ticker: {"iv": decimal_iv, "src": "md"|"yf"}}.
    """
    import yfinance as yf

    token = api_key if api_key is not None else marketdata_key()
    result: dict[str, dict] = {}
    today = datetime.date.today()

    for underlying in tickers:
        if token:
            try:
                url = (
                    f"{MD_BASE}/options/chain/{underlying}/"
                    f"?side=call&minDte=20&maxDte=50&token={token}"
                )
                with urllib.request.urlopen(url, timeout=8) as resp:
                    data = json.loads(resp.read())
                if data.get("s") == "ok":
                    ivs = data.get("iv") or []
                    strikes = data.get("strike") or []
                    underlyings = data.get("underlying") or []
                    if ivs and strikes:
                        spot = float(underlyings[0]) if underlyings else None
                        if spot:
                            diffs = [abs(float(strike) - spot) for strike in strikes]
                            idx = diffs.index(min(diffs))
                            iv_value = float(ivs[idx]) if ivs[idx] else None
                            if iv_value and 0.05 < iv_value < 5.0:
                                result[underlying] = {"iv": iv_value, "src": "md"}
                                continue
            except Exception:
                pass

        try:
            ticker = yf.Ticker(underlying)
            price = ticker.fast_info.last_price
            if not price:
                continue
            expiries = ticker.options
            if not expiries:
                continue
            best_expiry = min(
                expiries,
                key=lambda exp: abs((datetime.date.fromisoformat(exp) - today).days - 30),
            )
            chain = ticker.option_chain(best_expiry)
            calls = chain.calls[chain.calls["impliedVolatility"] > 0.01]
            if calls.empty:
                continue
            call_idx = (calls["strike"] - price).abs().idxmin()
            call_iv = float(calls.loc[call_idx, "impliedVolatility"])
            puts = chain.puts[chain.puts["impliedVolatility"] > 0.01]
            if not puts.empty:
                put_idx = (puts["strike"] - price).abs().idxmin()
                atm_iv = (call_iv + float(puts.loc[put_idx, "impliedVolatility"])) / 2
            else:
                atm_iv = call_iv
            if 0.05 < atm_iv < 5.0:
                result[underlying] = {"iv": atm_iv, "src": "yf"}
        except Exception:
            pass

    return result
