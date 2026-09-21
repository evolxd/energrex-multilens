#!/usr/bin/env python3
"""positions 表有没有重复行——重复会让 Beta-Delta 虚高，而且没有任何症状。

positions 是只增不改的追加表，读取方按 MAX(sync_time) 取每只股票的最新一
行。同一个 (symbol, sync_time, position_type) 写进两行时，两行的 sync_time
一样，于是**两行都被认成"最新"**，那只股票的敞口就被计了两遍。

这不是假设。同一时刻跑两个 Streamlit 实例，两份 BackgroundScheduler 会让
_auto_sync 各跑一遍，实测同步 4 次后 BD 从 224% 一路虚增到 378%。

只读，不改任何东西。真要清用：
    python scripts/import_positions_offline.py --dedupe
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "energrex.db"


def main() -> int:
    if not DB.exists():
        print(f"找不到数据库: {DB}")
        return 2

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    print(f"positions 总行数: {total}\n")

    dup = conn.execute(
        """
        SELECT account_id, symbol, sync_time, position_type, COUNT(*) AS n
        FROM positions
        GROUP BY account_id, symbol, sync_time, position_type
        HAVING n > 1
        ORDER BY n DESC, symbol
        """
    ).fetchall()

    if not dup:
        print("✓ 没有重复行——BD 偏高不是这个原因造成的。")
    else:
        extra = sum(r["n"] - 1 for r in dup)
        print(f"✗ {len(dup)} 组重复，多出 {extra} 行。这些会被重复计进 BD：\n")
        for r in dup[:20]:
            print(f"   {r['symbol']:<8} {r['sync_time']}  ×{r['n']}")
        if len(dup) > 20:
            print(f"   …还有 {len(dup) - 20} 组")
        print(f"\n   清理：python scripts/import_positions_offline.py --dedupe")

    print("\n最近 6 次写入（同一时间戳出现两次 = 双实例同时同步的痕迹）：")
    for r in conn.execute(
        "SELECT sync_time, COUNT(*) AS n FROM positions "
        "GROUP BY sync_time ORDER BY sync_time DESC LIMIT 6"
    ):
        print(f"   {r['sync_time']}   {r['n']} 行")

    conn.close()
    return 1 if dup else 0


if __name__ == "__main__":
    raise SystemExit(main())
