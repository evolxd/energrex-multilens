"""该用 QQQ 还是 SMH 对冲，以及各买多少。

两个指数都按全额买一遍，是把同一笔敞口对冲两次——这个模块存在的唯一理由
就是先把要对冲的量按产业链切开，再分给各自最贴合的工具。
"""

import pytest

from account.hedge_split import split_hedge_need

CHAINS = {
    "NVDA": "AI芯片",
    "AMD": "AI芯片",
    "MU": "AI芯片",
    "ONTO": "半导体设备",
    "SMH": "对冲(半导体)",
    "PLTR": "AI软件/SaaS",
    "MSFT": "大型科技",
    "QQQ": "对冲(指数)",
}


def _split(bd_by_und, *, equity=100_000.0, target=1.50, total=None):
    return split_hedge_need(
        bd_by_und,
        equity=equity,
        target_bd_ratio=target,
        chain_of=CHAINS.get,
        total_bd=total,
    )


def test_the_two_slices_add_back_up_to_the_whole_job():
    # 这就是"不要对冲两遍"的意思：切开之后两段相加等于原来的总量。
    split = _split({"NVDA": 120_000, "PLTR": 80_000})
    assert split.semi_bd_to_hedge + split.broad_bd_to_hedge == pytest.approx(
        split.bd_to_hedge
    )


def test_the_semiconductor_share_follows_beta_delta_not_head_count():
    # 一只半导体 120k、一只软件 80k：60/40，不是 50/50。
    split = _split({"NVDA": 120_000, "PLTR": 80_000})
    assert split.semi_share == pytest.approx(0.60)
    assert split.semi_bd == pytest.approx(120_000)
    assert split.broad_bd == pytest.approx(80_000)


def test_semiconductor_equipment_counts_as_semiconductor():
    split = _split({"ONTO": 50_000, "PLTR": 50_000})
    assert "ONTO" in split.semi_symbols
    assert split.semi_share == pytest.approx(0.5)


def test_an_unknown_ticker_falls_to_the_broad_side_rather_than_being_dropped():
    # 落到 QQQ 一侧是保守的那一边：宽基对冲什么都能沾一点，把不认识的标的
    # 当成半导体则会高估 SMH 该买的量。
    split = _split({"WEIRD": 100_000})
    assert split.broad_symbols == ["WEIRD"]
    assert split.semi_share == 0.0


def test_existing_index_puts_land_on_the_side_they_actually_protect():
    # SMH put 的负 Beta-Delta 抵掉的是半导体那一段，不是整个账户。
    split = _split({"NVDA": 120_000, "SMH": -40_000, "PLTR": 80_000, "QQQ": -20_000})
    assert split.semi_bd == pytest.approx(80_000)
    assert split.broad_bd == pytest.approx(60_000)


def test_already_at_target_asks_for_nothing():
    split = _split({"NVDA": 100_000, "PLTR": 50_000}, target=1.50)
    assert not split.needs_hedge
    assert split.semi_bd_to_hedge == 0.0
    assert split.broad_bd_to_hedge == 0.0


def test_an_over_hedged_semiconductor_sleeve_gets_no_more_smh():
    # 半导体已经被保护腿压成负的：再买 SMH put 是在建反向头寸，不是对冲。
    split = _split({"NVDA": 40_000, "SMH": -60_000, "MSFT": 200_000})
    assert split.semi_bd < 0
    assert split.semi_share == 0.0
    assert split.semi_bd_to_hedge == 0.0
    assert split.broad_bd_to_hedge == pytest.approx(split.bd_to_hedge)


def test_the_snapshot_total_wins_over_the_sum_of_the_parts():
    # 风险快照里的合计是四舍五入过的；两边各算各的会让"对冲后的 BD"跟快照
    # 上显示的数字对不上几百块，看起来像算错了。
    split = _split({"NVDA": 120_000, "PLTR": 80_000}, total=200_001.0)
    assert split.total_bd == 200_001.0
    assert split.bd_to_hedge == pytest.approx(50_001.0)


def test_an_empty_book_does_not_raise():
    split = _split({})
    assert split.semi_share == 0.0
    assert not split.needs_hedge


def test_sleeve_percentages_are_reported_against_equity():
    split = _split({"NVDA": 120_000, "PLTR": 80_000}, equity=100_000.0)
    assert split.semi_pct_of_equity == pytest.approx(120.0)
    assert split.broad_pct_of_equity == pytest.approx(80.0)
