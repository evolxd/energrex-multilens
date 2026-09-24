"""快照年龄：naive 时间戳必须按纽约时间理解，不是按运行机器的本地时间。

这里钉死的就是那个 bug：一份 3 分钟前生成的简报，在太平洋时区的机器上被显示
成「快照 -3.0小时前」。年龄是用来判断那块冻结的数还能不能信的，算错方向这个
警告就废了。
"""

import datetime

import pytest
import pytz

from account.snapshot_age import (
    ET,
    age_severity,
    describe,
    format_age,
    parse_snapshot_time,
    snapshot_age_hours,
)

_PT = pytz.timezone("America/Los_Angeles")


def test_a_naive_timestamp_is_new_york_time_not_the_machines_local_time():
    """这就是那个 bug。

    简报在 ET 16:35 生成、存成不带时区的 "2026-09-20T16:35:05"。页面在
    3 分钟后打开，机器在太平洋时区（本地 13:38）。旧代码拿本地 13:38 减
    ET 16:35 得到 -2.95 小时。
    """
    now_pt = _PT.localize(datetime.datetime(2026, 9, 20, 13, 38, 5))
    hours = snapshot_age_hours("2026-09-20T16:35:05", now=now_pt)
    assert hours == pytest.approx(0.05, abs=0.01)  # 3 分钟，不是 -3 小时


def test_the_same_instant_is_the_same_age_whatever_timezone_the_viewer_is_in():
    """同一份快照，在不同机器上打开必须算出同一个年龄。"""
    gen = "2026-09-20T16:35:05"
    instant = ET.localize(datetime.datetime(2026, 9, 20, 18, 35, 5))
    for tz in ("America/Los_Angeles", "America/New_York", "Asia/Shanghai", "UTC"):
        viewer_now = instant.astimezone(pytz.timezone(tz))
        assert snapshot_age_hours(gen, now=viewer_now) == pytest.approx(2.0, abs=1e-6)


def test_war_rooms_truncated_form_parses():
    """war_room.py 把 gen_time 截成 16 位、把 T 换成空格再存进 dict。"""
    now = ET.localize(datetime.datetime(2026, 9, 20, 18, 35))
    assert snapshot_age_hours("2026-09-20 16:35", now=now) == pytest.approx(2.0)


def test_a_timestamp_that_carries_its_own_offset_is_believed():
    """万一以后写入端改成带偏移，旧行和新行都得算对。"""
    now = ET.localize(datetime.datetime(2026, 9, 20, 18, 35, 5))
    assert snapshot_age_hours("2026-09-20T16:35:05-04:00", now=now) == pytest.approx(2.0)
    # 同一时刻用 UTC 写出来，年龄必须一样
    assert snapshot_age_hours("2026-09-20T20:35:05Z", now=now) == pytest.approx(2.0)


def test_unparseable_is_unknown_not_fresh():
    """'不知道多旧' 不能兜底成 '很新'——那是把警告关掉。"""
    for junk in (None, "", "   ", "待生成", "2026-13-45"):
        assert snapshot_age_hours(junk) is None
        assert parse_snapshot_time(junk) is None
        assert age_severity(snapshot_age_hours(junk)) is None
        assert format_age(None) == "时间未知"


def test_a_future_timestamp_is_shown_not_swallowed():
    """未来的时间戳意味着写入端时区错了或机器时钟不对，两件都要看见。"""
    now = ET.localize(datetime.datetime(2026, 9, 20, 16, 35))
    hours, sev, text = describe("2026-09-20T19:35:00", now=now)
    assert hours == pytest.approx(-3.0)
    assert sev == "future"
    assert "时钟或时区" in text


def test_clock_jitter_within_a_minute_reads_as_just_now():
    now = ET.localize(datetime.datetime(2026, 9, 20, 16, 35, 0))
    hours, sev, text = describe("2026-09-20T16:35:20", now=now)  # 未来 20 秒
    assert sev == "fresh"
    assert text == "刚刚"


def test_the_thresholds_are_the_ones_the_pages_colour_on():
    assert age_severity(0.5) == "fresh"
    assert age_severity(3.99) == "fresh"
    assert age_severity(4.0) == "warn"      # 橙：提醒以顶部实时值为准
    assert age_severity(23.9) == "warn"
    assert age_severity(24.0) == "stale"    # 红：隔夜的数，别读


def test_wording_switches_units_so_nobody_reads_zero_point_one_hours():
    assert format_age(0.05) == "3分钟前"
    assert format_age(2.5) == "2.5小时前"
    assert format_age(30.0) == "1.2天前"


def test_a_datetime_object_is_accepted_as_is():
    """存库层以后要是改成返回 datetime，不用再改这里。"""
    now = ET.localize(datetime.datetime(2026, 9, 20, 18, 35))
    naive = datetime.datetime(2026, 9, 20, 16, 35)
    assert snapshot_age_hours(naive, now=now) == pytest.approx(2.0)
    aware = ET.localize(datetime.datetime(2026, 9, 20, 16, 35))
    assert snapshot_age_hours(aware, now=now) == pytest.approx(2.0)


def test_a_naive_now_is_treated_as_the_machines_local_time():
    """页面里传 datetime.now()（naive）进来也要算对。"""
    gen = ET.localize(datetime.datetime(2026, 9, 20, 16, 35))
    naive_local_now = datetime.datetime.now()
    hours = snapshot_age_hours(gen, now=naive_local_now)
    expected = (datetime.datetime.now(datetime.timezone.utc) - gen).total_seconds() / 3600
    assert hours == pytest.approx(expected, abs=0.01)
