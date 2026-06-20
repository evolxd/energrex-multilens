"""ENERGREX — 期权分析模块入口（运行 options_module.py）"""
import sys, os, pathlib

_root = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_root))
os.chdir(str(_root))

_code = (_root / "options_module.py").read_text(encoding="utf-8")
exec(
    compile(_code, str(_root / "options_module.py"), "exec"),
    {"__file__": str(_root / "options_module.py"), "__name__": "__main__"},
)
