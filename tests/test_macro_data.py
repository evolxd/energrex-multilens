from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scoring"))
import macro_data  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _monthly_series(values: dict[str, float | None]) -> dict:
    """Build a fake FRED JSON body from {date: value_or_None}, in whatever
    order given -- the module must not assume the input arrives sorted."""
    return {"observations": [
        {"date": d, "value": "." if v is None else str(v)}
        for d, v in values.items()
    ]}


def test_fred_observations_requires_api_key(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "")
    with pytest.raises(macro_data.MacroDataError, match="FRED_API_KEY"):
        macro_data._fred_observations("UNRATE")


def test_fred_observations_reverses_desc_order_to_ascending(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "dummy")
    # server returns newest-first (sort_order=desc) -- module must hand back oldest-first
    payload = _monthly_series({"2026-03-01": 3.0, "2026-02-01": 2.0, "2026-01-01": 1.0})
    monkeypatch.setattr(macro_data.requests, "get", lambda *a, **k: _FakeResponse(payload))
    obs = macro_data._fred_observations("FAKE")
    assert [o["date"] for o in obs] == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_fred_observations_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "dummy")
    monkeypatch.setattr(macro_data.requests, "get", lambda *a, **k: _FakeResponse({"observations": []}))
    with pytest.raises(macro_data.MacroDataError, match="空数据"):
        macro_data._fred_observations("FAKE")


def test_yoy_pct_change_hand_computed():
    # 13 monthly points, base=100 twelve months back from latest=108 -> +8.00%
    obs = [{"date": f"2025-{m:02d}-01", "value": "100"} for m in range(1, 13)]
    obs.append({"date": "2026-01-01", "value": "108"})
    pct, date = macro_data._yoy_pct_change(obs)
    assert pct == pytest.approx(8.0)
    assert date == "2026-01-01"


def test_yoy_pct_change_indexes_by_valid_points_not_calendar_months():
    # _yoy_pct_change explicitly does not correct for gaps (see its
    # docstring) -- it indexes 12 steps back through the *compacted* list of
    # valid points, not 12 calendar months back. With one '.' dropped, the
    # base point it lands on is whatever the compacted list puts 12 back,
    # not the calendar-correct month. Compute that same index independently
    # here rather than hand-picking dates, so the test can't silently drift
    # from what the function actually does.
    raw = [{"date": f"2024-{m:02d}-01", "value": "80"} for m in range(11, 13)]
    raw += [{"date": f"2025-{m:02d}-01", "value": "100" if m != 6 else "."} for m in range(1, 13)]
    raw.append({"date": "2026-01-01", "value": "110"})

    valid = macro_data._valid_points(raw)
    expected_base_date, expected_base_val = valid[-13]

    pct, date = macro_data._yoy_pct_change(raw)
    assert date == "2026-01-01"
    assert pct == pytest.approx(round((110.0 / expected_base_val - 1) * 100, 2))
    assert expected_base_date != "2025-01-01"   # confirms the gap really did shift the base


def test_yoy_pct_change_raises_rather_than_silently_shift_when_a_gap_starves_it():
    # Exactly enough raw points for a calendar-correct comparison (13), but
    # one is missing -- only 12 valid points remain, one short of what a
    # 12-periods-back lookup needs. Must raise, not quietly compare against
    # the wrong point.
    obs = [{"date": f"2025-{m:02d}-01", "value": "100" if m != 6 else "."} for m in range(1, 13)]
    obs.append({"date": "2026-01-01", "value": "110"})
    with pytest.raises(macro_data.MacroDataError, match="数据点不足"):
        macro_data._yoy_pct_change(obs)


def test_yoy_pct_change_raises_when_too_few_points():
    obs = [{"date": "2026-01-01", "value": "100"}]
    with pytest.raises(macro_data.MacroDataError, match="数据点不足"):
        macro_data._yoy_pct_change(obs)


def test_latest_value_and_date_skips_trailing_missing_marker():
    obs = [{"date": "2026-01-01", "value": "3.5"}, {"date": "2026-02-01", "value": "."}]
    value, date = macro_data._latest_value_and_date(obs)
    assert value == pytest.approx(3.5)
    assert date == "2026-01-01"


def test_latest_value_and_date_raises_when_all_missing():
    obs = [{"date": "2026-01-01", "value": "."}]
    with pytest.raises(macro_data.MacroDataError, match="缺失标记"):
        macro_data._latest_value_and_date(obs)


def test_fetch_cpi_yoy_end_to_end(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "dummy")
    values = {f"2025-{m:02d}-01": 300.0 + m for m in range(1, 13)}
    values["2026-01-01"] = 315.0
    monkeypatch.setattr(
        macro_data.requests, "get",
        lambda *a, **k: _FakeResponse({"observations": [
            {"date": d, "value": str(v)} for d, v in sorted(values.items(), reverse=True)
        ]}),
    )
    result = macro_data.fetch_cpi_yoy()
    assert result["metric"] == "CPI YoY"
    assert result["source"] == "FRED:CPIAUCSL"
    assert result["date"] == "2026-01-01"
    base = values["2025-01-01"]
    assert result["value_pct"] == pytest.approx(round((315.0 / base - 1) * 100, 2))


def test_fetch_nonfarm_payrolls_change_is_latest_minus_previous(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "dummy")
    payload = _monthly_series({"2026-01-01": 159000.0, "2025-12-01": 158823.0, "2025-11-01": 158700.0})
    monkeypatch.setattr(macro_data.requests, "get", lambda *a, **k: _FakeResponse(payload))
    result = macro_data.fetch_nonfarm_payrolls_change()
    assert result["value_k"] == pytest.approx(159000.0 - 158823.0)
    assert result["date"] == "2026-01-01"


def test_fetch_unemployment_rate_returns_latest(monkeypatch):
    monkeypatch.setattr(macro_data, "_FRED_KEY", "dummy")
    payload = _monthly_series({"2026-01-01": 4.1, "2025-12-01": 4.0})
    monkeypatch.setattr(macro_data.requests, "get", lambda *a, **k: _FakeResponse(payload))
    result = macro_data.fetch_unemployment_rate()
    assert result["value_pct"] == pytest.approx(4.1)
    assert result["date"] == "2026-01-01"


def test_fetch_treasury_10y_yield_divides_tnx_quote_by_ten(monkeypatch):
    import pandas as pd

    class _FakeTicker:
        def __init__(self, symbol):
            pass

        def history(self, period):
            idx = pd.to_datetime(["2026-08-29", "2026-08-30"])
            return pd.DataFrame({"Close": [41.9, 42.5]}, index=idx)

    fake_yf = type("FakeYF", (), {"Ticker": staticmethod(_FakeTicker)})
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    result = macro_data.fetch_treasury_10y_yield()
    assert result["value_pct"] == pytest.approx(4.25)
    assert result["date"] == "2026-08-30"
    assert result["source"] == "yfinance:^TNX"


def test_fetch_all_actuals_isolates_per_metric_failures(monkeypatch):
    def _boom():
        raise macro_data.MacroDataError("boom")

    def _ok():
        return {"metric": "ok", "value_pct": 1.0, "date": "2026-01-01", "source": "x"}

    fake_fetchers = {key: _ok for key in macro_data._ALL_FETCHERS}
    fake_fetchers["cpi_yoy"] = _boom
    monkeypatch.setattr(macro_data, "_ALL_FETCHERS", fake_fetchers)

    result = macro_data.fetch_all_actuals()
    assert result["cpi_yoy"] is None
    assert "cpi_yoy" in result["_errors"]
    assert result["core_cpi_yoy"]["value_pct"] == pytest.approx(1.0)
    assert result["unemployment_rate"]["value_pct"] == pytest.approx(1.0)


def test_load_treasury_snapshot_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "nope.json")
    assert macro_data.load_treasury_snapshot() is None


def test_save_then_load_treasury_snapshot_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "macro_snapshot.json")
    result = {"metric": "10Y Treasury Yield", "value_pct": 4.25, "date": "2026-08-30",
              "source": "yfinance:^TNX"}
    macro_data.save_treasury_snapshot(result)
    assert macro_data.load_treasury_snapshot() == result


def test_load_treasury_snapshot_returns_none_on_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "macro_snapshot.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", path)
    assert macro_data.load_treasury_snapshot() is None


def test_get_risk_free_rate_decimal_converts_percent_to_decimal(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "macro_snapshot.json")
    macro_data.save_treasury_snapshot({"value_pct": 4.25, "date": "2026-08-30"})
    assert macro_data.get_risk_free_rate_decimal() == pytest.approx(0.0425)


def test_get_risk_free_rate_decimal_none_when_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "nope.json")
    assert macro_data.get_risk_free_rate_decimal() is None


def test_get_risk_free_rate_decimal_none_when_value_pct_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "macro_snapshot.json")
    macro_data.save_treasury_snapshot({"date": "2026-08-30"})
    assert macro_data.get_risk_free_rate_decimal() is None


def test_resolve_current_risk_free_rate_prefers_fresh_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "macro_snapshot.json")
    monkeypatch.setattr(
        macro_data, "fetch_treasury_10y_yield",
        lambda: {"metric": "10Y Treasury Yield", "value_pct": 4.5, "date": "2026-09-03", "source": "x"},
    )
    decimal, msg = macro_data.resolve_current_risk_free_rate()
    assert decimal == pytest.approx(0.045)
    assert "4.5" in msg
    # the fresh result must also have been persisted as a side effect
    assert macro_data.load_treasury_snapshot()["value_pct"] == pytest.approx(4.5)


def test_resolve_current_risk_free_rate_falls_back_to_snapshot_on_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "macro_snapshot.json")
    macro_data.save_treasury_snapshot({"value_pct": 4.79, "date": "2026-09-02"})

    def _boom():
        raise macro_data.MacroDataError("network down")

    monkeypatch.setattr(macro_data, "fetch_treasury_10y_yield", _boom)
    decimal, msg = macro_data.resolve_current_risk_free_rate()
    assert decimal == pytest.approx(0.0479)
    assert "上次快照" in msg


def test_resolve_current_risk_free_rate_none_when_fetch_fails_and_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_data, "_SNAPSHOT_PATH", tmp_path / "nope.json")

    def _boom():
        raise macro_data.MacroDataError("network down")

    monkeypatch.setattr(macro_data, "fetch_treasury_10y_yield", _boom)
    decimal, msg = macro_data.resolve_current_risk_free_rate()
    assert decimal is None
    assert "手动常量" in msg
