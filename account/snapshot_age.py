"""快照有多旧——时间戳的解析、时差换算和文案，只此一处。

为什么单独一个模块：`daily_briefing.gen_time` 存的是**纽约时间的墙上钟点，
不带时区后缀**（见 account_monitor.py `_save_daily_briefing`：
`datetime.datetime.now(_ET).strftime("%Y-%m-%dT%H:%M:%S")`）。两个页面
（account_monitor.py 门⑥ 和 war_room.py）各自写了一遍

    datetime.datetime.fromisoformat(gen_time) 拿 datetime.datetime.now() 去减

`now()` 是**运行机器的本地时间**。机器在太平洋时区时这个差正好是 3 小时，
于是一份 3 分钟前生成的快照被显示成「快照 -3.0小时前」。负的年龄不只是难看：
年龄是用来判断「这块冻结的数还能不能信」的，算错方向就等于这个警告失效——
而它本来就是为了堵 BD 271% vs 187% 那次上下不一致才加的。

所以解析和换算收在这里：naive 的一律按纽约时间理解（和写入端对齐），带时区
的按它自己的时区理解（万一以后写入格式变了，旧行也不会被算错）。

时间戳在未来时不遮掩。悄悄显示成「刚刚」会把两类事故藏起来：写入端时区改错，
以及机器时钟不对。所以只吞掉一分钟以内的抖动，再往前就明说。
"""

from __future__ import annotations

import datetime
from typing import Literal

import pytz

#: 写入端用的时区。改这里之前先改 account_monitor.py `_save_daily_briefing`，
#: 两边必须是同一个时区，否则年龄又会差出一个固定的偏移。
ET = pytz.timezone("America/New_York")

#: 超过这个小时数，页面上那块冻结的数就不该再当成当前值读。
STALE_HOURS = 24.0
WARN_HOURS = 4.0

#: 时钟抖动容差：写入和读取之间有几十秒的差、或者机器时钟微调，都算"刚刚"。
#: 超出这个范围的未来时间戳是真有问题，要显示出来。
_FUTURE_TOLERANCE_HOURS = 1.0 / 60.0

Severity = Literal["fresh", "warn", "stale", "future"]


def parse_snapshot_time(raw: object) -> datetime.datetime | None:
    """把存库的时间戳解析成带时区的 datetime；解析不了返回 None。

    接受 `2026-09-20T16:35:05`、`2026-09-20 16:35`（war_room 会截到 16 位）、
    带 `-04:00` 偏移的、以及以 `Z` 结尾的。**没有时区信息的按纽约时间理解**
    ——写入端就是这么写的。

    解析失败返回 None 而不是抛异常：调用方是页面渲染，为一个年龄标注崩掉整页
    不值得。但也不返回 `now()` 之类的兜底值，那会把"不知道多旧"显示成"很新"。
    """
    if isinstance(raw, datetime.datetime):
        dt = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return None
        text = text.replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None

    if dt.tzinfo is None:
        # is_dst=False：秋天回拨那一小时里有两个 01:30，取先出现的那个。
        # 一年一小时的歧义，对"这块数多旧"这个用途无所谓。
        return ET.localize(dt, is_dst=False)
    return dt


def snapshot_age_hours(raw: object,
                       now: datetime.datetime | None = None) -> float | None:
    """快照距今多少小时。解析不了返回 None，未来的时间戳返回负数。

    `now` 可以传 naive 的——按本地时区理解，跟 `datetime.now()` 一致。
    """
    gen = parse_snapshot_time(raw)
    if gen is None:
        return None
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.astimezone()  # naive 当本地时间
    return (now - gen).total_seconds() / 3600.0


def age_severity(hours: float | None) -> Severity | None:
    """年龄落在哪一档。None 进 None 出，调用方自己决定"不知道"怎么显示。"""
    if hours is None:
        return None
    if hours < -_FUTURE_TOLERANCE_HOURS:
        return "future"
    if hours >= STALE_HOURS:
        return "stale"
    if hours >= WARN_HOURS:
        return "warn"
    return "fresh"


def format_age(hours: float | None) -> str:
    """给人看的年龄文案。

    一小时以内按分钟说——「0.1小时前」没人这么读。
    """
    if hours is None:
        return "时间未知"
    if hours < -_FUTURE_TOLERANCE_HOURS:
        ahead = -hours
        if ahead >= 24:
            return f"时间戳在 {ahead / 24:.1f} 天后（时钟或时区有问题）"
        if ahead >= 1:
            return f"时间戳在 {ahead:.1f} 小时后（时钟或时区有问题）"
        return f"时间戳在 {ahead * 60:.0f} 分钟后（时钟或时区有问题）"
    if hours < _FUTURE_TOLERANCE_HOURS:
        return "刚刚"
    if hours < 1:
        return f"{hours * 60:.0f}分钟前"
    if hours < 24:
        return f"{hours:.1f}小时前"
    return f"{hours / 24:.1f}天前"


def describe(raw: object,
             now: datetime.datetime | None = None) -> tuple[float | None, Severity | None, str]:
    """`(小时数, 档位, 文案)` —— 页面一次调用就够。"""
    hours = snapshot_age_hours(raw, now)
    return hours, age_severity(hours), format_age(hours)
