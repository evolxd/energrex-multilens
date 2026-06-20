"""
Report generator
================
Produces:
  results_validated.csv   — original CSV + validation columns
  validation_report.html  — colour-coded, sortable, clickable
  validation_summary.json — aggregate statistics
"""

from __future__ import annotations
import json
import csv
from pathlib import Path
from datetime import datetime


# ── Status colours ─────────────────────────────────────────────────────

_STATUS_COLOR = {
    "PASS":   "#d4edda",   # green
    "REVIEW": "#fff3cd",   # yellow
    "FAIL":   "#f8d7da",   # red
}

_BADGE = {
    "PASS":   '<span style="background:#28a745;color:#fff;padding:2px 8px;border-radius:4px;font-size:0.85em">PASS</span>',
    "REVIEW": '<span style="background:#ffc107;color:#000;padding:2px 8px;border-radius:4px;font-size:0.85em">REVIEW</span>',
    "FAIL":   '<span style="background:#dc3545;color:#fff;padding:2px 8px;border-radius:4px;font-size:0.85em">FAIL</span>',
}


def _sort_key(row: dict) -> tuple:
    """Sort: FAIL → REVIEW → PASS, then formula_mismatch, source_conflict, final_score desc."""
    status_ord = {"FAIL": 0, "REVIEW": 1, "PASS": 2}.get(row.get("validation_status", ""), 3)
    mismatch   = 0 if str(row.get("formula_mismatch", "")).upper() == "TRUE" else 1
    conflict   = 0 if str(row.get("source_conflict",  "")).upper() == "TRUE" else 1
    score      = -float(row.get("final_综合得分(0-100)", row.get("final_score", 0)) or 0)
    return (status_ord, mismatch, conflict, score)


def _escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _link(url: str, label: str) -> str:
    if not url or not url.startswith("http"):
        return _escape(label)
    return f'<a href="{_escape(url)}" target="_blank">{_escape(label)}</a>'


def generate_csv(validated_rows: list[dict], path: str):
    if not validated_rows:
        return
    fieldnames = list(validated_rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(validated_rows)
    print(f"  [OK] results_validated.csv -> {path}  ({len(validated_rows)} rows)")


def generate_html(validated_rows: list[dict], path: str):
    rows_sorted = sorted(validated_rows, key=_sort_key)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    pass_n   = sum(1 for r in validated_rows if r.get("validation_status") == "PASS")
    review_n = sum(1 for r in validated_rows if r.get("validation_status") == "REVIEW")
    fail_n   = sum(1 for r in validated_rows if r.get("validation_status") == "FAIL")
    mismatch_n = sum(1 for r in validated_rows if r.get("formula_diff_level", "") not in ("", "minor_diff")
                    or str(r.get("formula_mismatch","")).upper() == "TRUE")
    conflict_n = sum(1 for r in validated_rows if str(r.get("source_conflict","")).upper() == "TRUE")
    review_req = sum(1 for r in validated_rows if str(r.get("human_review_required","")).upper() == "TRUE")

    def _td(val, bg=""):
        style = f' style="background:{bg}"' if bg else ""
        return f"<td{style}>{_escape(str(val))}</td>"

    rows_html = []
    for r in rows_sorted:
        status = r.get("validation_status", "")
        bg     = _STATUS_COLOR.get(status, "")
        ticker = r.get("ticker", "")
        company = r.get("company_公司名", r.get("company", ""))
        sector  = r.get("sector_板块", r.get("sector", ""))
        final   = r.get("final_综合得分(0-100)", r.get("final_score", ""))
        rating  = r.get("rating_评级", r.get("rating", ""))
        conf    = r.get("validation_confidence", "")
        badge   = _BADGE.get(status, _escape(status))
        diff_level = r.get("formula_diff_level", "")
        diff_reason = r.get("formula_diff_reason", "")
        diff_abs = r.get("formula_diff_abs", "")
        _diff_color = {
            "review_high":   "#dc3545",
            "review_medium": "#e67e22",
            "review_low":    "#f0ad4e",
            "minor_diff":    "#aaa",
        }
        diff_color = _diff_color.get(diff_level, "#aaa")
        diff_label = diff_level if diff_level else ("✓" if str(r.get("formula_mismatch","")).upper() == "TRUE" else "")
        mismatch = diff_label
        conflict = "✓" if str(r.get("source_conflict","")).upper() == "TRUE" else ""
        review   = "✓" if str(r.get("human_review_required","")).upper() == "TRUE" else ""
        recalc   = r.get("final_score_recalculated", "")
        mom_rc   = r.get("momentum_recalculated", "")
        sec_url  = r.get("source_urls_sec", "")
        notes    = r.get("validation_notes", "")

        yf_url  = f"https://finance.yahoo.com/quote/{ticker}"
        fmp_url = f"https://financialmodelingprep.com/financial-summary/{ticker}"

        notes_html = ""
        if notes:
            items = [n.strip() for n in str(notes).split("|") if n.strip()]
            notes_html = "<ul style='margin:0;padding-left:1em;font-size:0.8em'>" + \
                         "".join(f"<li>{_escape(i)}</li>" for i in items) + "</ul>"

        links = (
            f"{_link(yf_url, 'YF')} "
            f"{_link(fmp_url, 'FMP')} "
            f"{_link(sec_url, 'SEC') if sec_url else ''}"
        )

        rows_html.append(f"""
        <tr style="background:{bg}">
          <td>{_escape(ticker)}</td>
          <td>{_escape(company)}</td>
          <td>{_escape(sector)}</td>
          <td>{_escape(str(final))}</td>
          <td>{_escape(str(recalc))}</td>
          <td>{_escape(rating)}</td>
          <td>{badge}</td>
          <td>{_escape(str(conf))}</td>
          <td style="color:{diff_color};font-size:0.8em" title="{_escape(str(diff_reason))} Δ={_escape(str(diff_abs))}">{_escape(diff_label) or '–'}</td>
          <td style="color:{'#dc3545' if conflict else '#aaa'}">{conflict or '–'}</td>
          <td style="color:{'#ffc107' if review else '#aaa'}">{review or '–'}</td>
          <td>{_escape(str(mom_rc))}</td>
          <td>{links}</td>
          <td style="max-width:320px;font-size:0.8em">{notes_html or '–'}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Validation Report — {ts}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
<style>
  body {{ font-size: 0.9em; }}
  th   {{ white-space: nowrap; }}
  td   {{ vertical-align: top; }}
</style>
</head>
<body class="p-3">
<h4>AI成长股估值系统 · 数据验证报告 <small class="text-muted">{ts}</small></h4>

<div class="row g-2 mb-3">
  <div class="col-auto"><span class="badge bg-success fs-6">PASS {pass_n}</span></div>
  <div class="col-auto"><span class="badge bg-warning text-dark fs-6">REVIEW {review_n}</span></div>
  <div class="col-auto"><span class="badge bg-danger fs-6">FAIL {fail_n}</span></div>
  <div class="col-auto"><span class="badge bg-secondary fs-6">公式不符 {mismatch_n}</span></div>
  <div class="col-auto"><span class="badge bg-secondary fs-6">来源冲突 {conflict_n}</span></div>
  <div class="col-auto"><span class="badge bg-warning text-dark fs-6">需人工复核 {review_req}</span></div>
</div>

<table id="vt" class="table table-sm table-bordered table-hover">
<thead class="table-dark">
<tr>
  <th>Ticker</th><th>Company</th><th>Sector</th>
  <th>Final(CSV)</th><th>Final(重算)</th><th>Rating</th>
  <th>Status</th><th>Confidence</th>
  <th>公式不符</th><th>来源冲突</th><th>需复核</th>
  <th>动量(重算)</th><th>数据链接</th><th>备注</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>

<script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
<script>
$(document).ready(function() {{
  $('#vt').DataTable({{
    pageLength: 50,
    order: [],
    columnDefs: [{{ orderable: true }}]
  }});
}});
</script>
</body>
</html>"""

    Path(path).write_text(html, encoding="utf-8")
    print(f"  [OK] validation_report.html -> {path}")


def generate_json(validated_rows: list[dict], path: str):
    pass_n   = sum(1 for r in validated_rows if r.get("validation_status") == "PASS")
    review_n = sum(1 for r in validated_rows if r.get("validation_status") == "REVIEW")
    fail_n   = sum(1 for r in validated_rows if r.get("validation_status") == "FAIL")

    summary = {
        "generated_at":          datetime.now().isoformat(),
        "total_tickers":         len(validated_rows),
        "pass_count":            pass_n,
        "review_count":          review_n,
        "fail_count":            fail_n,
        "formula_mismatch_count": sum(1 for r in validated_rows
                                      if str(r.get("formula_mismatch","")).upper() == "TRUE"),
        "sector_conflict_count":  sum(1 for r in validated_rows
                                      if str(r.get("source_conflict","")).upper() == "TRUE"),
        "raw_data_conflict_count":sum(1 for r in validated_rows
                                      if str(r.get("raw_data_conflict","")).upper() == "TRUE"),
        "human_review_count":     sum(1 for r in validated_rows
                                      if str(r.get("human_review_required","")).upper() == "TRUE"),
        "avg_confidence":         round(
            sum(float(r.get("validation_confidence") or 0) for r in validated_rows)
            / max(len(validated_rows), 1), 3
        ),
        "tickers_fail":  [r["ticker"] for r in validated_rows if r.get("validation_status") == "FAIL"],
        "tickers_review":[r["ticker"] for r in validated_rows if r.get("validation_status") == "REVIEW"],
    }

    Path(path).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [OK] validation_summary.json -> {path}")
