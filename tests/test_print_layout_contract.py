from pathlib import Path


APP_TEXT = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8-sig")


def test_print_layout_reflows_plotly_to_actual_host_width():
    assert "w.Plotly.relayout(gd, {width: width, autosize: false});" in APP_TEXT
    assert '[data-testid="stPlotlyChart"] svg {' not in APP_TEXT


def test_score_visual_reserves_more_width_for_exact_bar_labels():
    assert "grid-template-columns: minmax(0, 44fr) minmax(0, 56fr)" in APP_TEXT
    assert 'textposition="outside"' in APP_TEXT
    assert 'cliponaxis=False' in APP_TEXT


def test_print_score_visual_uses_native_svg_instead_of_plotly():
    assert "def render_print_score_visuals" in APP_TEXT
    assert "viewBox='0 0 320 248'" in APP_TEXT
    assert "viewBox='0 0 400 230'" in APP_TEXT
    assert "score-screen-anchor" in APP_TEXT
    assert "st.markdown(render_print_score_visuals(row)" in APP_TEXT


def test_numeric_legend_is_scoped_to_the_subscore_column():
    right_column = APP_TEXT.split("with rc2:", 1)[1]
    assert "score-dimension-legend" in right_column
    assert "_score_legend_html" in right_column
