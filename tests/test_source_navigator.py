from scoring.source_navigator import guide_for_field, should_show_field


def test_sec_field_points_to_filing_and_exact_location():
    guide = guide_for_field("debt_to_equity", "NVDA", "D/E")
    assert "sec.gov" in guide.source_url
    assert "convertible" in guide.location
    assert guide.search_query.startswith("NVDA ")


def test_ai_field_warns_against_inventing_undisclosed_value():
    guide = guide_for_field("ai_profit_exposure_pct", "MSFT", "AI利润暴露")
    assert "未明确披露" in guide.location
    assert "不要用新闻推测值" in guide.action


def test_unknown_field_still_has_a_safe_official_source_path():
    guide = guide_for_field("new_metric", "AMD", "新指标")
    assert "Investor Relations" in guide.source
    assert "AMD" in guide.search_query
    assert guide.source_url.startswith("https://www.google.com/search?q=")


def test_queue_view_defaults_to_pending_only():
    assert should_show_field("pending", "只看待核验")
    assert not should_show_field("verified_auto", "只看待核验")
    assert should_show_field("optional", "待核验 + 可选")
    assert should_show_field("verified_auto", "全部字段")


def test_estimated_fields_show_up_in_review_queues():
    """2026-09-12 回归测试：estimated 字段（AI暴露组的6个字段）以前只在
    "全部字段"能看到，两个更窄的队列视图都会把它们过滤掉——用户想找到
    "该核对什么"的时候，这些字段压根不会出现在候选列表里。"""
    assert should_show_field("estimated", "只看待核验")
    assert should_show_field("estimated", "待核验 + 可选")
    assert should_show_field("estimated", "全部字段")
