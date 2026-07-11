# HANDOFF — ENERGREX AI Valuation (ai_valuation)

Last updated: 2026-07-11 (revised twice post-initial-handoff: fixed the
investor-lens print clipping bug, then found and fixed the same bug in the
投资分析摘要 section, then generalized both into a standing rule in
`PRINT_REPORT_STANDARD.md` and a new `REPORT_STANDARD.md`), by Claude
(Sonnet 5) in a Claude Code session.
Read this file top to bottom before touching anything. It supersedes
`CLAUDE.md` and `PROJECT_STATUS.md` in this repo — both are stale (CLAUDE.md
describes a 10-ticker prototype; PROJECT_STATUS.md is dated 2026-06-13). Do
not trust them for current state; they're kept only for old-design context.

---

## 1. What this project is

A Streamlit app that scores ~86 AI-adjacent US stocks (NVDA, PLTR, ASML,
GEV, KLAC, etc.) on five dimensions — valuation, growth, quality, AI
exposure, expectation-gap — plus a momentum/risk overlay, and renders:

- A ranked leaderboard with Buy/Watch/Expensive/Avoid bands.
- A single-stock detail report (score breakdown, price-temperature bands,
  Damodaran valuation-discipline box, 8 "investor lens" cards, print/PDF
  export).
- A comparison view (two tickers side by side).
- An account-monitoring module (positions, Greeks, QQQ/SMH hedge planning,
  protective-put governance).
- An options-analysis module.

This is a **personal research tool**, not a product for outside investors.
It is separate from — and should not be confused with — the other project
in this user's environment, **"ENERGREX期权量化系统"** at
`C:\Users\evolx\Documents\ENERGREX期权量化系统` (a different, older,
PowerShell-driven risk-monitoring system with its own GitHub repos
`energrex-risk-monitor` / `energrex-investor-public`). This repo
(`energrex-multilens` on GitHub) is the newer, Python/Streamlit one.

## 2. Quick start

```bash
cd C:\Users\evolx\ai_valuation
python -X utf8 -m streamlit run home.py --server.port 8501
```
Open `http://localhost:8501`. Sidebar nav: 作战室（主页） / AI 估值评分 /
期权分析 / 账户监控.

**Known environment gotcha**: this machine's global Python env previously had
`starlette==0.27.0` (too old for `streamlit>=1.58`, which needs
`starlette>=0.40.0`) — this was upgraded in-session (`pip install --upgrade
starlette`). That upgrade may have broken the separate FastAPI backend in
`backend/` (which historically pinned `starlette<0.28` via old `fastapi`).
If you need to run `backend/main.py`, check for import errors first; you may
need a dedicated virtualenv to stop Streamlit and FastAPI fighting over
`starlette`/`anyio`/`uvicorn` versions in the same global env.

To refresh scoring data for one or more tickers:
```bash
python refresh_scores.py NVDA PLTR         # specific tickers
python refresh_scores.py                    # all 86
python refresh_scores.py --no-momentum      # skip RSI/200DMA (faster)
```
This reads/writes `results_validated.csv` (the file `app.py` actually
displays — **not** `results.csv`, which is a separate, less-curated
artifact).

## 3. Architecture — read this before adding a ticker or editing scoring

There are **two parallel scoring/category systems** in this repo. This is
the single most important thing to understand; missing a ticker in either
one silently produces wrong output (this bit us twice already — see §6).

### 3a. Pipeline path: `refresh_scores.py` → `results_validated.csv`
- `scoring/quant_engine.py` — scoring math (`score_ticker`).
- `scoring/quant_data.py` — `QUANT_META` (ticker → `{sector_tag, capex_rev}`,
  sector_tag ∈ {Hardware, SaaS, Cybersecurity}), `QUANT_AI_EXPOSURE`
  (supplemental AI-exposure fields for some tickers).
- `scoring/mock_data.py` — `MOCK_STOCKS`, the richest per-ticker manual
  dataset (AI-exposure %, expectation-gap %, valuation/concentration risk,
  etc.) — this is Layer 3 in the merge order documented at the top of
  `refresh_scores.py`.
- `scoring/user_overrides.json` — Layer 6 (**highest priority**, applied
  last, unconditionally overwrites everything below it). Has a
  `{value, status, verified_at, source}` shape per field. `status` is purely
  descriptive metadata — the merge code does **not** check it, so a
  `status: "pending"` placeholder overrides real "verified" data just as
  hard as a real verified value would. **This already caused one bug** (see
  §6) — always check whether a field you're about to add to mock_data.py is
  already shadowed by a stale override here.
- Output: `results_validated.csv` (86 rows, tracked in git — was previously
  gitignored, changed deliberately in this session so Streamlit Cloud/Render
  deploys have data; see §5).

### 3b. On-page narrative path: `app.py` → `scoring/scoring_engine.py`
- `app.py` imports `get_category`, `WEIGHT_CONFIG`, `calc_damodaran_report`
  directly from `scoring_engine.py` (**not** from quant_engine.py).
- `CompanyCategory` enum here is a **different taxonomy**:
  `AI_CHIP / AI_SOFTWARE / CYBERSECURITY / SEMI_EQUIP / MEGA_TECH`, with its
  own `TICKER_CATEGORY` static dict.
- Drives: the Damodaran valuation-discipline box, the plain-language
  investment-summary paragraphs, and (via `scoring/investor_lenses.py`) the
  8 investor-lens cards on the single-stock page.
- **A ticker missing from `TICKER_CATEGORY` here silently defaults to
  `AI_SOFTWARE`** and gets scored/narrated with SaaS-shaped assumptions
  (NRR weight, Rule-of-40 framing) even if it's actually a semiconductor
  equipment company. This exact bug hit ASML and GEV (fixed in commit
  `52eb75c`).

**Rule going forward**: when you add a new ticker, you must add it in
*both* places — `scoring/quant_data.py QUANT_META` (for the CSV pipeline)
*and* `scoring/scoring_engine.py TICKER_CATEGORY` (for the on-page
narrative). They use different taxonomies (`sector_tag` string vs
`CompanyCategory` enum) and there's no code that keeps them in sync.

### 3c. Other modules worth knowing about
- `scoring/kelly_position.py` / `kelly_backtest.py` / `kelly_bands.json` —
  half-Kelly position sizing by score band, shown on the leaderboard and
  single-stock page. Explicitly flagged in the UI as a **proxy**, not a real
  forward-tested backtest (no point-in-time score→forward-return dataset
  exists yet). `kelly_snapshot_logger.py` logs `(ticker, score, price)` on
  every `refresh_scores.py` run specifically to build that dataset over
  time — do not delete/reset this log.
- `scoring/edgar_fetcher.py` — pulls AI-exposure signal from SEC 10-Q/8-K
  for a handful of tickers (NVDA/MRVL/PLTR historically); most tickers still
  rely on manual mock data.
- `scoring/investor_lenses.py` — 8 framework-based "lens" cards (Cathie
  Wood-style growth/S-curve, Jensen Huang-style AI positioning, etc.).
  **Card titles were deliberately renamed** from real names ("木头姐 Cathie
  Wood") to generic lens names ("颠覆成长视角") with the person's name moved
  into a "参考框架：X" subtitle — a compliance-style change to reduce the
  appearance of claiming to represent real people's actual opinions. This
  change is in the **uncommitted** diff (§6) — don't accidentally revert it.
- `account/hedge_governance.py` — protective-put discipline rules for
  QQQ/SMH hedges (uncommitted, see §6).
- `PRINT_REPORT_STANDARD.md` — the print/PDF layout contract for the
  single-stock report. **Read this before touching any `@media print` CSS
  in `app.py`.** It documents a Streamlit-specific CSS gotcha that has
  already caused two separate bugs (see §6) — every `st.xxx()` call wraps
  its output in its own `stElementContainer`, so naive `.anchor + div`
  sibling selectors silently match nothing.

## 4. Deployment status — nothing is confirmed fully live yet

- **GitHub**: `https://github.com/evolxd/energrex-multilens`, branch
  `master`, visibility **PUBLIC** (changed from private in this session
  after a full history audit found no secrets/credentials — see git log
  message on commit `86d3b31` for what was checked). `.env` was never
  committed and stays gitignored.
- **Streamlit Community Cloud**: attempted first, abandoned. Free tier
  doesn't support custom domains, and the user wants `energrex.com` bound to
  it eventually. Also hit GitHub OAuth/App-permission friction when the repo
  was still private (moot now that it's public, but that path was dropped
  in favor of Render before it was fully tested working).
- **Render**: `render.yaml` added at repo root (commit `1fc5f55`) —
  `env: python`, free web-service plan, `startCommand: streamlit run home.py
  --server.port $PORT --server.address 0.0.0.0 --server.headless true`.
  **The user had not yet completed a Render deployment as of end of this
  session** — last discussed step was walking through
  render.com → New → Blueprint → select `evolxd/energrex-multilens`. If a
  new session picks this up, first ask the user whether Render deployment
  ever succeeded before assuming it's live.
- **Custom domain (`energrex.com`, already owned by user)**: not yet bound
  to anything. Plan was: deploy to Render first, then Settings → Custom
  Domains → add domain → CNAME/ALIAS record at the DNS provider. Free Render
  tier supports custom domains + free SSL; the only free-tier cost is a
  ~15-minute-idle spin-down (first visitor after idle eats a ~10-30s cold
  start, everyone else during the "warm" window is instant).
- Local dev server (`streamlit run home.py --server.port 8501`) was running
  throughout this session for verification — check if it's still up before
  assuming you need to restart it.

## 5. What was done in this session (chronological, verified — not just claimed)

Committed, in order (see `git log` for full messages):

1. `86d3b31` — Added GEV and ASML to the 86-ticker universe with real
   researched AI-exposure and earnings-surprise data (sourced via web
   search, not fabricated — see commit message for what's FACT vs
   ASSUMPTION). Refreshed KLAC's expectation-gap fields with current
   quarter data. **Fixed a real bug**: `user_overrides.json` had 4-6
   AI-exposure fields pinned to a flat `0.5 "pending"` placeholder for
   NVDA/PLTR/PANW/MU, silently overriding real researched values already
   sitting in `mock_data.py` — removed the dead overrides so real data
   flows through. Also changed `results_validated.csv` from gitignored to
   tracked (needed so a fresh deploy actually has data — verified there's
   no account/credential data in it, only public market/scoring fields).
2. `1fc5f55` — Added `render.yaml` for Render deployment (after discovering
   Streamlit Cloud free tier can't do custom domains).
3. `52eb75c` — Fixed `TICKER_CATEGORY` in `scoring_engine.py` missing ASML
   and GEV entirely (both were silently scored as `AI_SOFTWARE`/SaaS instead
   of `SEMI_EQUIP`). Also corrected several `user_overrides.json` fields
   that had been marked "verified" with unresearched template values, using
   real Q1'26 filings. Added automated sanity checks to
   `cross_validate_data.py` (magnitude bounds + cross-ticker duplicate-value
   detection) so this class of rubber-stamped bad data gets auto-flagged
   going forward.
4. `932ae42` — Added half-Kelly position sizing (`kelly_backtest.py`,
   `kelly_position.py`, `kelly_snapshot_logger.py`) and a price-staleness
   flag in `cross_validate_data.py` (catches ratios locked in before a big
   price move).
5. `02092db`, `63cc150`, `a2bf9b4` — Iterated the on-page narrative copy:
   expanded the "多维智库分析 Prompt" and the Damodaran box to full
   plain-language paragraphs, then **removed the copy-paste Prompt section
   entirely** in favor of...
6. `db495e7` — ...8 on-page "investor lens" cards
   (`scoring/investor_lenses.py`) that compute a rule-based, plain-language
   verdict per framework directly from the stock's own data. Verified
   against NVDA plus 3 edge-case (missing-data) profiles before committing.
7. `76b7ece`, `30d7ebb`, `8938607`, `e8a8505`, `768ce32`, `5e5ed9e` — Six
   rounds of print/PDF layout fixes for the single-stock report
   (sidebar/toolbar hidden in print, full-width content, radar+bar chart
   overflow, footer glued to last card, price-zone chart not filling width,
   investor-lens cards clipped at a page boundary, then the identical bug
   found in the 投资分析摘要 alert cards). Root cause for the last two:
   Chrome does not reliably honor `break-inside:avoid` on flex children
   (any `st.columns()` layout) — instead of pushing an overflowing
   card/alert to a fresh page it clips its text at the page edge, with
   whatever follows (footer, next section) rendering glued into the cut
   line. Fix pattern: force the specific `stHorizontalBlock` to
   `display:block` in print via an anchor-`:has()` selector, so pagination
   happens across plain block-level boxes instead of flex items. **This was
   diagnosed by generating a real PDF via Playwright `page.pdf()` and
   rasterizing each page with PyMuPDF (`fitz`)** —
   `page.emulate_media(media="print")` alone is a continuous-canvas
   approximation that does **not** reveal pagination clipping; you have to
   look at the actual paginated output. This is now a **generalized,
   documented standard**, not a one-off patch — see
   `PRINT_REPORT_STANDARD.md` → "Flex-Column Fragmentation Standard" for
   the exact 3-step recipe to apply to any future multi-card column, and
   `REPORT_STANDARD.md` (new this session, see item 8 below) for the
   content-level drift guard that references it.
8. A new `REPORT_STANDARD.md` was written this session — the content/
   editorial contract for the single-stock report (required section order,
   plain-language requirement, investor-lens naming/disclaimer rules,
   rating-display source-of-truth, the dual-taxonomy category gotcha from
   §3). It complements `PRINT_REPORT_STANDARD.md` (layout only) rather than
   duplicating it. Read it before editing anything in the single-stock
   report's content or copy.

Not committed (see §6).

Also done this session, outside git:
- Confirmed the running Streamlit app itself is functionally correct end to
  end (leaderboard, single-stock detail, comparison view all verified live
  in a real browser, not just code review).
- Full repo history audited for secrets before making the GitHub repo
  public (see `86d3b31` commit message and this session's transcript for
  the exact grep patterns used — nothing sensitive found, one false-positive
  `sk-` match traced to a public SEC filename).

## 6. ⚠️ Uncommitted work sitting in the working tree — review before losing it

As of end of session, `git status` shows:

```
M account_monitor.py
M scoring/investor_lenses.py
?? EXTERNAL_CONSENSUS_STANDARD.md
?? PRICE_BOUNDARY_STANDARD.md
?? PROTECTIVE_PUT_GOVERNANCE_STANDARD.md
?? account/hedge_governance.py
?? scoring/price_boundary_backtest.py
?? tests/test_hedge_governance.py
```

This is **not half-finished scaffolding** — it was verified working before
being left uncommitted:
- `account/hedge_governance.py` (protective-put discipline rules for
  QQQ/SMH hedges) is wired into `account_monitor.py`'s QQQ hedge-plan tab
  (renders a "保护性 Put 纪律检查" status card) — not a dead unused module.
- `tests/test_hedge_governance.py` — **7/7 tests pass** (verified this
  session: `python -m pytest tests/test_hedge_governance.py -v`).
- `scoring/investor_lenses.py` diff is the "rename lens cards from real
  person names to generic framework names" change described in §3c —
  already live in the running app (confirmed via a rendered PLTR print
  screenshot showing "🚀 颠覆成长视角" as the card title, not "木头姐 Cathie
  Wood").
- The three new `*_STANDARD.md` files (`EXTERNAL_CONSENSUS_STANDARD.md`,
  `PRICE_BOUNDARY_STANDARD.md`, `PROTECTIVE_PUT_GOVERNANCE_STANDARD.md`) are
  fully written governance docs, same style/quality as the already-committed
  `PRINT_REPORT_STANDARD.md`.
- `scoring/price_boundary_backtest.py` — a validation/backtest scaffold for
  the price-temperature-band feature; per its own docstring, with the
  current single-day snapshot data it should report `ENGINEERING_ONLY`
  status, not real predictive evidence.

**Action for next session**: read the diffs (`git diff account_monitor.py
scoring/investor_lenses.py`) and the new files, confirm with the user this
is all intentional finished work (it tested clean this session, but wasn't
explicitly re-confirmed with the user before this handoff was written), then
commit it. Don't silently commit without a quick sanity read — the user has
not seen a summary of the hedge-governance feature specifically in this
conversation, only implicitly done in an earlier, now-summarized part of the
session.

## 7. Bugs already fixed — don't reintroduce these

1. **`user_overrides.json` unconditional-priority footgun**: `status:
   "pending"` fields overwrite real data just as hard as `"verified"` ones.
   Before adding an override, check it isn't shadowing better data in
   `mock_data.py`. (Fixed for NVDA/PLTR/PANW/MU in `86d3b31`.)
2. **Two-taxonomy ticker mapping**: see §3. Adding a ticker to
   `quant_data.py` alone is not enough; `scoring_engine.py TICKER_CATEGORY`
   must also be updated, or the on-page narrative silently uses the wrong
   company-type assumptions. (Fixed for ASML/GEV in `52eb75c`.)
3. **Streamlit print CSS: `stElementContainer` sibling gotcha**: every
   `st.xxx()` call gets its own wrapper div; a `.some-anchor + div` selector
   written against two consecutive Python calls almost never matches the
   element you think it does. Always verify with `element.closest('[data-
   testid="stElementContainer"]').nextElementSibling` in DevTools/Playwright
   before trusting a print-CSS anchor selector. Full writeup with a
   copy-pasteable verification snippet in `PRINT_REPORT_STANDARD.md` →
   "Streamlit DOM Selector Standard". (Caused 3 separate print bugs this
   session: footer-glued-to-card, chart-overflow, chart-not-filling-width.)
4. **`break-inside: avoid` on container-level elements taller than one
   printed page is undefined behavior** — browsers tend to collapse the
   margin/border right at the forced break point instead of cleanly pushing
   content to the next page. Only apply it to genuinely atomic,
   card-sized elements (a single metric, a single chart, a single lens
   card), never to a multi-card grid container. (Documented in
   `PRINT_REPORT_STANDARD.md`, root-caused in commit `8938607`.)
5. **Global Python env `starlette` version conflict**: this machine's
   global env had `fastapi==0.104.1` (wants `starlette<0.28`) and
   `streamlit==1.58.0` (wants `starlette>=0.40`) installed together. Upgrading
   starlette to unblock Streamlit may have broken the FastAPI backend. A
   proper fix is a dedicated virtualenv per app; this was not done this
   session (flagged to the user, not resolved).
6. **Chrome print pagination does not reliably honor `break-inside:avoid`
   on flex children**: `st.columns()` renders a flex row
   (`stHorizontalBlock`). A card that overflows the current page's
   remaining space should move wholesale to the next page; instead Chrome
   clips its content at the page edge, and anything after it (like a
   footer) can render visually glued into that cut line. Fix pattern: force
   the flex container to `display:block` and its children to
   `width:100%`/`flex:none` in `@media print` so pagination happens across
   plain block-level boxes (reliable) instead of flex items (unreliable).
   **Diagnostic recipe** (don't trust `page.emulate_media(media="print")`
   alone — it doesn't paginate, so `getBoundingClientRect()` measurements
   under it look fine even when the real PDF clips):
   ```python
   # 1. Drive the Streamlit UI with Playwright to the state you want to check
   #    (see git history around commit 768ce32 for the full ticker-selection
   #    script — combobox selection needs care, there are 2 comboboxes on
   #    the 单股详情 page and Streamlit needs ~2.5s to re-render after a
   #    radio-nav click).
   # 2. Generate the REAL PDF, not just an emulated-media screenshot:
   page.pdf(path="debug.pdf", format="A4", print_background=True,
            margin={"top":"0mm","bottom":"0mm","left":"0mm","right":"0mm"})
   # 3. Rasterize every page and look at the last 2 pages specifically
   #    (that's where clipping/overlap bugs concentrate, since that's where
   #    content runs out mid-page):
   import fitz
   doc = fitz.open("debug.pdf")
   for i in range(len(doc)):
       doc[i].get_pixmap(dpi=150).save(f"page_{i+1}.png")
   ```
   PyMuPDF (`import fitz`) is already installed in this environment.

## 8. Current watchlist state

86 tickers in `results_validated.csv` as of this session (up from 84 at
session start — GEV and ASML were newly added; KLAC already existed but was
refreshed). Rating bands (recomputed live from `final_score` by `app.py`,
**not** the possibly-stale `rating_评级` CSV column — see
`_score_to_rating()` in `app.py`: ≥65 Strong Buy, ≥55 Buy, ≥45 Watch, ≥35
Expensive, else Avoid).

Top of leaderboard at last check: NVDA 78 (Strong Buy), PLTR 74 (Strong
Buy), MU 67 (Strong Buy — crossed the 65 threshold after the
user_overrides.json fix in §5.1), PANW ~59 (Buy). GEV/ASML/KLAC all landed
in Avoid territory (35-40 range) but their AI-exposure/expectation-gap
fields are researched estimates, not defaults — see their `mock_data.py`
entries for the sourcing notes (search dates, article citations) baked into
inline comments.

## 9. Next steps / open TODOs, roughly in priority order

1. **Decide on the 6 uncommitted files (§6)** — read, confirm with user,
   commit or discard.
2. **Confirm/complete Render deployment** — last known state was mid-setup;
   verify whether it ever finished, fix any build errors (psycopg2-binary
   is the most likely thing to fail to compile on Render's default image —
   check build logs first before assuming it's fine).
3. **Bind `energrex.com`** (or a subdomain like `app.energrex.com`) once
   Render deployment is confirmed live.
4. **Resolve the global-env starlette/fastapi conflict** (§7.5) if the
   FastAPI `backend/` service is ever needed again — probably wants its own
   virtualenv.
5. **Fill in the two "unknown manual" fields** for GEV and ASML
   (`raw_de_ratio`, `raw_fwd_rev_guide` — currently `n/a` by design, not
   fabricated) if the user wants tighter data-quality coverage on those two.
6. Longer-horizon, not urgent: `kelly_snapshot_logger.py`'s daily
   (ticker, score, price) log needs to accumulate real history before the
   half-Kelly sizing can graduate from "explicitly-flagged proxy" to a real
   backtest — nothing to do here except not delete the log.

## 10. Things a new session should NOT do without asking

- Don't assume Render/custom-domain deployment is live — verify first (§4).
- Don't commit the §6 files without a quick read-through and user
  confirmation — they're good work, but the user hasn't explicitly signed
  off on them in this conversation (that happened in an earlier,
  now-summarized part of the session).
- Don't touch `.gitignore`'s `results_validated.csv` exclusion back on —
  that was a deliberate, audited decision (§4/§5) to make deploys work.
- Don't add a ticker to only one of the two category systems (§3) — always
  both.
- Don't write a new print-CSS `+`-sibling selector without verifying the
  actual DOM relationship first (§7.3) — it has bitten this project three
  times already.
