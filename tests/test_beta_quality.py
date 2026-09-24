"""Measured betas, and the warning that stops a weak one passing for a strong one.

Two of the largest holdings (SPCX, ETHU) were absent from the beta table, so
`account.risk.compute_portfolio_stress_test` silently applied its `1.0` default
to both -- understating Beta-Delta in the direction that makes a leveraged book
look calm. Vendor data could not fix it: Firstrade reported 25.14 for SPCX,
above the 7.30 ceiling the volatility ratio allows even at perfect correlation,
and Alpha Vantage returned None for both names.
"""

import pytest

from account.beta_quality import (
    DERIVED_BETAS,
    LOW_CONFIDENCE_R2,
    beta_overrides,
    low_confidence_held,
)


def test_every_symbol_that_was_silently_defaulting_now_has_a_measured_beta():
    # 2026-09-21：KLAC/ONTO/PATH 也曾整段落在 1.0 的静默缺省上（$7,324 现货）。
    assert {"SPCX", "ETHU", "KLAC", "ONTO", "PATH"} <= set(beta_overrides())


def test_the_leveraged_names_are_far_from_the_1_0_default_they_replaced():
    # 这几个标的是当初做这件事的理由：缺省值不是无害的近似。
    for symbol in ("SPCX", "ETHU", "KLAC", "ONTO"):
        assert beta_overrides()[symbol] > 1.4


def test_a_measured_one_point_zero_is_allowed_and_is_not_the_same_as_defaulting():
    """PATH 测出来就是 0.99，这不是失败——是结论。

    它跟"没人测过所以取 1.0"必须能分开：后者在 DERIVED_BETAS 里查不到，
    前者查得到，而且带着 R²=0.08 这个"别太当真"的证据。把 beta≈1.0 当成
    测量失败排除掉，就等于规定只有刺激的结果才允许被记录。
    """
    entry = DERIVED_BETAS["PATH"]
    assert entry.beta == pytest.approx(1.0, abs=0.15)
    assert entry.is_low_confidence                      # R² 0.08，标着弱拟合
    assert "PATH" in beta_overrides()                   # 但确实进了表


@pytest.mark.parametrize("symbol", sorted(DERIVED_BETAS))
def test_every_entry_carries_the_evidence_behind_it(symbol):
    entry = DERIVED_BETAS[symbol]
    assert entry.observations >= 30                 # enough to regress at all
    assert 0.0 <= entry.r_squared <= 1.0
    assert entry.std_error > 0
    assert entry.sample_start < entry.sample_end
    assert entry.note


@pytest.mark.parametrize("symbol", sorted(DERIVED_BETAS))
def test_beta_stays_under_the_volatility_ratio_ceiling(symbol):
    """Beta cannot exceed vol_ratio, which is what makes 25.14 impossible.

    SPCX's ratio was 7.30 and ETHU's 8.70; a regression beta above that would
    mean a correlation over 1.
    """
    assert DERIVED_BETAS[symbol].beta < 7.0


def test_both_names_are_flagged_low_confidence():
    # Beta above 2.5 with R² under 0.2: they move a lot, but mostly not with
    # the market. Sizing a hedge off beta alone will disappoint.
    for entry in DERIVED_BETAS.values():
        assert entry.is_low_confidence
        assert entry.r_squared < LOW_CONFIDENCE_R2


def test_only_symbols_actually_held_are_reported():
    assert [e.symbol for e in low_confidence_held(["ETHU"])] == ["ETHU"]
    assert low_confidence_held(["NVDA", "AVGO"]) == []
    assert low_confidence_held([]) == []


def test_holdings_are_matched_case_insensitively_and_untrimmed():
    assert [e.symbol for e in low_confidence_held([" ethu "])] == ["ETHU"]


def test_worst_fit_is_reported_first():
    # Asserted as a property rather than a fixed symbol order: the betas are
    # re-measured weekly, so which name has the poorest fit changes over time.
    fits = [e.r_squared for e in low_confidence_held(["SPCX", "ETHU"])]
    assert fits == sorted(fits)


def test_describe_names_the_beta_and_its_fit():
    described = DERIVED_BETAS["SPCX"].describe()
    assert "2.57" in described and "0.16" in described


# ── 口径（全样本 vs 下跌日）────────────────────────────────────────────
# 压力测试算的是 beta × 负的冲击，要的是下跌日系数。但下跌日只用得上一半
# 样本，不是每个标的都测得出够精确的值——所以表里会同时存在两种口径。
# 真正危险的不是不统一，是看不出某个标的用的是哪一种。

def _write_cache(tmp_path, monkeypatch, fits):
    import json
    from account import beta_quality
    p = tmp_path / "beta_cache.json"
    p.write_text(json.dumps({"updated_at": "2026-09-21T00:00:00",
                             "betas": {k: v["beta"] for k, v in fits.items()},
                             "fits": fits}), encoding="utf-8")
    monkeypatch.setattr(beta_quality, "_cache_path", lambda: p)


def test_seeds_are_all_explicitly_full_sample():
    # 种子全是全样本回归。默认值必须是 full，让"升级成下跌日"是显式动作。
    assert {e.kind for e in DERIVED_BETAS.values()} == {"full"}


def test_the_kind_survives_the_cache_round_trip(tmp_path, monkeypatch):
    from account.beta_quality import load_measured
    _write_cache(tmp_path, monkeypatch, {
        "AAA": {"beta": 2.1, "kind": "downside", "r_squared": 0.4,
                "std_error": 0.5, "observations": 200,
                "sample_start": "2024-09-01", "sample_end": "2026-09-18"},
    })
    entry = load_measured()["AAA"]
    assert entry.kind == "downside"
    assert "下跌日口径" in entry.note


def test_a_symbol_that_fell_back_says_why_it_fell_back(tmp_path, monkeypatch):
    # 看板上只看到 full 的话，分不清"没算下跌日"和"算了但不够精确"。
    from account.beta_quality import load_measured
    _write_cache(tmp_path, monkeypatch, {
        "BBB": {"beta": 1.6, "kind": "full", "r_squared": 0.3,
                "std_error": 0.3, "observations": 400,
                "sample_start": "2024-09-01", "sample_end": "2026-09-18",
                "downside_beta": 2.08, "downside_t": 1.5},
    })
    note = load_measured()["BBB"].note
    assert "2.08" in note and "1.5" in note and "全样本" in note


def test_a_cache_without_the_field_reads_as_full_not_as_unknown(tmp_path, monkeypatch):
    from account.beta_quality import load_measured
    _write_cache(tmp_path, monkeypatch, {
        "CCC": {"beta": 1.2, "r_squared": 0.3, "std_error": 0.3,
                "observations": 400, "sample_start": "x", "sample_end": "y"},
    })
    assert load_measured()["CCC"].kind == "full"


def test_a_uniform_table_reports_no_mixing(tmp_path, monkeypatch):
    from account.beta_quality import mixed_kind_note
    _write_cache(tmp_path, monkeypatch, {
        s: {"beta": 1.5, "kind": "downside", "r_squared": 0.3, "std_error": 0.3,
            "observations": 400, "sample_start": "x", "sample_end": "y"}
        for s in ("AAA", "BBB")
    })
    assert mixed_kind_note(["AAA", "BBB"]) is None


def test_a_mixed_table_names_both_groups(tmp_path, monkeypatch):
    from account.beta_quality import mixed_kind_note
    _write_cache(tmp_path, monkeypatch, {
        "AAA": {"beta": 2.1, "kind": "downside", "r_squared": .3, "std_error": .3,
                "observations": 400, "sample_start": "x", "sample_end": "y"},
        "BBB": {"beta": 1.6, "kind": "full", "r_squared": .3, "std_error": .3,
                "observations": 400, "sample_start": "x", "sample_end": "y"},
    })
    note = mixed_kind_note(["AAA", "BBB"])
    assert note and "AAA" in note and "BBB" in note


def test_only_held_symbols_are_considered(tmp_path, monkeypatch):
    # 不持有的标的口径不一致，不该报成"你的表混了口径"。
    from account.beta_quality import mixed_kind_note
    _write_cache(tmp_path, monkeypatch, {
        "AAA": {"beta": 2.1, "kind": "downside", "r_squared": .3, "std_error": .3,
                "observations": 400, "sample_start": "x", "sample_end": "y"},
        "ZZZ": {"beta": 1.6, "kind": "full", "r_squared": .3, "std_error": .3,
                "observations": 400, "sample_start": "x", "sample_end": "y"},
    })
    assert mixed_kind_note(["AAA"]) is None
    assert mixed_kind_note(["AAA", "ZZZ"]) is not None


def test_t_stat_is_available_for_reading_precision():
    entry = DERIVED_BETAS["ONTO"]
    assert entry.t_stat == pytest.approx(1.57 / 0.410, abs=0.01)
