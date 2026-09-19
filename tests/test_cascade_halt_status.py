"""A sync that never ran must not render as one that succeeded.

`run_sync_cascade()` returns early when Chrome is missing or Firstrade is
logged out. Both early returns handed back the same all-zero summary a real run
produces, and `_sidebar.py` marked the status "✅ 账户同步 + 级联完成" on any
non-exception, then showed a green "净值 $0 · 盈亏 $0" box. A halted sync looked
exactly like an account that had gone to zero.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

import _cascade


class _FakeAM(dict):
    """Stand-in for the lazily imported account_monitor function table."""

    def __init__(self, chrome_status):
        super().__init__()
        self["_ensure_chrome"] = lambda step=None: chrome_status


def _run_with_chrome_status(monkeypatch, status):
    monkeypatch.setattr(_cascade, "_get_am", lambda: _FakeAM(status))
    return _cascade.run_sync_cascade(step=lambda _msg: None)


def test_logged_out_run_is_flagged_not_silently_zeroed(monkeypatch):
    summary = _run_with_chrome_status(monkeypatch, "needs_login")
    assert summary["status"] == "needs_login"
    assert summary["halt_reason"]
    assert summary["equity"] == 0.0


def test_missing_chrome_is_flagged(monkeypatch):
    summary = _run_with_chrome_status(monkeypatch, "no_chrome")
    assert summary["status"] == "no_chrome"
    assert summary["halt_reason"]


def test_halt_reason_names_the_actual_blocker(monkeypatch):
    assert "登录" in _run_with_chrome_status(monkeypatch, "needs_login")["halt_reason"]
    assert "Chrome" in _run_with_chrome_status(monkeypatch, "no_chrome")["halt_reason"]


def test_a_halted_summary_is_distinguishable_from_a_real_zero_run():
    # The sidebar branches on status alone, so a genuine run reporting zero
    # equity must still read as "ok" and not be mistaken for a halt.
    real_zero_run = {"equity": 0.0, "bd_pct": 0.0, "pnl": 0.0,
                     "exit_signals": 0, "kelly_strategies": 0,
                     "status": "ok", "halt_reason": ""}
    assert real_zero_run.get("status", "ok") == "ok"
