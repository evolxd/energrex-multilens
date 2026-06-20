import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scoring"))

from quant_engine import normalize_score, score_ticker, DIM_WEIGHTS

# normalize_score unit tests
s = normalize_score(0.64, 0.5, 3.5, "negative")
assert abs(s - 95.33) < 0.1, f"expected ~95.33 got {s}"

cap_test   = normalize_score(9999, 100, 0, "positive")
floor_test = normalize_score(-99,  100, 0, "positive")
assert cap_test   == 100.0, f"cap failed: {cap_test}"
assert floor_test == 0.0,   f"floor failed: {floor_test}"
print("normalize_score: cap/floor OK")

# Weights sum
total = sum(DIM_WEIGHTS.values())
assert abs(total - 1.0) < 1e-9, f"weights sum={total}"
print(f"DIM_WEIGHTS sum={total:.4f}: OK")

# Smoke-test with NVDA mock data
from mock_data import MOCK_STOCKS
from quant_data import QUANT_META
data = dict(MOCK_STOCKS["NVDA"])
data.update(QUANT_META.get("NVDA", {}))
data["_bad_fields"] = []
data["price_vs_200dma"] = 0.12
data["rsi_14"] = 58.0
result = score_ticker("NVDA", data)
assert 0 <= result.final_score <= 100, f"score out of range: {result.final_score}"
print(f"NVDA: final_score={result.final_score:.2f}  rating={result.rating}  circuit={result.circuit_triggered}")

# Smoke-test PLTR (SaaS)
data2 = dict(MOCK_STOCKS["PLTR"])
data2.update(QUANT_META.get("PLTR", {}))
data2["_bad_fields"] = []
data2["price_vs_200dma"] = 0.05
data2["rsi_14"] = 62.0
r2 = score_ticker("PLTR", data2)
print(f"PLTR: final_score={r2.final_score:.2f}  rating={r2.rating}")

# Circuit breaker test (synthetic)
data3 = dict(MOCK_STOCKS["NVDA"])
data3.update(QUANT_META.get("NVDA", {}))
data3["_bad_fields"] = []
data3["beta"] = 2.5
data3["max_drawdown_1y"] = -0.40
data3["price_vs_200dma"] = -0.15
data3["rsi_14"] = 35.0
r3 = score_ticker("CIRCUIT_TEST", data3)
assert r3.circuit_triggered, "circuit breaker should have fired"
print(f"Circuit test: triggered={r3.circuit_triggered}  score={r3.final_score:.2f}  reason={r3.circuit_reason}")

print("\nAll tests PASSED")
