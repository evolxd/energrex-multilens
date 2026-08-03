"""Regression tests for PRICE_BOUNDARY_STANDARD.md chart readability rules.

These exist because the module shipped for a long time with no coverage at
all, and two separate x-axis framing regressions reached the user's screen
before anyone noticed. The rules asserted here are the ones the standard
states explicitly; each test names the rule it guards.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from price_zone_chart import render_price_zone_svg

PLOT_LEFT = 72.0
PLOT_RIGHT = 982.0
PLOT_WIDTH = PLOT_RIGHT - PLOT_LEFT
PLOT_TOP = 22.0
PLOT_BOTTOM = 194.0
PLOT_HEIGHT = PLOT_BOTTOM - PLOT_TOP

CURRENT_LINE_RE = re.compile(
    r"<line x1='([\d.]+)' y1='22\.0' x2='[\d.]+' y2='194\.0' stroke='#1E1E1B' stroke-width='2\.2'/>"
)
BOUNDARY_LINE_RE = re.compile(r"<line x1='([\d.]+)'[^>]*stroke-dasharray='4 4'/>")
POLYLINE_RE = re.compile(r"<polyline points='([^']+)'")


def build_grid(current, score_fn):
    """5%-400% of current price, matching the standard's simulation grid."""
    return [
        {"price": current * pct / 100.0, "valuation_score": score_fn(current * pct / 100.0)}
        for pct in range(5, 401)
    ]


def current_line_fraction(svg):
    match = CURRENT_LINE_RE.search(svg)
    assert match, "current-price marker must always be rendered"
    return (float(match.group(1)) - PLOT_LEFT) / PLOT_WIDTH


def curve_score_span(svg):
    match = POLYLINE_RE.search(svg)
    assert match, "valuation-score curve must always be rendered"
    ys = [float(point.split(",")[1]) for point in match.group(1).split()]
    scores = [(PLOT_BOTTOM - y) / PLOT_HEIGHT * 100.0 for y in ys]
    return max(scores) - min(scores)


def declining_score(current):
    """Crosses 80 / 75 / 60 inside the grid, so all three boundaries exist."""
    return lambda price: max(0.0, min(100.0, 95.0 - 30.0 * (price / current)))


def always_cheap_score(current):
    """TSM-like: never drops below 80, so no boundary exists anywhere."""
    return lambda price: max(0.0, min(100.0, 100.0 - 8.0 * (price / current - 0.3)))


def flat_score(_current):
    return lambda _price: 95.0


def test_rightmost_line_sits_at_85_percent_of_plot_width():
    """Standard: 'the rightmost line ... should sit at about 85% of plot width'."""
    current = 200.0
    grid = build_grid(current, declining_score(current))
    report = {
        "current_price": current,
        "boundary": {"低价区上限": 100.0, "合适区上限": 133.0, "观察区上限": 233.0},
        "scored_grid": grid,
    }
    svg = render_price_zone_svg(report)

    xs = [float(x) for x in BOUNDARY_LINE_RE.findall(svg)]
    xs.append(float(CURRENT_LINE_RE.search(svg).group(1)))
    rightmost_fraction = (max(xs) - PLOT_LEFT) / PLOT_WIDTH
    assert rightmost_fraction == pytest.approx(0.85, abs=0.02)


def test_all_three_boundary_lines_render_when_boundaries_exist():
    current = 200.0
    report = {
        "current_price": current,
        "boundary": {"低价区上限": 100.0, "合适区上限": 133.0, "观察区上限": 233.0},
        "scored_grid": build_grid(current, declining_score(current)),
    }
    assert len(BOUNDARY_LINE_RE.findall(render_price_zone_svg(report))) == 3


def test_no_boundary_case_invents_no_boundary_line():
    """Standard: 'If the module cannot calculate a boundary ... Do not invent a price.'"""
    current = 404.2
    report = {
        "current_price": current,
        "boundary": {"低价区上限": None, "合适区上限": None, "观察区上限": None},
        "scored_grid": build_grid(current, always_cheap_score(current)),
    }
    assert BOUNDARY_LINE_RE.findall(render_price_zone_svg(report)) == []


def test_no_boundary_case_keeps_current_price_off_the_edges():
    """Guards both shipped regressions: current price jammed at 85% (fabricated
    zoom anchor), then at 10% (curve-flatness trim overshooting left)."""
    current = 404.2
    report = {
        "current_price": current,
        "boundary": {"低价区上限": None, "合适区上限": None, "观察区上限": None},
        "scored_grid": build_grid(current, always_cheap_score(current)),
    }
    fraction = current_line_fraction(render_price_zone_svg(report))
    assert 0.15 <= fraction <= 0.75


def test_no_boundary_case_window_is_not_visually_flat():
    """Guards the 'flat dead line across most of the plot' screenshot."""
    current = 404.2
    report = {
        "current_price": current,
        "boundary": {"低价区上限": None, "合适区上限": None, "观察区上限": None},
        "scored_grid": build_grid(current, always_cheap_score(current)),
    }
    assert curve_score_span(render_price_zone_svg(report)) >= 3.0


def test_genuinely_flat_curve_falls_back_to_full_grid():
    """When no window can show movement, show the whole simulated range
    rather than an arbitrary zoom that implies detail that isn't there."""
    current = 100.0
    grid = build_grid(current, flat_score(current))
    report = {
        "current_price": current,
        "boundary": {"低价区上限": None, "合适区上限": None, "观察区上限": None},
        "scored_grid": grid,
    }
    svg = render_price_zone_svg(report)
    ticks = re.findall(r"class='tick'>\$([\d,.]+)</text>", svg)
    rendered_min = float(ticks[0].replace(",", ""))
    rendered_max = float(ticks[-1].replace(",", ""))
    assert rendered_min == pytest.approx(min(row["price"] for row in grid), rel=0.01)
    assert rendered_max == pytest.approx(max(row["price"] for row in grid), rel=0.01)


def test_current_price_marker_always_inside_plot_area():
    """The marker must stay visible in every branch, including the fallback."""
    for score_fn, boundary in (
        (declining_score, {"低价区上限": 100.0, "合适区上限": 133.0, "观察区上限": 233.0}),
        (always_cheap_score, {"低价区上限": None, "合适区上限": None, "观察区上限": None}),
        (flat_score, {"低价区上限": None, "合适区上限": None, "观察区上限": None}),
    ):
        current = 200.0
        report = {
            "current_price": current,
            "boundary": boundary,
            "scored_grid": build_grid(current, score_fn(current)),
        }
        fraction = current_line_fraction(render_price_zone_svg(report))
        assert 0.0 <= fraction <= 1.0
