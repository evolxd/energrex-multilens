"""期权张数的符号：卖方被读成买方，是 BD 和压力测试同时出错的那个根。

`options_positions` 里同一张表有两种写法——xlsx 导入存带符号的张数，Chrome
抓取存 `abs(qty)` + `direction='short'`。读的一端只有价差分析做了兼容，风险
快照、组合 Greeks、对冲宽度检查三条路都直接用 raw quantity，于是卖出的 put
被当成买入的 put：Beta-Delta 符号反掉，"大盘崩盘"在压力测试里变成赚钱。
"""

import datetime

import pytest

from account.options import (
    direction_carries_side,
    option_market_value,
    signed_quantity,
)
from account.risk import compute_portfolio_stress_test


def test_the_scraper_shape_abs_qty_plus_direction_is_restored_to_negative():
    # _parse_scraped_rows 写的就是这种行
    assert signed_quantity(2, "short") == -2.0


def test_the_xlsx_shape_already_signed_is_left_alone():
    # positions_xlsx.py 存的是带符号的张数，再翻一次就等于把 bug 换个方向
    assert signed_quantity(-2, "short") == -2.0


def test_long_stays_long_under_every_spelling():
    for direction in ("long", "LONG", "Long", "buy", None, "", "  "):
        assert signed_quantity(3, direction) == 3.0


def test_the_manual_editors_call_put_is_an_option_type_not_a_side():
    """手动录入表格的「方向」下拉框写的是 Call/Put，不是买卖。

    这一列被三套代码写过三种词汇。把 "Put" 当成卖出的话，所有手工录入的
    买入 put 都会被翻成卖出——比原来的 bug 还糟。
    """
    assert signed_quantity(2, "Call") == 2.0
    assert signed_quantity(2, "Put") == 2.0
    assert signed_quantity(-2, "Put") == -2.0
    assert direction_carries_side("Put") is False
    assert direction_carries_side("Call") is False
    assert direction_carries_side("") is False
    assert direction_carries_side(None) is False
    assert direction_carries_side("short") is True
    assert direction_carries_side("LONG") is True


def test_chinese_and_broker_spellings_of_short_are_recognised():
    for direction in ("卖出", "卖", "空", "SELL", "sold", "STO", "write"):
        assert signed_quantity(1, direction) == -1.0, direction


def test_missing_and_junk_quantities_are_zero_not_a_crash():
    for junk in (None, "", "abc", float("nan") and None):
        assert signed_quantity(junk, "short") == 0.0
    assert signed_quantity(0, "short") == 0.0


def test_string_quantities_from_sqlite_are_accepted():
    assert signed_quantity("2", "short") == -2.0
    assert signed_quantity("-2", "long") == -2.0


def test_a_short_put_read_raw_flips_the_sign_of_its_market_value():
    """这就是下游看到的后果，用最短的例子钉一下。

    两张卖出的 put、现价 $5：真实市值 -$1,000（欠着的负债）。按 raw
    quantity 读成 +$1,000，方向整个反了。
    """
    raw_qty, direction, price = 2, "short", 5.0
    assert option_market_value(raw_qty, price) == pytest.approx(1000.0)
    assert option_market_value(signed_quantity(raw_qty, direction), price) == pytest.approx(-1000.0)


def test_reading_a_sold_put_raw_turns_a_crash_into_a_profit():
    """这一条是那张截图的机制本身，用真正的压力测试函数跑出来。

    券商单子上是**卖出** 3 张 SMH 500 put。按 raw quantity 读（+3，被当成
    买入），压力测试说大盘跌 20% 这笔赚 $33,559；归一之后同一笔是亏
    $33,559。BD 的符号也跟着整个反过来。

    "净多头 + 崩盘赚钱"就是这么同时出现在一块面板上的——不是两个数其中一个
    算错，是喂进去的方向反了，两个数一起反。
    """
    row = {"symbol": "SMH270115P00500000", "quantity": 3, "direction": "short",
           "current_price": 12.0, "market_value": 3600.0,
           "strike": 500.0, "expiry": "2027-01-15"}

    def stress(qty):
        leg = dict(row, quantity=qty)
        return compute_portfolio_stress_test(
            [], [leg], underlying_prices={"SMH": 525.0},
            iv_map={"SMH": {"iv": 0.30}}, beta_map={"SMH": 1.5},
            today=datetime.date(2026, 9, 21))

    raw = stress(row["quantity"])
    fixed = stress(signed_quantity(row["quantity"], row["direction"]))

    assert raw["stress_20"] > 0      # 崩盘"赚钱"——这就是页面上看到的
    assert fixed["stress_20"] < 0    # 卖出的 put 在崩盘里亏钱
    assert fixed["stress_20"] == pytest.approx(-raw["stress_20"])
    assert fixed["beta_delta"] == pytest.approx(-raw["beta_delta"])
