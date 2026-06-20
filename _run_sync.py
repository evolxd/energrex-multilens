"""
_run_sync.py — 账户同步独立运行器
由 _sidebar.py 通过 subprocess.Popen 调用，不依赖 Streamlit 上下文。
原理：用 AST 过滤只执行 account_monitor.py 里的函数定义段（< 第3933行），
     跳过 Streamlit UI 渲染段，然后调用 _auto_sync()。
"""
import sys, pathlib, ast, os, types, logging

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

# ── 加载 .env ─────────────────────────────────────────────
_env = ROOT / ".env"
if _env.exists():
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, v = _l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── 最小 Streamlit Mock（只需支持 @st.cache_resource / @st.cache_data）──
_st = types.ModuleType("streamlit")
_st.session_state = {}

def _cache_dec(*a, **kw):
    """统一 cache 装饰器 mock：支持 @st.cache_resource 和 @st.cache_data(ttl=x)。"""
    fn = a[0] if (a and callable(a[0])) else None
    def deco(f):
        _r = {}
        def w(*aa, **kk):
            k = id(f)
            if k not in _r:
                _r[k] = f(*aa, **kk)
            return _r[k]
        return w
    return deco(fn) if fn else deco

_st.cache_resource = _cache_dec
_st.cache_data     = _cache_dec

_noop = lambda *a, **kw: None
for _n in ["set_page_config", "markdown", "write", "info", "error", "warning",
           "success", "caption", "divider", "spinner", "toast", "rerun",
           "columns", "metric", "button", "selectbox", "radio", "tabs",
           "expander", "container", "header", "subheader", "title",
           "page_link", "file_uploader", "dataframe", "plotly_chart",
           "stop", "form", "form_submit_button", "empty", "progress"]:
    setattr(_st, _n, _noop)

_st.sidebar = types.SimpleNamespace(
    **{n: _noop for n in ["markdown", "write", "info", "error", "warning",
                           "success", "caption", "divider", "button",
                           "selectbox", "file_uploader", "title",
                           "header", "radio"]}
)
sys.modules["streamlit"] = _st

# ── AST 过滤：只执行 account_monitor.py 的函数定义段（st.set_page_config 之前）──
src  = (ROOT / "account_monitor.py").read_text(encoding="utf-8")
_UI_LINE = next(
    (i + 1 for i, line in enumerate(src.splitlines()) if "st.set_page_config" in line),
    len(src.splitlines()),
)
tree = ast.parse(src, filename="account_monitor.py")
filtered = ast.Module(
    body=[n for n in tree.body if getattr(n, "lineno", 0) < _UI_LINE],
    type_ignores=[],
)
ast.fix_missing_locations(filtered)

_ns = {"__file__": str(ROOT / "account_monitor.py"),
       "__name__": "account_monitor"}
exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), _ns)

# ── 执行同步 ─────────────────────────────────────────────
logging.info("[_run_sync] 开始同步 account_1 …")
_ns["_auto_sync"]("account_1")
logging.info("[_run_sync] 完成")
