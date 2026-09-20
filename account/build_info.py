"""Which code is this process actually running.

Streamlit re-executes the page script on every interaction, so edits to
`account_monitor.py` appear immediately. Imported modules do not work that way:
`account/*.py` and `scoring/*.py` are cached in `sys.modules` and keep serving
the version loaded when the process started. A `git pull` that changes
`account/repository.py` or `account/beta_quality.py` therefore has no effect at
all until Streamlit is restarted -- while the page itself looks updated.

That gap cost a full evening. Beta overrides, the position dedupe and the chain
mapping were all pulled and none of them took effect; the dashboard rendered a
fresh timestamp beside byte-identical risk numbers, which reads as "the fix did
not work" rather than "the fix is not loaded". This module makes the difference
visible: the header can state the commit and flag any module whose file on disk
is newer than the copy in memory.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules whose staleness silently changes the numbers on screen. Page scripts
# are excluded: Streamlit re-runs those anyway.
_WATCHED_PREFIXES = ("account.", "scoring.")


def git_revision() -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h %cd", "--date=format:%m-%d %H:%M"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def stale_modules() -> list[str]:
    """Imported modules whose source file has changed since it was loaded.

    Compares each loaded module's file mtime against the import-time mtime
    recorded on first call, so the first call establishes the baseline and
    later calls report drift within this process' lifetime. A restart clears
    it, which is exactly the action being prompted.
    """
    stale: list[str] = []
    baseline = _import_baseline()
    for name, module in list(sys.modules.items()):
        if not name.startswith(_WATCHED_PREFIXES):
            continue
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            mtime = pathlib.Path(path).stat().st_mtime
        except OSError:
            continue
        loaded_at = baseline.get(name)
        if loaded_at is None:
            baseline[name] = mtime
        elif mtime > loaded_at + 1.0:          # 1s slack for filesystem noise
            stale.append(name)
    return sorted(stale)


_BASELINE: dict[str, float] = {}


def _import_baseline() -> dict[str, float]:
    return _BASELINE


def summary() -> str:
    """One line for the dashboard header."""
    revision = git_revision()
    stale = stale_modules()
    if stale:
        return f"{revision} ⚠️ {len(stale)} 个模块已更新未重启"
    return revision
