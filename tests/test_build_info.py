"""Tell the operator when the running process is behind the files on disk.

Streamlit re-runs the page script but not imported modules, so a `git pull`
that touches `account/*` or `scoring/*` changes nothing until the process is
restarted. On 2026-09-20 three separate fixes were pulled and none took effect;
the only visible symptom was a refreshed timestamp above unchanged risk
numbers, which looks like the fixes failing rather than never loading.
"""

import sys
import types

from account import build_info


def _fake_module(name: str, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    return module


def test_a_module_untouched_since_import_is_not_stale(tmp_path, monkeypatch):
    source = tmp_path / "quiet.py"
    source.write_text("x = 1")
    monkeypatch.setitem(sys.modules, "account.quiet", _fake_module("account.quiet", source))
    monkeypatch.setattr(build_info, "_BASELINE", {})

    build_info.stale_modules()                     # 建立基线
    assert "account.quiet" not in build_info.stale_modules()


def test_a_module_rewritten_after_import_is_reported(tmp_path, monkeypatch):
    source = tmp_path / "changed.py"
    source.write_text("x = 1")
    monkeypatch.setitem(sys.modules, "account.changed",
                        _fake_module("account.changed", source))
    monkeypatch.setattr(build_info, "_BASELINE", {})

    build_info.stale_modules()                     # 建立基线
    import os
    stat = source.stat()
    os.utime(source, (stat.st_atime + 600, stat.st_mtime + 600))

    assert "account.changed" in build_info.stale_modules()


def test_page_scripts_and_third_party_modules_are_ignored(tmp_path, monkeypatch):
    # Streamlit re-executes page scripts, and a library changing on disk is not
    # this project's problem -- only account/* and scoring/* matter.
    source = tmp_path / "other.py"
    source.write_text("x = 1")
    monkeypatch.setitem(sys.modules, "pandas_like", _fake_module("pandas_like", source))
    monkeypatch.setattr(build_info, "_BASELINE", {})

    build_info.stale_modules()
    import os
    stat = source.stat()
    os.utime(source, (stat.st_atime + 600, stat.st_mtime + 600))

    assert "pandas_like" not in build_info.stale_modules()


def test_summary_is_quiet_when_everything_is_current(monkeypatch):
    monkeypatch.setattr(build_info, "stale_modules", lambda: [])
    monkeypatch.setattr(build_info, "git_revision", lambda: "abc1234 09-20 01:00")
    assert build_info.summary() == "abc1234 09-20 01:00"


def test_summary_flags_a_restart_when_modules_drifted(monkeypatch):
    monkeypatch.setattr(build_info, "stale_modules", lambda: ["account.repository"])
    monkeypatch.setattr(build_info, "git_revision", lambda: "abc1234 09-20 01:00")
    assert "未重启" in build_info.summary()


def test_a_missing_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "account.gone",
                        _fake_module("account.gone", tmp_path / "nope.py"))
    monkeypatch.setattr(build_info, "_BASELINE", {})
    assert isinstance(build_info.stale_modules(), list)
