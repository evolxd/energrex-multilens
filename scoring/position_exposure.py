"""Measure the three exposures the portfolio limits govern.

Kept separate from position_limits.py: that module decides whether a rule may
change, this one reads what the portfolio currently looks like.

Two traps here, both of the same shape: measuring part of a portfolio and
reporting it as the whole.

The first is options. An account can hold the large majority of its equity in
option spreads and only a few percent in stock; reading only the stock table
then reports "largest single name 5%, all clear" while one underlying actually
stands at a large share of the account. Options are folded in by underlying.

The second is unclassified holdings. TICKER_CATEGORY only covers the tracked
universe, so anything outside it has no chain. Dropping those rows would
understate chain concentration exactly when it matters. They are collected
under an explicit "未分类" chain instead.

Exposure per underlying is netted before it is made absolute. A spread that is
long 38k and short 23k of the same name is a 15k bet, not a 61k one; taking
each leg's magnitude separately would treat a hedge as double the risk of an
outright.

Options are measured at market value -- capital currently committed. That is
not the same as delta-adjusted notional, which is what the position would be
worth as stock; for a leveraged option book the latter can exceed equity.
Market value is the conservative, offline-computable reading, and the two
answer different questions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

UNCLASSIFIED = "未分类"


@dataclass
class Exposures:
    total_equity: float
    cash_pct: float
    by_ticker_pct: dict[str, float] = field(default_factory=dict)
    by_chain_pct: dict[str, float] = field(default_factory=dict)
    unclassified_tickers: list[str] = field(default_factory=list)

    @property
    def max_single_stock_pct(self) -> float:
        return max(self.by_ticker_pct.values(), default=0.0)

    @property
    def max_single_stock_ticker(self) -> str | None:
        if not self.by_ticker_pct:
            return None
        return max(self.by_ticker_pct, key=self.by_ticker_pct.get)

    @property
    def max_chain_pct(self) -> float:
        return max(self.by_chain_pct.values(), default=0.0)

    @property
    def max_chain_name(self) -> str | None:
        if not self.by_chain_pct:
            return None
        return max(self.by_chain_pct, key=self.by_chain_pct.get)

    def for_limit(self, key: str) -> float | None:
        """The reading that the named limit governs."""
        if key == "single_stock_max":
            return self.max_single_stock_pct
        if key == "chain_max":
            return self.max_chain_pct
        if key == "cash_floor_min":
            return self.cash_pct
        return None


def underlying_of(symbol: str) -> str:
    """The stock behind an OCC option symbol, or the symbol itself."""
    try:
        from account.options import OCC_RE
    except Exception:
        return symbol
    match = OCC_RE.match(symbol.strip().upper().replace(" ", ""))
    return match.group(1) if match else symbol


def _signed_value(row: dict) -> float | None:
    try:
        return float(row.get("market_value") or 0.0)
    except (TypeError, ValueError):
        return None


def compute_exposures(
    positions: list[dict],
    total_equity: float | None,
    cash_balance: float | None,
    category_of,
    option_positions: list[dict] | None = None,
) -> Exposures | None:
    """Build exposures from raw position rows.

    Rows need `symbol` and `market_value`. Option rows are grouped by the
    underlying parsed out of their OCC symbol. `category_of` maps a symbol to
    a chain name, returning None when it does not know it. Returns None when
    total equity is unusable, because every figure here is a percentage of it
    and a zero denominator would silently produce nonsense.
    """
    if not total_equity or total_equity <= 0:
        return None

    # Net first, make absolute later: long and short legs of one spread offset.
    net_by_ticker: dict[str, float] = {}
    for row in positions or []:
        symbol = (row.get("symbol") or "").strip().upper()
        value = _signed_value(row)
        if not symbol or value is None:
            continue
        net_by_ticker[symbol] = net_by_ticker.get(symbol, 0.0) + value

    for row in option_positions or []:
        symbol = (row.get("symbol") or "").strip().upper()
        value = _signed_value(row)
        if not symbol or value is None:
            continue
        name = underlying_of(symbol)
        net_by_ticker[name] = net_by_ticker.get(name, 0.0) + value

    by_ticker_pct = {
        s: abs(v) / total_equity * 100.0 for s, v in net_by_ticker.items() if v
    }

    by_chain_pct: dict[str, float] = {}
    unclassified: list[str] = []
    for symbol, pct in by_ticker_pct.items():
        chain = category_of(symbol)
        if not chain:
            chain = UNCLASSIFIED
            unclassified.append(symbol)
        by_chain_pct[chain] = by_chain_pct.get(chain, 0.0) + pct

    cash_pct = (float(cash_balance or 0.0) / total_equity) * 100.0

    return Exposures(
        total_equity=float(total_equity),
        cash_pct=cash_pct,
        by_ticker_pct=by_ticker_pct,
        by_chain_pct=by_chain_pct,
        unclassified_tickers=sorted(unclassified),
    )


def breaches(exposures: Exposures, limits: dict[str, float | None]) -> list[dict]:
    """Every limit currently violated, worst overshoot first.

    A limit with no value set yet is not a breach -- it is an unanswered
    question, and reporting it as compliant would be worse than saying nothing.
    """
    from scoring.position_limits import LIMIT_BY_KEY

    found = []
    for key, limit_value in limits.items():
        spec = LIMIT_BY_KEY.get(key)
        if spec is None or limit_value is None:
            continue
        reading = exposures.for_limit(key)
        if reading is None:
            continue
        if spec.is_breached(reading, float(limit_value)):
            overshoot = (
                reading - float(limit_value)
                if spec.kind == "max"
                else float(limit_value) - reading
            )
            found.append(
                {
                    "key": key,
                    "label": spec.label,
                    "limit": float(limit_value),
                    "reading": reading,
                    "overshoot": overshoot,
                    "detail": _detail_for(exposures, key),
                }
            )
    return sorted(found, key=lambda item: item["overshoot"], reverse=True)


def approaching(
    exposures: Exposures,
    limits: dict[str, float | None],
    warn_ratio: float = 0.9,
) -> list[dict]:
    """Limits at or past `warn_ratio` of their allowance but not yet breached."""
    from scoring.position_limits import LIMIT_BY_KEY

    breached_keys = {item["key"] for item in breaches(exposures, limits)}
    found = []
    for key, limit_value in limits.items():
        spec = LIMIT_BY_KEY.get(key)
        if spec is None or limit_value is None or key in breached_keys:
            continue
        reading = exposures.for_limit(key)
        if reading is None:
            continue
        limit_value = float(limit_value)
        if spec.kind == "max":
            near = limit_value > 0 and reading >= limit_value * warn_ratio
        else:
            # A floor is approached from above: 11% cash against a 10% floor.
            near = reading <= limit_value / warn_ratio if warn_ratio else False
        if near:
            found.append(
                {
                    "key": key,
                    "label": spec.label,
                    "limit": limit_value,
                    "reading": reading,
                    "detail": _detail_for(exposures, key),
                }
            )
    return found


def _detail_for(exposures: Exposures, key: str) -> str:
    if key == "single_stock_max":
        return exposures.max_single_stock_ticker or ""
    if key == "chain_max":
        return exposures.max_chain_name or ""
    return ""
