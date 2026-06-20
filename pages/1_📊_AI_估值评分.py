"""ENERGREX — AI 估值评分模块入口（运行 app.py）"""
import sys, os, pathlib

_root = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_root))
os.chdir(str(_root))

_code = (_root / "app.py").read_text(encoding="utf-8")
exec(
    compile(_code, str(_root / "app.py"), "exec"),
    {"__file__": str(_root / "app.py"), "__name__": "__main__"},
)
