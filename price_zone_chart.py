"""Responsive, print-stable SVG renderer for the ENERGREX price zones."""

from __future__ import annotations

from math import isfinite


def _valid_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0 else None


def _fmt_price(value: float) -> str:
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 10:
        return f"${value:,.1f}".rstrip("0").rstrip(".")
    return f"${value:,.2f}".rstrip("0").rstrip(".")


def render_price_zone_svg(report: dict) -> str:
    """Render one responsive SVG; its viewBox is identical on screen and print."""
    current = float(report["current_price"])
    boundary = report.get("boundary", {})
    p60 = _valid_price(boundary.get("观察区上限"))
    p75 = _valid_price(boundary.get("合适区上限"))
    p80 = _valid_price(boundary.get("低价区上限"))
    scored = [
        row for row in report.get("scored_grid", [])
        if _valid_price(row.get("price")) is not None
    ]
    grid_prices = [float(row["price"]) for row in scored] or [current * 0.5, current * 1.5]
    anchors = [value for value in (current, p60, p75, p80) if value]

    x_min = max(min(grid_prices), min(anchors) * 0.55)
    rightmost_line = max(anchors)
    if rightmost_line > x_min:
        # The furthest of the three boundaries/current-price lines sits at 85%.
        x_max = min(max(grid_prices), x_min + (rightmost_line - x_min) / 0.85)
    else:
        x_max = min(max(grid_prices), rightmost_line * 1.18)
    if x_max <= x_min:
        x_min, x_max = min(grid_prices), max(grid_prices)

    width, height = 1000.0, 250.0
    left, right, top, bottom = 72.0, 982.0, 22.0, 194.0
    plot_width, plot_height = right - left, bottom - top

    def sx(price: float) -> float:
        return left + (price - x_min) / (x_max - x_min) * plot_width

    def sy(score: float) -> float:
        score = max(0.0, min(100.0, score))
        return bottom - score / 100.0 * plot_height

    raw_zones = [
        (x_min, p80, "低价区", "#2F4A3C"),
        (p80, p75, "合适区", "#4A6B5C"),
        (p75, p60, "观察区", "#A67C3D"),
        (p60, x_max, "高价区", "#8B3A2E"),
    ]
    zones = []
    for start, end, label, color in raw_zones:
        if start is None or end is None or end <= start:
            continue
        start, end = max(x_min, start), min(x_max, end)
        if end > start:
            zones.append((start, end, label, color))

    zone_svg = []
    for start, end, label, color in zones:
        x0, x1 = sx(start), sx(end)
        center = (x0 + x1) / 2
        zone_svg.append(
            f"<rect x='{x0:.2f}' y='{top:.2f}' width='{x1-x0:.2f}' height='{plot_height:.2f}' "
            f"fill='{color}' fill-opacity='0.13'/>"
        )
        zone_svg.append(
            f"<rect x='{center-38:.2f}' y='101' width='76' height='25' rx='3' "
            "fill='#FAF8F3' fill-opacity='0.88' stroke='#CFC8B8' stroke-width='0.8'/>"
            f"<text x='{center:.2f}' y='118' text-anchor='middle' class='zone-label'>{label}</text>"
        )

    grid_svg = []
    for score in (45, 60, 75, 80):
        y = sy(score)
        grid_svg.append(
            f"<line x1='{left}' y1='{y:.2f}' x2='{right}' y2='{y:.2f}' class='grid-line'/>"
            f"<text x='{left-12}' y='{y+4:.2f}' text-anchor='end' class='tick'>{score}</text>"
        )

    boundary_svg = []
    for price, color in ((p80, "#2F4A3C"), (p75, "#4A6B5C"), (p60, "#A67C3D")):
        if price and x_min <= price <= x_max:
            x = sx(price)
            boundary_svg.append(
                f"<line x1='{x:.2f}' y1='{top}' x2='{x:.2f}' y2='{bottom}' "
                f"stroke='{color}' stroke-width='1.3' stroke-dasharray='4 4'/>"
            )

    current_x = sx(current)
    current_anchor = "end" if current_x > right - 55 else "middle"
    current_text_x = current_x - 5 if current_anchor == "end" else current_x
    current_svg = (
        f"<line x1='{current_x:.2f}' y1='{top}' x2='{current_x:.2f}' y2='{bottom}' "
        "stroke='#1E1E1B' stroke-width='2.2'/>"
        f"<text x='{current_text_x:.2f}' y='15' text-anchor='{current_anchor}' class='current-label'>"
        f"当前价 {_fmt_price(current)}</text>"
    )

    curve_points = []
    for row in scored:
        price = float(row["price"])
        if x_min <= price <= x_max:
            curve_points.append(f"{sx(price):.2f},{sy(float(row.get('valuation_score', 0))):.2f}")
    curve_svg = (
        f"<polyline points='{' '.join(curve_points)}' fill='none' stroke='#1E1E1B' "
        "stroke-width='2.2' stroke-linejoin='round' stroke-linecap='round'/>"
        if curve_points else ""
    )

    tick_svg = []
    for index in range(5):
        price = x_min + (x_max - x_min) * index / 4
        x = sx(price)
        anchor = "start" if index == 0 else "end" if index == 4 else "middle"
        tick_svg.append(
            f"<text x='{x:.2f}' y='216' text-anchor='{anchor}' class='tick'>{_fmt_price(price)}</text>"
        )

    return (
        "<div class='price-zone-svg-wrap'>"
        "<svg class='price-zone-svg' viewBox='0 0 1000 250' "
        "preserveAspectRatio='xMidYMid meet' role='img' "
        "aria-label='价格温度带：低价区、合适区、观察区、高价区及当前价格'>"
        "<style>"
        ".price-zone-svg{display:block;width:100%;height:auto;background:#FAF8F3;}"
        ".price-zone-svg text{font-family:Arial,'Microsoft YaHei',sans-serif;fill:#4F4A40;}"
        ".price-zone-svg .tick{font-size:11px;}"
        ".price-zone-svg .zone-label{font-size:13px;font-weight:700;fill:#1E1E1B;}"
        ".price-zone-svg .current-label{font-size:12px;font-weight:700;fill:#1E1E1B;}"
        ".price-zone-svg .grid-line{stroke:#BEB7A8;stroke-width:.8;stroke-opacity:.45;}"
        "</style>"
        f"<rect x='{left}' y='{top}' width='{plot_width}' height='{plot_height}' fill='#F3F0E8'/>"
        + "".join(zone_svg)
        + "".join(grid_svg)
        + curve_svg
        + "".join(boundary_svg)
        + current_svg
        + "".join(tick_svg)
        + "<text x='527' y='243' text-anchor='middle' class='tick'>价格区间</text>"
        + "<text x='17' y='112' text-anchor='middle' class='tick' transform='rotate(-90 17 112)'>"
        "估值温度 / Valuation Score</text>"
        "</svg></div>"
    )

