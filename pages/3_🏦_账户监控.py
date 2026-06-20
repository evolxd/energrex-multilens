"""ENERGREX — 账户持仓监控入口（运行 account_monitor.py）"""
import sys, os, pathlib

_root = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_root))
os.chdir(str(_root))

_code = (_root / "account_monitor.py").read_text(encoding="utf-8-sig")
exec(
    compile(_code, str(_root / "account_monitor.py"), "exec"),
    {"__file__": str(_root / "account_monitor.py"), "__name__": "__main__"},
)
