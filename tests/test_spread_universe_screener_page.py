"""Unit tests for the private helpers to be extracted from
pages/7_🌐_期权价差全市场筛选.py's render(): _format_put_ranked_for_display,
_format_call_ranked_for_display, _pool_preview.

Unlike the single-ticker bull_put/bull_call pages, this page's render() has
no date-arithmetic closures (_dte/_release_risk_label) -- it never touches
datetime directly, DTE is a plain slider value passed straight through to
scoring/spread_universe_screener.py. The untested logic here is instead two
bare (not even closures) DataFrame-formatting blocks that round/percent-
format rank_put_screen_results()/rank_call_screen_results()'s raw numeric
columns for display -- scoring/spread_universe_screener.py's own tests only
cover the unformatted numeric output.

These tests lock the CURRENT formatting behavior exactly as written,
including one quirk worth flagging rather than "fixing" silently: this
page's `.round(n).astype(str) + "%"` pattern leaves a trailing ".0" on
whole-percent values (e.g. "811.0%") and no explicit "+" sign on positive
move_needed_pct, which differs from bull_call_spread_module.py's `_row`
(f"{x:.0f}%" / f"{x:+.1f}%"). That inconsistency predates this refactor and
is out of scope here -- flagged, not touched.

_pool_preview is pure Streamlit rendering (st.caption + st.dataframe) with
no return value to assert on; consistent with spread_ui_common.py's
pick_source_and_expirations() having zero test coverage for the same
reason, it is extracted (for render() line-count/structure) but not tested
here.

The page's filename is not a valid Python identifier (numeric prefix +
emoji + Chinese), so it can't be `import`ed normally -- reached the same
way spread_tool.py's "全市场扫描" branch reaches it: sys.path + importlib.
"""
import importlib
import pathlib
import sys
import unittest

import pandas as pd

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "pages"))
_screener_page = importlib.import_module("7_🌐_期权价差全市场筛选")

_format_put_ranked_for_display = _screener_page._format_put_ranked_for_display
_format_call_ranked_for_display = _screener_page._format_call_ranked_for_display


class FormatPutRankedForDisplayTests(unittest.TestCase):
    def test_rounds_and_percent_formats_the_right_columns(self):
        ranked = pd.DataFrame({
            "ticker": ["NVDA"],
            "net_credit": [2.567],
            "rom": [0.6666666],
            "adr": [8.11],
            "buffer_pct": [0.109],
            "total_score": [95.5],  # untouched passthrough column
        })

        display = _format_put_ranked_for_display(ranked)

        self.assertEqual(display["net_credit"].iloc[0], 2.57)
        self.assertEqual(display["rom"].iloc[0], "66.7%")
        self.assertEqual(display["adr"].iloc[0], "811.0%")
        self.assertEqual(display["buffer_pct"].iloc[0], "10.9%")

    def test_untouched_columns_pass_through_unchanged(self):
        ranked = pd.DataFrame({
            "ticker": ["NVDA"], "net_credit": [2.0], "rom": [0.5],
            "adr": [1.0], "buffer_pct": [0.05], "total_score": [88.2],
        })
        display = _format_put_ranked_for_display(ranked)
        self.assertEqual(display["ticker"].iloc[0], "NVDA")
        self.assertEqual(display["total_score"].iloc[0], 88.2)

    def test_does_not_mutate_the_caller_s_dataframe(self):
        """render() does `display = ranked.copy()` before mutating -- the
        original `ranked` (still used elsewhere, e.g. the CSV download
        button) must not be touched."""
        ranked = pd.DataFrame({
            "net_credit": [2.567], "rom": [0.6666666],
            "adr": [8.11], "buffer_pct": [0.109],
        })
        original_rom = ranked["rom"].iloc[0]

        _format_put_ranked_for_display(ranked)

        self.assertEqual(ranked["rom"].iloc[0], original_rom)
        self.assertIsInstance(ranked["rom"].iloc[0], float)


class FormatCallRankedForDisplayTests(unittest.TestCase):
    def test_rounds_and_percent_formats_the_right_columns(self):
        ranked = pd.DataFrame({
            "ticker": ["NVDA"],
            "net_debit": [3.456],
            "rom": [2.3333333],
            "adr": [14.1944444],
            "move_needed_pct": [-0.019047619],
            "total_score": [100.0],  # untouched passthrough column
        })

        display = _format_call_ranked_for_display(ranked)

        self.assertEqual(display["net_debit"].iloc[0], 3.46)
        self.assertEqual(display["rom"].iloc[0], "233.3%")
        self.assertEqual(display["adr"].iloc[0], "1419.0%")
        self.assertEqual(display["move_needed_pct"].iloc[0], "-1.9%")

    def test_untouched_columns_pass_through_unchanged(self):
        ranked = pd.DataFrame({
            "ticker": ["NVDA"], "net_debit": [1.0], "rom": [1.0],
            "adr": [1.0], "move_needed_pct": [0.0], "total_score": [77.7],
        })
        display = _format_call_ranked_for_display(ranked)
        self.assertEqual(display["ticker"].iloc[0], "NVDA")
        self.assertEqual(display["total_score"].iloc[0], 77.7)

    def test_columns_are_not_swapped_between_put_and_call(self):
        """The put/call formatting blocks are structurally parallel but
        touch different column names (buffer_pct vs move_needed_pct) -- the
        exact copy-paste mistake this test guards against is one function
        accidentally formatting the other's column name."""
        ranked = pd.DataFrame({
            "net_debit": [1.0], "rom": [0.5], "adr": [1.0],
            "move_needed_pct": [0.05],
        })
        display = _format_call_ranked_for_display(ranked)
        self.assertNotIn("buffer_pct", display.columns)
        self.assertEqual(display["move_needed_pct"].iloc[0], "5.0%")

    def test_does_not_mutate_the_caller_s_dataframe(self):
        ranked = pd.DataFrame({
            "net_debit": [3.456], "rom": [2.3333333],
            "adr": [14.1944444], "move_needed_pct": [-0.019047619],
        })
        original_adr = ranked["adr"].iloc[0]

        _format_call_ranked_for_display(ranked)

        self.assertEqual(ranked["adr"].iloc[0], original_adr)
        self.assertIsInstance(ranked["adr"].iloc[0], float)


if __name__ == "__main__":
    unittest.main()
