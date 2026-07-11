# ENERGREX AI Valuation Print Report Standard

This file is the stable layout contract for printed/PDF valuation reports.
Do not change print density only to fit more content on page 1. The goal is a
repeatable institutional report layout with readable spacing and no clipping.

## Page Standard

- Paper: A4 portrait.
- Browser print target: Save as PDF.
- Recommended Chrome settings: scale 100%, background graphics enabled.
- Minimum page margin: 10mm horizontal and 12mm vertical.
- Report body max width: 176mm.
- Sidebar, toolbar, filters, sliders, buttons, and other interactive controls
  must be hidden in print mode.

## Density Standard

- The report should feel balanced, not screen-like.
- Do not reduce page margin below 10mm horizontal unless explicitly creating a
  compact version.
- Do not let any chart, card, table, or text touch the printable boundary.
- Keep section spacing visible enough that the PDF reads as a report, not a
  screenshot.

## Chart Standard

- The five-factor radar chart and sub-score bar chart may stay in two columns
  on A4, but must remain inside the printable area.
- Radar chart target height: about 210px for print.
- Sub-score bar chart target height: about 160px for print.
- Plotly print zoom target: about 0.72.
- Bar-chart value labels should remain inside the bars or have enough right
  margin to avoid clipping.
- If any chart clips in PDF preview, fix chart width/margins first; do not
  solve it by shrinking the whole browser print scale below 90%.

## Drift Guards

- Any future change to `@media print`, Plotly chart height, report cards, or
  PDF export should be checked against this file.
- A print change passes only if:
  - A4 preview has no right-side clipping.
  - Page margins are visually consistent.
  - Radar and bar charts are readable.
  - First page does not look crowded.
  - No interactive Streamlit controls appear in the PDF.
  - No chart, table, or card is left at a narrower width than its container
    (a big blank strip next to a chart means a print CSS selector silently
    failed to match — verify it, do not assume it applied).

## Streamlit DOM Selector Standard (read before writing any `+`-sibling rule)

Every `st.xxx()` call — `st.markdown`, `st.plotly_chart`, `st.metric`, etc. —
renders inside its own `div[data-testid="stElementContainer"]` wrapper. Two
elements written on consecutive lines of Python are **not** DOM siblings of
each other; their *stElementContainer wrappers* are the siblings.

- ❌ Wrong: `.my-anchor + div [data-testid="stPlotlyChart"]` — this looks for
  a sibling of `.my-anchor` itself. Since `.my-anchor` is alone inside its own
  `stMarkdownContainer` → `stElementContainer`, it has no such sibling, and
  the selector silently matches nothing (no error, it just never applies).
- ✅ Right: use `:has()` to jump from the anchor up to its own
  `stElementContainer`, then step to the next one:
  ```css
  div[data-testid="stElementContainer"]:has(.my-anchor)
      + div[data-testid="stElementContainer"] [data-testid="stPlotlyChart"] { ... }
  ```
- Before shipping any anchor-based print selector, verify the match in
  DevTools (or headless Playwright) with:
  ```js
  const ec = document.querySelector('.my-anchor').closest('[data-testid="stElementContainer"]');
  !!ec.nextElementSibling?.querySelector('[data-testid="stPlotlyChart"]')
  ```
  If this prints `false`, the CSS will not apply — fix the selector before
  trusting the print preview.
- This project shipped exactly this bug once already (`.price-zone-print-anchor`
  in the 单股详情 price-zone chart, fixed 2026-07). Do not reintroduce the
  direct-sibling form for any future anchor-based print rule.

