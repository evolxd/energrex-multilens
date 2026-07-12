# ENERGREX Single-Stock Report Content Standard

This is the **content/editorial contract** for the single-stock valuation
report (单股详情 page). It governs what the report says and how it's
structured. It does not govern print/PDF layout — that's
`PRINT_REPORT_STANDARD.md`. Where a specific module has its own deeper
standard (price bands, external consensus, hedge governance), this file
defers to it and just states the cross-reference; do not duplicate those
rules here and let two copies drift apart.

Related standards (read these too, they are not optional context):
- `PRINT_REPORT_STANDARD.md` — print/PDF layout, pagination, CSS selector
  gotchas specific to Streamlit's DOM structure.
- `PRICE_BOUNDARY_STANDARD.md` — price-temperature-band module rules.
- `EXTERNAL_CONSENSUS_STANDARD.md` — external rating fields (Seeking Alpha
  etc.) as a calibration layer, not a scoring input.
- `PROTECTIVE_PUT_GOVERNANCE_STANDARD.md` — hedge discipline rules (account
  monitoring, not this report, but same "don't drift" philosophy).
- `HANDOFF.md` — session-to-session state; read it first if you're a new
  session, it explains the two-taxonomy category-mapping trap and other
  live gotchas that affect report correctness.

## 1. Required section order

The single-stock report is a fixed sequence. Do not reorder, silently
drop, or silently rename sections — if a section is genuinely obsolete,
remove it deliberately and update this file in the same change.

1. **Letterhead** (print-only) — ENERGREX brand bar, report date, data
   source (`results_validated.csv`), ticker/company/category, rating badge,
   score.
2. **Header cards** (screen + print) — ticker/company/category/type,
   validation confidence %, composite score + rating badge, score
   composition (加权合计 / 风险扣分 / Final Score), data quality label.
3. **价格温度带** (price-temperature band) — governed by
   `PRICE_BOUNDARY_STANDARD.md`. Must show the reason if it can't compute
   (never invent a price).
4. **能力雷达图 + 子分详情** (radar chart + sub-score bars) — five
   dimensions (估值/成长/质量/AI暴露/预期差), same weighting shown in both.
5. **关键财务指标** (key financial metrics) — PEG, EV/Sales, revenue
   growth, FCF margin, gross margin, Rule of 40 (or category-appropriate
   equivalent).
6. **投资分析摘要** (investment analysis summary) — see §2, plain-language
   requirement.
7. **Damodaran 估值纪律分析** (valuation discipline box) — ROIC-WACC excess
   return, reinvestment rate vs sustainable growth, plus a "🗣️ 解读" block
   translating the numbers into plain language (see §2).
8. **框架镜头分析** (8 investor-lens cards) — see §3 for naming/disclaimer
   rules. Must include the `framework-lens-intro` disclaimer block
   immediately above the cards.
9. **Print-only footer disclaimer** — see §4.

## 2. Plain-language requirement

Every dimension-level summary (投资分析摘要 one-liners, the Damodaran
解读 block, each investor-lens card's paragraph) must explain **why**, not
just restate a number.

- ❌ "估值分 55，偏高" (just restates the score)
- ✅ "PEG 1.87x，说明现在这个价格已经把不少乐观预期计入进去了。这不代表股票
  不能再涨，但意味着接下来每个季度的增长都得原样兑现" (explains the
  mechanism and the implication)

This was a deliberate rewrite this project went through (see `HANDOFF.md`
§5 items 5-6 for the commit history) — a prior version had a copy-paste
"multi-perspective Prompt" section for the user to paste into an external
AI chat. That was **removed and replaced** with the on-page investor-lens
cards precisely so the plain-language analysis happens directly in the
report. Do not reintroduce a copy-paste prompt section as a substitute for
on-page analysis.

## 3. Investor-lens card naming and disclaimer rules

The 8 lens cards are framework prompts computed from the stock's own data,
not real opinions of real people. This distinction must stay visible in
three places:

1. **Card title**: a generic lens name describing the angle, e.g. "颠覆成长
   视角", "AI瓶颈视角", "路线图执行视角" — **never** a real person's name as
   the card title (e.g. not "木头姐 Cathie Wood").
2. **Framework subtitle**: the real person's name goes here instead, always
   prefixed `参考框架：`, e.g. "参考框架：Cathie Wood · S曲线 · 颠覆式创新".
3. **In-paragraph phrasing**: refer to "该镜头的核心问题" / "该镜头关注" /
   "该镜头强调" — not "王/李/张最看重" (name-as-subject phrasing implying
   it's their real view).

The `framework-lens-intro` block directly above the cards must state, in
substance: this is a rule-based mapping of public frameworks onto this
stock's data, not the named individuals' actual opinions, endorsement, or
authorization, and it's for manual review, not a substitute for an
investment conclusion. Keep this even if the rest of the section is
edited — it's a compliance-adjacent disclaimer, not decorative copy.

Adding a 9th lens or changing an existing one's framework: put the
computation in `scoring/investor_lenses.py`, follow the existing per-lens
function signature `(ticker, data, category, scores) -> dict` returning
`{name, icon, framework, dimension, verdict, verdict_color, paragraph}`,
and verify it runs without crashing on at least one real ticker plus an
all-fields-missing edge case before shipping (this is how the existing 8
were verified — see `HANDOFF.md` §5 item 6).

## 4. Disclaimers — required, not optional copy

Two disclaimers are load-bearing, not decorative, and must not be trimmed
for space:

- **Print footer** (`report-print-footer` class): "本报告由 ENERGREX AI
  估值评分系统于 [date] 自动生成，评分与「框架镜头分析」均为规则化模型输出，
  不构成投资建议；相关人物姓名仅用于说明公开思想框架来源，非本人观点、授权
  或背书。请自行核查数据来源与口径。"
- **Framework-lens intro** (§3 above).

If you add a new report section that makes a forward-looking or
model-derived claim (price boundary, Kelly position size, hedge trigger,
etc.), it needs its own scoped disclaimer following the pattern already
used in `PRICE_BOUNDARY_STANDARD.md` ("价格区间不是主观猜测，而是..." /
"模型局限：...不是对未来股价的预测") — state what the number actually
measures and what it does not claim to predict.

## 5. Rating and score display

- The only source of truth for Final Score quality bands and actionable
  conclusions is `scoring/decision_policy.py`. Final Score bands are
  ≥80 综合强劲, ≥65 综合良好, ≥50 综合中性, ≥35 谨慎评估, else 风险较高. These are candidate
  quality labels, not trade orders.
- A displayed decision must use `evaluate_decision()` and pass all gates:
  valuation score ≥60, data validity ≥95%, no unresolved human-review flag,
  and an aligned Final Score. A high-price zone or failed data gate must never
  display an actionable conclusion or Kelly position size.
- **Do not read the CSV's `rating_评级` / `rating` column directly** in any
  new script or ad-hoc analysis (e.g. a bare `pandas.read_csv` outside
  `app.py`) and treat it as current — it can be stale relative to the live
  policy. Always run it through `score_band()` / `evaluate_decision()` before
  displaying or reasoning about it.
- Score color and rating badge color must come from the existing
  `score_color()` / `rating_color()` helpers — don't hardcode a hex value
  for a new UI element that shows a score or rating.

## 6. Data-quality and category-mapping correctness

- The header card must show validation confidence (`验证置信度`) and, when
  `human_review_required` is true, the `🔍待核查` badge. Don't hide these
  even if a ticker "looks fine" — they reflect the underlying data
  pipeline's own assessment, not the report author's judgment.
- **A ticker's category must be correct in both taxonomy systems** before
  its report can be trusted — `scoring/quant_data.py QUANT_META` (feeds the
  score) and `scoring/scoring_engine.py TICKER_CATEGORY` (feeds this
  report's narrative, via `get_category()`). A ticker missing from
  `TICKER_CATEGORY` silently defaults to `AI_SOFTWARE` and every
  plain-language section in this report — Damodaran box, investor lenses,
  investment summary — will narrate it with SaaS-shaped assumptions even
  if it's actually a chipmaker or industrial company. This exact bug hit
  ASML and GEV; see `HANDOFF.md` §3 and §7.2. When you add a ticker, grep
  both files, not just the one you remember.

## 7. Drift guard — what "passes" before shipping a report change

A change to this report is done only if:

- Section order matches §1 (or this file was updated in the same change).
- Every dimension summary you touched reads as a plain-language
  explanation, not a restated number (§2).
- Any investor-lens card you touched keeps the generic-title /
  参考框架-subtitle split and lens-phrasing convention (§3).
- Both required disclaimers are still present verbatim in substance (§4).
- You did not hardcode a rating threshold or color anywhere outside
  `_score_to_rating()` / `score_color()` / `rating_color()` (§5).
- If you added a ticker or changed a category, you checked **both**
  `QUANT_META` and `TICKER_CATEGORY` (§6).
- You checked `PRINT_REPORT_STANDARD.md` if you touched anything that
  renders differently in print vs screen — in particular, any new
  `st.columns(N)` block where a column stacks more than one card/alert must
  follow the Flex-Column Fragmentation Standard there, or it will clip
  mid-sentence in the printed PDF (this has already happened twice).
