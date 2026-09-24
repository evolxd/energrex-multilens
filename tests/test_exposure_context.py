"""Tests for scoring/exposure_context.py -- 门③/门④共用的敞口读数层。

重点是 exposures_after_trade()：门④"加上这笔之后会不会破线"的核心，
必须跟门③ compute_exposures 给出一致的口径（期权按标的净额、现金同步
扣减），否则两个页面会对同一笔交易给出互相矛盾的答案。
"""
import unittest

from scoring.exposure_context import (
    exposures_after_trade,
    is_fund_like,
    non_scored_chain,
    non_scored_companies,
    stock_prices_and_values,
    underlyings_in,
)
from scoring.position_exposure import compute_exposures


def _chain_of(symbol: str):
    return {"NVDA": "AI算力", "AVGO": "AI算力", "XOM": "能源"}.get(symbol)


class ExposuresAfterTradeTests(unittest.TestCase):

    def setUp(self):
        self.positions = [{"symbol": "XOM", "market_value": 10_000.0}]
        self.options = [{"symbol": "NVDA260116C00100000", "market_value": 20_000.0}]
        self.equity = 100_000.0
        self.cash = 30_000.0

    def test_new_trade_adds_to_that_underlying(self):
        after = exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="NVDA", capital_committed=5_000.0,
        )
        # 20k 已有 + 5k 新仓 = 25k / 100k
        self.assertAlmostEqual(after.by_ticker_pct["NVDA"], 25.0)

    def test_new_trade_on_fresh_underlying_creates_its_own_bucket(self):
        after = exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="AVGO", capital_committed=8_000.0,
        )
        self.assertAlmostEqual(after.by_ticker_pct["AVGO"], 8.0)
        # 同一条产业链上累加：NVDA 20% + AVGO 8%
        self.assertAlmostEqual(after.by_chain_pct["AI算力"], 28.0)

    def test_committed_capital_comes_out_of_cash(self):
        after = exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="NVDA", capital_committed=10_000.0,
        )
        # 现金 30k - 10k = 20k / 100k
        self.assertAlmostEqual(after.cash_pct, 20.0)

    def test_negative_capital_is_treated_as_committed_not_refunded(self):
        """credit 价差填成负数也按"占住这么多"算，不能变成现金增加。"""
        after = exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="NVDA", capital_committed=-5_000.0,
        )
        self.assertAlmostEqual(after.cash_pct, 25.0)
        self.assertAlmostEqual(after.by_ticker_pct["NVDA"], 25.0)

    def test_does_not_mutate_caller_positions(self):
        exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="NVDA", capital_committed=5_000.0,
        )
        self.assertEqual(len(self.options), 1)

    def test_matches_compute_exposures_when_nothing_added(self):
        """加 0 元的一笔，除了多出一个空桶，读数应该跟门③完全一致。"""
        before = compute_exposures(
            self.positions, self.equity, self.cash, _chain_of, self.options)
        after = exposures_after_trade(
            self.positions, self.options, self.equity, self.cash, _chain_of,
            symbol="NVDA", capital_committed=0.0,
        )
        self.assertAlmostEqual(before.max_single_stock_pct, after.max_single_stock_pct)
        self.assertAlmostEqual(before.cash_pct, after.cash_pct)

    def test_returns_none_without_equity(self):
        self.assertIsNone(exposures_after_trade(
            self.positions, self.options, None, self.cash, _chain_of,
            symbol="NVDA", capital_committed=5_000.0,
        ))


class UnderlyingsInTests(unittest.TestCase):

    def test_parses_option_symbols_and_dedupes(self):
        got = underlyings_in(
            [{"symbol": "xom"}, {"symbol": "NVDA"}],
            [{"symbol": "NVDA260116C00100000"}],
        )
        self.assertEqual(got, ("NVDA", "XOM"))

    def test_tolerates_empty_inputs(self):
        self.assertEqual(underlyings_in([], None), ())


class StockPricesAndValuesTests(unittest.TestCase):
    """把「减仓 $7,000」换成「卖 39 股」要的每股价格。"""

    def test_price_comes_from_the_same_snapshot_row_as_the_exposure(self):
        prices, values = stock_prices_and_values(
            [{"symbol": "NVDA", "quantity": 150, "market_value": 28_500.0}]
        )
        self.assertAlmostEqual(prices["NVDA"], 190.0)
        self.assertAlmostEqual(values["NVDA"], 28_500.0)

    def test_a_closed_position_has_no_price_rather_than_a_price_of_zero(self):
        # 股数 0 的行是已平仓的留痕；给它一个价格会让下游算出无穷多股。
        prices, values = stock_prices_and_values(
            [{"symbol": "GONE", "quantity": 0, "market_value": 0.0}]
        )
        self.assertNotIn("GONE", prices)
        self.assertEqual(values["GONE"], 0.0)

    def test_a_short_position_reports_a_positive_price(self):
        prices, _ = stock_prices_and_values(
            [{"symbol": "SHORT", "quantity": -100, "market_value": -5_000.0}]
        )
        self.assertAlmostEqual(prices["SHORT"], 50.0)

    def test_unparseable_rows_are_skipped_not_fatal(self):
        prices, values = stock_prices_and_values(
            [{"symbol": "BAD", "quantity": "x", "market_value": "y"},
             {"symbol": "", "quantity": 1, "market_value": 1.0},
             {"symbol": "OK", "quantity": 10, "market_value": 1_000.0}]
        )
        self.assertEqual(set(prices), {"OK"})
        self.assertEqual(set(values), {"OK"})


class NonScoredChainTests(unittest.TestCase):
    """门①结构分析靠它跳过 ETF——对 QQQ 问「管理层诚信」是没有意义的。"""

    def test_hedge_and_leveraged_products_are_named(self):
        self.assertEqual(non_scored_chain("QQQ"), "对冲(指数)")
        self.assertEqual(non_scored_chain("SMH"), "对冲(半导体)")
        self.assertEqual(non_scored_chain("ETHU"), "加密资产")

    def test_an_ordinary_operating_company_is_not_one(self):
        self.assertIsNone(non_scored_chain("NVDA"))

    def test_case_and_whitespace_do_not_matter(self):
        self.assertEqual(non_scored_chain(" qqq "), "对冲(指数)")

    def test_empty_input_is_not_an_error(self):
        self.assertIsNone(non_scored_chain(""))
        self.assertIsNone(non_scored_chain(None))




class IsFundLikeTests(unittest.TestCase):
    """「背后有没有一家公司」跟「进不进评分流程」是两件事。

    2026-09-21 之前门①的结构解读页拿 non_scored_chain() 当"是不是 ETF"用，
    于是 VST 被当成 ETF 跳过并显示成「ETF/杠杆产品」——而 Vistra 是一家真实
    的发电公司，有 10-K、有分部披露，结构分析对它完全适用。
    """

    def test_etfs_and_leveraged_products_have_no_company_behind_them(self):
        for symbol in ("QQQ", "SMH", "ETHU"):
            self.assertTrue(is_fund_like(symbol), symbol)

    def test_vst_is_a_real_company_even_though_it_is_not_scored(self):
        # 这一条就是那个 bug。两个函数必须给出相反的答案。
        self.assertIsNotNone(non_scored_chain("VST"))   # 确实不进评分
        self.assertFalse(is_fund_like("VST"))           # 但它是家公司

    def test_an_ordinary_scored_holding_is_neither(self):
        self.assertIsNone(non_scored_chain("NVDA"))
        self.assertFalse(is_fund_like("NVDA"))

    def test_case_and_whitespace_do_not_matter(self):
        self.assertTrue(is_fund_like(" qqq "))

    def test_empty_input_is_not_an_error(self):
        self.assertFalse(is_fund_like(""))
        self.assertFalse(is_fund_like(None))




class NonScoredCompaniesTests(unittest.TestCase):
    """门①结构解读页的标的全集要把它们并进来，否则 VST 连出现的机会都没有。"""

    def test_vst_is_listed_as_a_company(self):
        self.assertIn("VST", non_scored_companies())

    def test_etfs_are_not(self):
        self.assertFalse(set(non_scored_companies()) & {"QQQ", "SMH", "ETHU"})

    def test_every_entry_is_a_real_company_not_a_fund(self):
        self.assertFalse([s for s in non_scored_companies() if is_fund_like(s)])


if __name__ == "__main__":
    unittest.main()
