import sys
sys.path.insert(0, "scoring")
sys.stdout.reconfigure(encoding="utf-8")
from quant_engine import score_ticker
from quant_data import QUANT_META, QUANT_AI_EXPOSURE
from mock_data import MOCK_STOCKS

for ticker in ["PLTR", "CRWD", "SNOW", "DDOG"]:
    base = dict(MOCK_STOCKS[ticker])
    for k, v in QUANT_META.get(ticker, {}).items():
        base.setdefault(k, v)
    for k, v in QUANT_AI_EXPOSURE.get(ticker, {}).items():
        if base.get(k) is None:
            base[k] = v
    base.setdefault("sector_tag", "Hardware")
    base.setdefault("_bad_fields", [])

    nrr = base.get("net_revenue_retention")
    sector = base["sector_tag"]

    base_no_nrr = dict(base)
    base_no_nrr["net_revenue_retention"] = None

    r_with    = score_ticker(ticker, base)
    r_without = score_ticker(ticker, base_no_nrr)

    dq = r_with.dim_scores["quality"]      - r_without.dim_scores["quality"]
    da = r_with.dim_scores["ai_exposure"]  - r_without.dim_scores["ai_exposure"]
    df = r_with.final_score                - r_without.final_score

    print(f"{ticker} [{sector}]  NRR={nrr}")
    print(f"  quality贡献:      {dq*0.15:+.2f}分  (维度delta={dq:+.1f} x 15%)")
    print(f"  ai_exposure贡献: {da*0.20:+.2f}分  (维度delta={da:+.1f} x 20%)")
    print(f"  NRR总影响final: {df:+.2f}分")
    print()
