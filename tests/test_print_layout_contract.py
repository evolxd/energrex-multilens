from pathlib import Path

from price_zone_chart import render_price_zone_svg


APP_SOURCE = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")


REPORT = {
    "current_price": 126.80,
    "boundary": {
        "低价区上限": 71.01,
        "合适区上限": 84.96,
        "观察区上限": 129.34,
    },
    "scored_grid": [
        {"price": price, "valuation_score": score, "final_score": score}
        for price, score in ((40, 95), (71.01, 80), (84.96, 75), (129.34, 60), (150, 52))
    ],
}


def test_price_zone_svg_has_stable_responsive_coordinate_system():
    svg = render_price_zone_svg(REPORT)
    assert "viewBox='0 0 1000 250'" in svg
    assert "preserveAspectRatio='xMidYMid meet'" in svg
    assert "width:100%;height:auto" in svg


def test_price_zone_svg_contains_four_zones_and_four_vertical_lines():
    svg = render_price_zone_svg(REPORT)
    for label in ("低价区", "合适区", "观察区", "高价区", "当前价"):
        assert label in svg
    assert svg.count("stroke-dasharray='4 4'") == 3
    assert "stroke-width='2.2'" in svg


def test_rightmost_key_line_is_positioned_at_85_percent_of_plot():
    svg = render_price_zone_svg(REPORT)
    # p60 is the rightmost boundary in this fixture. Plot area is x=72..982,
    # therefore its expected position is 72 + 0.85 * 910 = 845.5.
    assert "x1='845.50'" in svg


def test_app_uses_native_svg_instead_of_plotly_for_price_zone():
    assert "st.markdown(render_price_zone_svg(_ps), unsafe_allow_html=True)" in APP_SOURCE
    assert "st.plotly_chart(\n            make_price_zone_chart(_ps)" not in APP_SOURCE
