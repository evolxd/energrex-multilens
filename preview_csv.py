import csv, sys
sys.stdout.reconfigure(encoding="utf-8")
with open("results.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
for r in rows:
    print(r["ticker"], "|", r["final_综合得分(0-100)"], "| live:", r["live_fields_实时字段数"], "fields")
    for k, v in r.items():
        if k.startswith("raw_"):
            label = k.split("_", 2)[-1] if k.count("_") >= 2 else k
            print(f"   {label}: {v}")
    print()
