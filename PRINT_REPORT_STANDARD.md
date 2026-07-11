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

## Flex-Column Fragmentation Standard (any `st.columns(N)` with N ≥ 2, N cards in a column)

`st.columns()` renders a `display:flex` row (`stHorizontalBlock`). Chrome's
print pagination does **not** reliably honor `break-inside: avoid` on flex
children. When a card/alert box near the bottom of a page doesn't fit in the
remaining space, instead of moving the whole element to a fresh page, Chrome
**clips its content at the page edge** — text gets cut mid-sentence, and
anything that follows (a footer, the next section) can render visually glued
into the cut line.

This has hit this project twice already, in every case where a column holds
**more than one stacked text-bearing element** (a chart or a single short
metric is fine — see Chart Standard above; the risk is specifically
multi-card/multi-alert stacks):

- `.investor-lens-card` — 8 cards, 2 columns, 4 stacked per column (fixed).
- `.investment-summary-anchor` — 5 `st.info/success/warning/error` alerts,
  2 columns (fixed).

**Standing rule**: any `st.columns(N)` block where at least one column stacks
more than one card/alert/text-block element must be forced to a single
column in `@media print`, using this exact pattern:

1. Add a Python-side marker as the first element inside one of the columns:
   ```python
   with col_a:
       st.markdown("<div class='my-section-anchor'></div>", unsafe_allow_html=True)
       st.info(...)
       ...
   ```
2. Add the print CSS:
   ```css
   div[data-testid="stHorizontalBlock"]:has(.my-section-anchor) {
       display: block !important;
   }
   div[data-testid="stHorizontalBlock"]:has(.my-section-anchor) [data-testid="stColumn"] {
       width: 100% !important;
       max-width: 100% !important;
       flex: none !important;
   }
   ```
3. Make sure the element type itself is in the atomic no-break list
   (`[data-testid="stAlert"]`, `[data-testid="stMetric"]`,
   `[data-testid="stPlotlyChart"]`, `[data-testid="stExpander"]`, or your
   custom card class) — single-column stacking only helps if the individual
   elements can also reliably avoid breaking mid-box.

**Do not** try to fix this by tuning margins, spacers, or `break-before`
timing instead — that was tried first (see `.report-print-footer`'s spacer
div and margin rules, added for a *different*, now-superseded diagnosis) and
does not address the actual cause. If you're looking at a print bug where
text is clipped mid-sentence (not just badly spaced), check for a
multi-column flex fragmentation issue first, before touching spacing.

**Verification is mandatory and must use real pagination, not
`emulate_media()` alone** — `page.emulate_media(media="print")` applies
print CSS but does not paginate; a `getBoundingClientRect()` measurement
under it can look perfectly fine while the real PDF still clips. Always
generate the actual PDF and rasterize it:
```python
page.pdf(path="debug.pdf", format="A4", print_background=True,
         margin={"top":"0mm","bottom":"0mm","left":"0mm","right":"0mm"})
import fitz  # PyMuPDF, already installed in this environment
doc = fitz.open("debug.pdf")
for i in range(len(doc)):
    doc[i].get_pixmap(dpi=150).save(f"page_{i+1}.png")
```
Then look at the last 1-2 pages specifically — clipping/overlap bugs
concentrate wherever content runs out mid-page.

