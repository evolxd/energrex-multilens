"""估值分数底下那家公司，是靠几条腿站着的。

六维评分把公司压成一个点：`revenue_growth_yoy = 0.85` 对一家四条腿各涨
21% 的公司和一家全靠一个分部的公司是同一个数。这里钉住的是那个点展开
之后的形状——以及几个容易算反的地方（HHI 比最大占比更对、增量为负时
百分比不能报、分部之和不等于 Total 时不许摊回去）。
"""

import pytest

from scoring.company_structure import (
    assess,
    growth_attribution,
    max_share_swing,
    segment_mix,
    structural_drift,
)


# ── 分部构成 ────────────────────────────────────────────────────────────

def test_total_key_is_the_denominator_not_a_segment():
    mix = segment_mix({"2026-04-30": {"Total": 100.0, "DataCenter": 90.0, "Gaming": 10.0}})
    assert mix.n_segments == 2
    assert mix.total == 100.0
    assert mix.shares["DataCenter"] == pytest.approx(0.90)


def test_without_a_reported_total_the_segments_sum_to_it():
    mix = segment_mix({"2026-04-30": {"A": 60.0, "B": 40.0}})
    assert mix.total == 100.0
    assert mix.residual == 0.0


def test_the_gap_between_segments_and_total_is_reported_not_spread_around():
    # 摊回各分部等于替公司做了一个它自己没做的假设。
    mix = segment_mix({"2026-04-30": {"Total": 100.0, "A": 60.0, "B": 30.0}})
    assert mix.residual == pytest.approx(10.0)
    assert mix.residual_share == pytest.approx(0.10)
    assert mix.shares["A"] == pytest.approx(0.60)      # 不是 60/90


def test_rounding_dust_is_not_counted_as_a_leg():
    mix = segment_mix({"2026-04-30": {"Total": 1000.0, "A": 997.0, "Dust": 3.0}})
    assert "Dust" not in mix.segments


def test_no_data_is_none_not_an_empty_mix():
    assert segment_mix({}) is None
    assert segment_mix({"2026-04-30": {"Total": 0.0}}) is None


# ── HHI 比"最大占比"更对 ────────────────────────────────────────────────

def test_hhi_separates_two_books_that_max_share_calls_the_same():
    three_legs = segment_mix({"p": {"A": 34.0, "B": 33.0, "C": 33.0}})
    one_big = segment_mix({"p": {"A": 60.0, "B": 20.0, "C": 20.0}})
    # 最大占比说 34% 比 60% 安全，方向没错；但 HHI 给出的是"有效腿数"，
    # 这才是要拿去判断脆弱度的量。
    assert three_legs.hhi < one_big.hhi
    assert three_legs.effective_segments == pytest.approx(3.0, abs=0.05)
    assert one_big.effective_segments == pytest.approx(2.27, abs=0.05)


def test_four_segments_with_one_dominant_is_effectively_one_leg():
    mix = segment_mix({"p": {"A": 90.0, "B": 4.0, "C": 3.0, "D": 3.0}})
    assert mix.n_segments == 4
    assert mix.effective_segments < 1.3        # 数出来 4 条腿，实际 1 条


# ── 增量归因 ────────────────────────────────────────────────────────────

def _four_quarters(q1, q2, q3, q4):
    return {"2025-07-31": q1, "2025-10-31": q2, "2026-01-31": q3, "2026-04-30": q4}


def test_attribution_compares_year_over_year_not_sequentially():
    revs = _four_quarters(
        {"A": 100.0}, {"A": 110.0}, {"A": 120.0}, {"A": 130.0},
    )
    revs["2026-07-31"] = {"A": 200.0}
    attr = growth_attribution(revs, periods_back=4)
    assert attr.prior_period == "2025-07-31"     # 四期前，不是上一期
    assert attr.total_growth == pytest.approx(1.0)


def test_not_enough_history_returns_none_rather_than_a_shorter_comparison():
    # 季节性强的生意里，拿三期前凑合算出来的"增长"混着季节因素。
    revs = {"2026-01-31": {"A": 100.0}, "2026-04-30": {"A": 130.0}}
    assert growth_attribution(revs, periods_back=4) is None


def test_the_growth_story_is_traced_to_the_segment_that_produced_it():
    revs = _four_quarters(
        {"DataCenter": 100.0, "Gaming": 50.0},
        {"DataCenter": 120.0, "Gaming": 50.0},
        {"DataCenter": 150.0, "Gaming": 50.0},
        {"DataCenter": 190.0, "Gaming": 55.0},
    )
    revs["2026-07-31"] = {"DataCenter": 250.0, "Gaming": 55.0}
    attr = growth_attribution(revs, periods_back=4)

    top = attr.top_contributor
    assert top.segment == "DataCenter"
    # 整体增量 155，其中 150 来自 DataCenter
    assert attr.share_of_growth(top) == pytest.approx(150 / 155, abs=0.01)


def test_flatlining_the_top_contributor_is_arithmetic_not_a_forecast():
    revs = {
        "2025-07-31": {"DataCenter": 100.0, "Gaming": 50.0},
        "2025-10-31": {"DataCenter": 120.0, "Gaming": 50.0},
        "2026-01-31": {"DataCenter": 150.0, "Gaming": 50.0},
        "2026-04-30": {"DataCenter": 190.0, "Gaming": 52.0},
        "2026-07-31": {"DataCenter": 250.0, "Gaming": 55.0},
    }
    attr = growth_attribution(revs, periods_back=4)
    # 整体 150 → 305，+103%。DataCenter 增速归零则 150 → 155，+3.3%。
    assert attr.total_growth == pytest.approx(155 / 150, abs=0.01)
    assert attr.growth_without_top == pytest.approx(5 / 150, abs=0.01)


def test_a_segment_growing_from_zero_has_no_growth_rate_rather_than_infinity():
    revs = {
        "2025-07-31": {"A": 100.0},
        "2025-10-31": {"A": 100.0},
        "2026-01-31": {"A": 100.0},
        "2026-04-30": {"A": 100.0},
        "2026-07-31": {"A": 100.0, "NewThing": 40.0},
    }
    attr = growth_attribution(revs, periods_back=4)
    new = next(c for c in attr.contributions if c.segment == "NewThing")
    assert new.own_growth is None
    assert new.delta == pytest.approx(40.0)


def test_a_shrinking_business_gets_no_percentage_of_growth():
    # 分母为负时这个比例会反号，报出来的是噪声。
    revs = {
        "2025-07-31": {"A": 100.0, "B": 100.0},
        "2025-10-31": {"A": 100.0, "B": 100.0},
        "2026-01-31": {"A": 100.0, "B": 100.0},
        "2026-04-30": {"A": 100.0, "B": 100.0},
        "2026-07-31": {"A": 110.0, "B": 50.0},
    }
    attr = growth_attribution(revs, periods_back=4)
    assert attr.total_delta < 0
    assert all(attr.share_of_growth(c) is None for c in attr.contributions)


# ── 结构漂移 ────────────────────────────────────────────────────────────

def test_drift_is_returned_oldest_first():
    revs = {"2026-04-30": {"A": 1.0}, "2025-04-30": {"A": 1.0}, "2026-01-31": {"A": 1.0}}
    periods = [m.period for m in structural_drift(revs)]
    assert periods == sorted(periods)


def test_the_swing_that_matters_is_first_to_last_not_the_wiggle_in_between():
    # 中间来回震荡但回到原点的，用历史比率外推仍然成立。
    revs = {
        "2025-01-31": {"A": 50.0, "B": 50.0},
        "2025-04-30": {"A": 80.0, "B": 20.0},
        "2025-07-31": {"A": 50.0, "B": 50.0},
    }
    name, change = max_share_swing(structural_drift(revs))
    assert change == pytest.approx(0.0, abs=0.001)


def test_a_real_mix_shift_is_surfaced_with_its_direction():
    revs = {
        "2025-01-31": {"A": 50.0, "B": 50.0},
        "2025-04-30": {"A": 70.0, "B": 30.0},
        "2025-07-31": {"A": 80.0, "B": 20.0},
    }
    name, change = max_share_swing(structural_drift(revs))
    assert {name} <= {"A", "B"}
    assert abs(change) == pytest.approx(0.30, abs=0.01)


def test_one_period_cannot_show_drift():
    assert max_share_swing(structural_drift({"2026-04-30": {"A": 1.0}})) is None


# ── 合成结论：flags 与 notes 必须分开 ───────────────────────────────────

def test_no_segment_data_reads_as_unknown_not_as_healthy():
    # 把"数据不够所以看不出问题"混进"没发现问题"，是这类分析最贵的错。
    result = assess("XYZ", {})
    assert not result.is_analyzable
    assert result.flags == []
    assert result.notes and "看不出结构" in result.notes[0]


def test_a_single_segment_filer_is_a_disclosure_limit_not_a_finding():
    result = assess("SNOW", {"2026-04-30": {"Total": 100.0, "Everything": 100.0}})
    assert not result.is_analyzable
    assert result.flags == []
    assert any("披露颗粒度" in n for n in result.notes)


def test_nominal_diversification_hiding_a_single_leg_is_flagged():
    result = assess("XYZ", {"2026-04-30": {"A": 90.0, "B": 4.0, "C": 3.0, "D": 3.0}})
    assert any("等效分部数" in f for f in result.flags)


def test_growth_resting_on_one_segment_is_flagged_with_the_counterfactual():
    revs = {
        "2025-07-31": {"DataCenter": 100.0, "Gaming": 50.0},
        "2025-10-31": {"DataCenter": 120.0, "Gaming": 50.0},
        "2026-01-31": {"DataCenter": 150.0, "Gaming": 50.0},
        "2026-04-30": {"DataCenter": 190.0, "Gaming": 52.0},
        "2026-07-31": {"DataCenter": 250.0, "Gaming": 52.0},
    }
    result = assess("NVDA", revs)
    hit = [f for f in result.flags if "同比增量" in f]
    assert hit and "DataCenter" in hit[0]


def test_a_large_unallocated_residual_is_a_note_on_the_reading():
    result = assess("XYZ", {"2026-04-30": {"Total": 100.0, "A": 55.0, "B": 25.0}})
    assert any("未分配" in n for n in result.notes)


def test_a_stable_two_leg_business_produces_no_flags():
    revs = {
        "2025-07-31": {"A": 50.0, "B": 50.0},
        "2025-10-31": {"A": 52.0, "B": 52.0},
        "2026-01-31": {"A": 54.0, "B": 54.0},
        "2026-04-30": {"A": 56.0, "B": 56.0},
        "2026-07-31": {"A": 58.0, "B": 58.0},
    }
    result = assess("XYZ", revs)
    assert result.is_analyzable
    assert result.flags == []


# ── SEC 覆盖 ────────────────────────────────────────────────────────────

def test_every_cik_is_ten_digits():
    """CIK 错一位不会报错，只会安静地拉来另一家公司的财报。

    SEC 的 companyfacts 接口要求零填充到 10 位；长度不对会 404，而调用方
    只会看到"取不到数据"，查不到是编号写错了。
    """
    from scoring.edgar_fetcher import TICKER_CIK
    for ticker, cik in TICKER_CIK.items():
        assert cik.isdigit(), f"{ticker}: {cik!r} 不是纯数字"
        assert len(cik) == 10, f"{ticker}: {cik!r} 不是 10 位"


def test_no_two_tickers_share_a_cik():
    from scoring.edgar_fetcher import TICKER_CIK
    seen = {}
    for ticker, cik in TICKER_CIK.items():
        assert cik not in seen, f"{ticker} 跟 {seen.get(cik)} 撞了同一个 CIK {cik}"
        seen[cik] = ticker


def test_etfs_are_not_given_a_cik():
    """ETF 背后没有公司，配 CIK 只会让人以为拉得到分部收入。"""
    from scoring.edgar_fetcher import TICKER_CIK
    from scoring.exposure_context import is_fund_like
    assert not [t for t in TICKER_CIK if is_fund_like(t)]


def test_the_held_names_that_were_missing_are_now_covered():
    # 2026-09-21 补的那一批，账户实际持有的非 ETF 标的现在应当全部有 CIK。
    from scoring.edgar_fetcher import TICKER_CIK
    assert {"AMD", "ARM", "DDOG", "FCX", "KLAC",
            "META", "PATH", "VST", "SPCX"} <= set(TICKER_CIK)
