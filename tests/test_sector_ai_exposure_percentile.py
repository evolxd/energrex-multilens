"""Tests for refresh_scores.add_sector_ai_exposure_percentile.

This is a second, independent reading of AI exposure -- deliberately not
merged into final_score. It answers "who's strongest within this sector",
not "who's strongest overall"; see refresh_scores.py's module comment for
why the two are kept apart.
"""
import pandas as pd

from refresh_scores import add_sector_ai_exposure_percentile

SECTOR_COL = "sector_板块"
AI_COL = "ai_AI暴露得分(AI营收/平台/订单占比)"
PCT_COL = "ai_行业内百分位(同sector_tag内排名)"
NOTE_COL = "ai_行业内百分位_样本量提示"


def make_df(rows):
    return pd.DataFrame(rows, columns=["ticker", SECTOR_COL, AI_COL])


def test_ranks_within_sector_not_across_the_whole_universe():
    """A mediocre Hardware score can outrank a strong SaaS score within its
    own group -- the whole point is not comparing across sectors."""
    df = make_df([
        ("A", "Hardware", 60.0),
        ("B", "Hardware", 40.0),
        ("C", "SaaS", 90.0),
        ("D", "SaaS", 30.0),
    ])
    out = add_sector_ai_exposure_percentile(df)
    a = out.loc[out["ticker"] == "A", PCT_COL].iloc[0]
    b = out.loc[out["ticker"] == "B", PCT_COL].iloc[0]
    assert a == 100.0  # top of its own (2-member) Hardware group
    assert b == 50.0   # bottom of that group


def test_final_score_columns_are_untouched():
    """This must never influence final_score -- it's a separate reading,
    not a blended one."""
    df = make_df([("A", "Hardware", 60.0), ("B", "Hardware", 40.0)])
    df["final_综合得分(0-100)"] = [70.0, 65.0]
    out = add_sector_ai_exposure_percentile(df.copy())
    assert list(out["final_综合得分(0-100)"]) == [70.0, 65.0]


def test_company_category_labels_are_normalized_into_sector_tag_groups():
    """CompanyCategory's Chinese labels ('AI软件/SaaS', '网络安全', ...)
    occasionally leak into the sector_tag column -- see the alias table's
    docstring. A leaked label must join its real group, not become its own
    spurious n=1 group."""
    df = make_df([
        ("A", "SaaS", 80.0),
        ("B", "AI软件/SaaS", 20.0),  # should join the SaaS group above
    ])
    out = add_sector_ai_exposure_percentile(df)
    # n=2 combined group: A is top (100th pct), B is bottom (50th pct) --
    # if the alias mapping failed, each would show n=1 and both 100.0.
    assert out.loc[out["ticker"] == "A", PCT_COL].iloc[0] == 100.0
    assert out.loc[out["ticker"] == "B", PCT_COL].iloc[0] == 50.0
    assert "n=1" not in out.loc[out["ticker"] == "B", NOTE_COL].iloc[0]


def test_small_group_gets_a_visible_caveat_not_false_confidence():
    df = make_df([("A", "Cybersecurity", 50.0), ("B", "Cybersecurity", 60.0)])
    out = add_sector_ai_exposure_percentile(df)
    assert "样本量小" in out.loc[out["ticker"] == "A", NOTE_COL].iloc[0]
    assert "n=2" in out.loc[out["ticker"] == "A", NOTE_COL].iloc[0]


def test_large_group_has_no_caveat():
    rows = [(f"T{i}", "Hardware", float(i)) for i in range(12)]
    out = add_sector_ai_exposure_percentile(make_df(rows))
    assert (out[NOTE_COL] == "").all()


def test_missing_columns_returns_dataframe_unchanged():
    df = pd.DataFrame({"ticker": ["A"]})
    out = add_sector_ai_exposure_percentile(df)
    assert PCT_COL not in out.columns
