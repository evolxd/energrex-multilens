"""ENERGREX — Bull Call Spread 评分模块入口（运行 bull_call_spread_module.py）"""
import sys, os, pathlib

_root = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_root))
os.chdir(str(_root))

_code = (_root / "bull_call_spread_module.py").read_text(encoding="utf-8-sig")
exec(
    compile(_code, str(_root / "bull_call_spread_module.py"), "exec"),
    {"__file__": str(_root / "bull_call_spread_module.py"), "__name__": "__main__"},
)
