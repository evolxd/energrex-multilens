import datetime
import unittest

import pandas as pd

from account.systemic_risk_signal import (
    FOMC_DECISION_DATES_2026,
    fomc_event_risk,
    qqq_trend_break,
)


class FomcEventRiskTests(unittest.TestCase):
    def test_true_on_decision_day(self):
        self.assertTrue(fomc_event_risk(datetime.date(2026, 9, 16)))

    def test_true_day_before_and_after(self):
        self.assertTrue(fomc_event_risk(datetime.date(2026, 9, 15)))
        self.assertTrue(fomc_event_risk(datetime.date(2026, 9, 17)))

    def test_false_outside_window(self):
        self.assertFalse(fomc_event_risk(datetime.date(2026, 9, 20)))

    def test_false_between_meetings(self):
        self.assertFalse(fomc_event_risk(datetime.date(2026, 8, 15)))

    def test_window_days_is_configurable(self):
        self.assertFalse(fomc_event_risk(datetime.date(2026, 9, 14), window_days=0))
        self.assertTrue(fomc_event_risk(datetime.date(2026, 9, 14), window_days=2))

    def test_custom_meeting_dates_override_default(self):
        custom = (datetime.date(2027, 1, 1),)
        self.assertTrue(fomc_event_risk(datetime.date(2027, 1, 1), meeting_dates=custom))
        # A 2026 default date should NOT fire once a custom calendar is given.
        self.assertFalse(fomc_event_risk(datetime.date(2026, 9, 16), meeting_dates=custom))

    def test_2026_calendar_has_eight_meetings(self):
        self.assertEqual(len(FOMC_DECISION_DATES_2026), 8)


class QqqTrendBreakTests(unittest.TestCase):
    def _closes(self, values):
        return pd.Series(values)

    def test_insufficient_history_is_safe_false(self):
        result = qqq_trend_break(self._closes([100.0] * 5))
        self.assertFalse(result["trend_break"])
        self.assertEqual(result["reason"], "insufficient_history")

    def test_none_history_is_safe_false(self):
        result = qqq_trend_break(None)
        self.assertFalse(result["trend_break"])

    def test_price_below_200dma_triggers_trend_break(self):
        # 250 days flat at 500, then a sharp recent drop -- 200DMA stays near
        # 500 while the latest close sits well under it.
        closes = [500.0] * 250 + [400.0]
        result = qqq_trend_break(self._closes(closes))
        self.assertTrue(result["trend_break"])
        self.assertTrue(result["below_200"])

    def test_price_above_moving_averages_is_no_break(self):
        closes = [500.0] * 250
        result = qqq_trend_break(self._closes(closes))
        self.assertFalse(result["trend_break"])
        self.assertFalse(result["below_50"])
        self.assertFalse(result["below_200"])

    def test_below_50_but_above_200_does_not_set_trend_break(self):
        # Long flat history keeps the 200DMA low; a milder recent pullback
        # only dips under the 50DMA, not the 200DMA -- hedge_governance's
        # "major trend break" trigger should stay off in this case.
        closes = [200.0] * 200 + [260.0] * 49 + [255.0]
        result = qqq_trend_break(self._closes(closes))
        self.assertFalse(result["trend_break"])
