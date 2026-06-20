"""Option symbol parsing helpers."""

from __future__ import annotations

import re

OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_occ(symbol: str) -> dict:
    """Parse an OCC option symbol.

    `direction` is kept for legacy callers and means option type, not position
    side. New code should prefer `option_type` or `call_put`.
    """
    match = OCC_RE.match(symbol.strip().upper().replace(" ", ""))
    if not match:
        return {}
    root, yy, mm, dd, cp, strike_raw = match.groups()
    option_type = "call" if cp == "C" else "put"
    return {
        "root": root,
        "option_type": option_type,
        "call_put": cp,
        "direction": "Call" if cp == "C" else "Put",
        "strike": int(strike_raw) / 1000,
        "expiry": f"20{yy}-{mm}-{dd}",
    }


def parse_occ_sym(symbol: str) -> dict | None:
    """Parse an OCC option symbol into the scraped-position shape."""
    parsed = parse_occ(symbol)
    if not parsed:
        return None
    return {
        "underlying": parsed["root"],
        "expiry": parsed["expiry"],
        "strike": parsed["strike"],
        "call_put": parsed["call_put"],
    }


def option_market_value(quantity: float, price: float, multiplier: int = 100) -> float:
    """Return signed option market value; short positions remain negative."""
    return float(quantity or 0) * float(price or 0) * multiplier
