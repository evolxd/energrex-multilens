"""Unit tests for spread_ui_common.py's dte() and release_risk_label().

Consolidated 2026-09-24 from tests/test_bull_put_spread_module.py and
tests/test_bull_call_spread_module.py, which each had a byte-identical
copy of these test classes (mirroring the byte-identical duplicate
functions in bull_put_spread_module.py / bull_call_spread_module.py that
got moved here). Do not re-add these cases to the two page-specific test
files -- this file is now the single source of truth for them.

pick_source_and_expirations() is not covered here -- it was already
untested before this consolidation (it needs a live/mocked options-chain
data source and several Streamlit widgets), and this refactor's scope is
strictly the two functions being moved, not a general test-coverage pass
on this module.
"""
import datetime
import unittest
from unittest.mock import patch

# spread_ui_common must import first: it inserts scoring/ onto sys.path as a
# side effect, which the bare `import macro_calendar` below relies on.
from spread_ui_common import dte, release_risk_label  # noqa: E402
import macro_calendar


class DteTests(unittest.TestCase):
    def test_days_between_today_and_expiration(self):
        self.assertEqual(dte("2026-01-31", datetime.date(2026, 1, 1)), 30)

    def test_zero_when_expiration_is_today(self):
        self.assertEqual(dte("2026-01-01", datetime.date(2026, 1, 1)), 0)

    def test_negative_when_expiration_already_passed(self):
        self.assertEqual(dte("2025-12-25", datetime.date(2026, 1, 1)), -7)


class ReleaseRiskLabelTests(unittest.TestCase):
    def test_delegates_to_macro_calendar_with_explicit_today(self):
        today = datetime.date(2026, 9, 24)
        with patch.object(macro_calendar, "release_risk_label", return_value="__SENTINEL__") as mocked:
            result = release_risk_label("2026-10-16", today)
        mocked.assert_called_once_with(today, "2026-10-16")
        self.assertEqual(result, "__SENTINEL__")


if __name__ == "__main__":
    unittest.main()
