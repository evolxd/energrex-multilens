"""
_run_price_refresh.py — 行情刷新独立运行器
由 _sidebar.py 通过 subprocess.Popen 调用。
刷新所有账户的期权现价/Greeks（MarketData）+ 股票现价（yfinance）。
"""
import sys, pathlib, ast, os, types, logging

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

_env = ROOT / ".env"
if _env.exists():
    for _l in _env.read_text(encoding="utf-8").splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, v = _l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Streamlit mock ────────────────────────────────────────
_st = types.ModuleType("streamlit")
_st.session_state = {}

def _cache_dec(*a, **kw):
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

# ── AST 过滤 ─────────────────────────────────────────────
_UI_LINE = 3933
src  = (ROOT / "account_monitor.py").read_text(encoding="utf-8")
tree = ast.parse(src, filename="account_monitor.py")
filtered = ast.Module(
    body=[n for n in tree.body if getattr(n, "lineno", 0) < _UI_LINE],
    type_ignores=[],
)
ast.fix_missing_locations(filtered)

_ns = {"__file__": str(ROOT / "account_monitor.py"),
       "__name__": "account_monitor"}
exec(compile(filtered, str(ROOT / "account_monitor.py"), "exec"), _ns)

ACCT_CFG = _ns["ACCT_CFG"]

# ── 刷新期权现价 + Greeks ─────────────────────────────────
for cfg in ACCT_CFG:
    aid = cfg["id"]
    try:
        upd, dele = _ns["_refresh_options_prices"](aid)
        logging.info(f"[price_refresh] 期权 {aid}: 更新={upd} 删除={dele}")
    except Exception as e:
        logging.warning(f"[price_refresh] 期权 {aid} 失败: {e}")

# ── 刷新股票现价 ─────────────────────────────────────────
for cfg in ACCT_CFG:
    aid = cfg["id"]
    try:
        r = _ns["_refresh_stock_prices"](aid)
        logging.info(f"[price_refresh] 股票 {aid}: {r}")
    except Exception as e:
        logging.warning(f"[price_refresh] 股票 {aid} 失败: {e}")

logging.info("[price_refresh] 完成")
