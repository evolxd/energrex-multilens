"""CIK 错一位不会报错——只会安静地画出另一家公司的财报。

`fetch_xbrl_facts` 照样 200，`_quarterly_segment_revenues` 照样解析出分部
收入，页面照样出图。没有任何症状，只能主动核。这里钉的是核对的判定规则
（纯函数，不联网），联网那半截在 scripts/verify_cik.py 里。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_cik import verdict  # noqa: E402


def test_the_ticker_being_in_secs_own_list_is_what_passes():
    status, name, _ = verdict("NVDA", {"name": "NVIDIA CORP", "tickers": ["NVDA"]})
    assert status == "✓"
    assert name == "NVIDIA CORP"


def test_a_cik_belonging_to_another_company_is_caught():
    # 这就是要防的那种错：编号有效、请求成功、数据是别人的。
    status, _, note = verdict("KLAC", {"name": "ADOBE INC", "tickers": ["ADBE"]})
    assert status.startswith("✗")
    assert "ADBE" in note


def test_judgement_uses_the_ticker_list_not_the_company_name():
    """公司名会变，ticker 列表是 SEC 维护的结构化字段。

    ONTO 现在叫 Onto Innovation，以前叫 Rudolph Technologies；META 以前是
    Facebook。拿名字做模糊匹配的话，改过名的公司会被误报成不匹配。
    """
    status, _, _ = verdict("ONTO", {"name": "ONTO INNOVATION INC.", "tickers": ["ONTO"]})
    assert status == "✓"
    # 名字跟 ticker 毫无字面关系，照样通过
    status, _, _ = verdict("META", {"name": "Meta Platforms, Inc.", "tickers": ["META"]})
    assert status == "✓"


def test_a_company_with_several_listed_tickers_passes_on_any_of_them():
    status, _, _ = verdict("GOOG", {"name": "Alphabet Inc.", "tickers": ["GOOGL", "GOOG"]})
    assert status == "✓"


def test_case_does_not_matter():
    assert verdict("nvda", {"name": "NVIDIA CORP", "tickers": ["nvda"]})[0] == "✓"


def test_a_failed_fetch_is_not_reported_as_a_pass():
    # "没核到"和"核过了没问题"必须分开——这是这个项目反复踩过的同一个坑。
    status, _, note = verdict("SPCX", None)
    assert status == "取数失败"
    assert "不等于对" in note


def test_an_entity_without_a_ticker_list_is_referred_to_a_human():
    # 刚上市的实体 submissions 里 tickers 可能还是空的，那时只能人工看名字。
    status, name, note = verdict(
        "SPCX", {"name": "Space Exploration Technologies Corp.", "tickers": []})
    assert status == "无法判定"
    assert name == "Space Exploration Technologies Corp."
    assert "人工" in note


def test_a_missing_name_does_not_crash_the_check():
    status, name, _ = verdict("AAA", {"tickers": ["AAA"]})
    assert status == "✓"
    assert name == ""
