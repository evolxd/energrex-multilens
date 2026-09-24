"""Golden cross-check for account.risk_gateway (Phase 4 of
docs/REFACTORING_WORKFLOW.md): proves the new gateway -- which owns the same
DB queries / network calls / on-disk config reads the account_monitor.py
wrappers used to do internally -- reproduces the Phase 2 golden snapshots
byte-for-byte, using the SAME synthetic scenarios and the SAME fixed
stand-ins for network/beta/risk-limits as test_risk_and_iv_golden.py.

This is the safety net gating Phase 4's actual point: rewiring the 4+1
external reflection call sites (_cascade.py x2, pages/5_..., pre_trade_
check.py) plus account_monitor.py's own wrappers to import
account.risk_gateway.get_risk_snapshot / get_iv_regime directly, instead of
going through _cascade._get_am()'s AST-slice reflection dict.
"""
import pathlib
import tempfile

import pytest
from freezegun import freeze_time

import account.db as account_db
import account.risk_gateway as gw
import test_risk_and_iv_golden as base


@pytest.fixture
def gateway_db(monkeypatch):
    """Point account.db at a throwaway sqlite file, same as base.ns's DB
    redirection, but without AST-execing account_monitor.py -- this suite
    only needs the gateway module, not account_monitor.py's own namespace.
    """
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(account_db, "DB_PATH", pathlib.Path(tmp_dir) / "risk_gateway_probe.db")
    account_db.init_db()
    yield


@pytest.fixture
def gateway_mocks(monkeypatch):
    """Pin the gateway's network/beta/risk-limits sources to the exact same
    fixed values test_risk_and_iv_golden.py's `ns` fixture pins on
    account_monitor.py's namespace, so the two are directly comparable."""
    monkeypatch.setattr(
        gw, "fetch_underlying_prices",
        lambda tickers, logger=None: {t: base._FIXED_PRICES[t] for t in tickers if t in base._FIXED_PRICES},
    )
    monkeypatch.setattr(
        gw, "get_atm_iv_batch",
        lambda tickers, api_key=None: {t: base._FIXED_IV[t] for t in tickers if t in base._FIXED_IV},
    )
    monkeypatch.setattr(gw, "_load_beta_map", lambda: dict(base._FIXED_BETA))
    monkeypatch.setattr(gw, "_load_risk_limits", lambda: dict(base._FIXED_RISK_LIMITS))


@pytest.mark.parametrize("name", sorted(base.IV_SCENARIOS))
def test_gateway_iv_regime_matches_golden_snapshot(gateway_db, name):
    acct = f"iv_{name}"
    base._clear_all(acct)
    base.IV_SCENARIOS[name](acct)
    result = gw.get_iv_regime(acct)
    actual = base._canonical(result)

    snapshot = base.SNAPSHOT_DIR / f"iv_{name}.json"
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"account.risk_gateway.get_iv_regime 在场景 {name} 下跟阶段二锁定的"
        "黄金快照不一致 -- 网关的取数逻辑跟旧的 account_monitor.py 薄包装不等价。")


@pytest.mark.parametrize("name", sorted(base.RISK_SCENARIOS))
def test_gateway_risk_snapshot_matches_golden_snapshot(gateway_db, gateway_mocks, name):
    acct = f"risk_{name}"
    base._clear_all(acct)
    base.RISK_SCENARIOS[name](acct)
    with freeze_time(base.FROZEN_NOW):
        result = gw.get_risk_snapshot(acct)
    actual = base._canonical(result)

    snapshot = base.SNAPSHOT_DIR / f"risk_{name}.json"
    assert actual == snapshot.read_text(encoding="utf-8"), (
        f"account.risk_gateway.get_risk_snapshot 在场景 {name} 下跟阶段二锁定的"
        "黄金快照不一致 -- 网关的取数逻辑跟旧的 account_monitor.py 薄包装不等价。")
