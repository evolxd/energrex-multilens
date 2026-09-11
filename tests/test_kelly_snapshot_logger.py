"""Tests for scoring/kelly_snapshot_logger.py -- 每日 score+维度 快照记录。

2026-09-10 审计发现：快照之前只存 final_score，没法做时点正确的维度级
验证（哪个维度真的有预测力、风险扣分是拖累还是保护）。这批测试锁定
新增的六维度+风险扣分+熔断标记列，以及新旧快照混存时的向后兼容
（老日期的行在新列上应该是 NaN，不是崩溃或被拿新数据回填）。
"""
import pathlib
import tempfile
import unittest

import pandas as pd

from scoring import kelly_snapshot_logger as logger


class LogSnapshotDimensionColumnsTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_path = logger._SNAPSHOT_PATH
        logger._SNAPSHOT_PATH = pathlib.Path(self._tmp.name) / "score_snapshots.csv"

    def tearDown(self):
        logger._SNAPSHOT_PATH = self._original_path
        self._tmp.cleanup()

    def _df(self, **overrides):
        row = {
            "ticker": "NVDA",
            "final_综合得分(0-100)": 82.5,
            "raw_current_price_yf": "182.30 [yf]",
            "val_估值得分(PEG/EV/ERG/PE/FCFYld)": 70.0,
            "grw_成长得分(营收/EPS/FCF/指引增速)": 88.0,
            "qlt_质量得分(毛利率/FCF率/ROIC/负债)": 75.0,
            "ai_AI暴露得分(AI营收/平台/订单占比)": 95.0,
            "exp_预期差得分(超预期营收EPS指引)": 60.0,
            "mom_动量得分(RSI14/价格vs200日均)": 55.0,
            "risk_风险扣分(max20,Beta/回撤/负债)": 4.5,
            # 生产 CSV 里这一列真实的样子：触发是字符串 "YES"，没触发是空
            # 单元格——不是 Python 布尔值。
            "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)": float("nan"),
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_dimension_columns_land_in_snapshot(self):
        logger.log_snapshot(self._df())
        out = pd.read_csv(logger._SNAPSHOT_PATH)
        r = out.iloc[0]
        self.assertEqual(r["valuation"], 70.0)
        self.assertEqual(r["growth"], 88.0)
        self.assertEqual(r["quality"], 75.0)
        self.assertEqual(r["ai_exposure"], 95.0)
        self.assertEqual(r["expectation_gap"], 60.0)
        self.assertEqual(r["momentum"], 55.0)
        self.assertEqual(r["risk_penalty"], 4.5)
        self.assertEqual(bool(r["circuit_triggered"]), False)

    def test_circuit_triggered_yes_string_is_preserved(self):
        logger.log_snapshot(self._df(**{
            "circuit_熔断(Beta>2.2且回撤>35%或DE>1.8)": "YES",
        }))
        out = pd.read_csv(logger._SNAPSHOT_PATH)
        self.assertEqual(bool(out.iloc[0]["circuit_triggered"]), True)

    def test_circuit_not_triggered_maps_to_false_not_nan(self):
        """空单元格在这一列里表示"算过、没触发"，不是"缺数据"——写进快照
        必须是 False，不能变成 NaN（NaN 会被上面 test_missing_dimension_
        columns_do_not_crash 那种"这一列压根不存在"的情况混淆）。"""
        logger.log_snapshot(self._df())
        out = pd.read_csv(logger._SNAPSHOT_PATH)
        self.assertFalse(pd.isna(out.iloc[0]["circuit_triggered"]))
        self.assertEqual(bool(out.iloc[0]["circuit_triggered"]), False)

    def test_missing_dimension_columns_do_not_crash(self):
        """老版本 results_validated.csv（没有维度列）应该照常写入，只是没有那几列。"""
        minimal = pd.DataFrame([{
            "ticker": "NVDA",
            "final_综合得分(0-100)": 82.5,
        }])
        n = logger.log_snapshot(minimal)
        self.assertEqual(n, 1)
        out = pd.read_csv(logger._SNAPSHOT_PATH)
        self.assertEqual(out.iloc[0]["final_score"], 82.5)
        self.assertNotIn("valuation", out.columns)

    def test_old_snapshot_rows_get_nan_not_backfilled(self):
        """一份已经存在、只有旧列（没有维度分）的历史快照文件，写入今天
        带维度分的新一批之后——老日期的维度列必须是 NaN（缺失数据的诚实
        表达），不能被后来的写入悄悄填上。
        """
        old_row = pd.DataFrame([{
            "date": "2026-01-01", "ticker": "NVDA",
            "final_score": 70.0, "rating": "✅ 综合良好", "price": 100.0,
        }])
        old_row.to_csv(logger._SNAPSHOT_PATH, index=False, encoding="utf-8-sig")

        logger.log_snapshot(self._df())

        out = pd.read_csv(logger._SNAPSHOT_PATH)
        self.assertEqual(len(out), 2)
        old = out[out["final_score"] == 70.0].iloc[0]
        self.assertTrue(pd.isna(old["valuation"]))
        new = out[out["final_score"] == 82.5].iloc[0]
        self.assertEqual(new["valuation"], 70.0)


if __name__ == "__main__":
    unittest.main()
