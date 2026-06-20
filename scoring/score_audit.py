"""
评分透明审计工具
================
对任意一只股票，打印每一步的原始输入值、公式、中间结果、最终子分。
用于核对评分逻辑。
"""
import sys, io
# Windows cmd/PowerShell defaults to cp1252 — force UTF-8 so box-drawing chars render
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from scoring_engine import (
    get_category, safe_val, linear_clamp, inverse_clamp,
    WEIGHT_CONFIG, CompanyCategory
)


# ── 辅助打印函数 ─────────────────────────────────
def _bar(score: float, width: int = 30) -> str:
    filled = int(score / 100 * width)
    return "█" * filled + "░" * (width - filled)

def _row(label, raw, formula, result, note=""):
    note_str = f"  ← {note}" if note else ""
    print(f"  {label:<35} 输入={str(raw):<10} | {formula:<40} → {result:>6.1f}分{note_str}")

def section(title: str):
    print()
    print("=" * 75)
    print(f"  {title}")
    print("=" * 75)

def subsection(title: str):
    print(f"\n  ── {title}")
    print("  " + "-" * 65)


# ── 核心审计函数 ─────────────────────────────────
def audit_stock(ticker: str, data: dict):
    cat = get_category(ticker)
    weights = WEIGHT_CONFIG[cat]

    print()
    print("╔" + "═" * 73 + "╗")
    print(f"║  评分审计报告：{ticker:<10}  类型：{cat.value:<15}  ║")
    print("╚" + "═" * 73 + "╝")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. 估值分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("① 估值分（valuation_score）  公式：越便宜→高分")

    peg        = safe_val(data.get("peg_ratio"), 2.0)
    ev_ebitda  = safe_val(data.get("ev_ebitda"), 30.0)
    ev_sales   = safe_val(data.get("ev_sales"), 15.0)
    rev_g      = safe_val(data.get("revenue_growth_yoy"), 0.20)
    rev_g_pct  = max(rev_g * 100, 1.0)   # ERG 单位修正：转为 % 形式
    fpe        = safe_val(data.get("forward_pe"), 50.0)
    fcf_yield  = safe_val(data.get("fcf_yield"), 0.01)

    peg_s      = inverse_clamp(peg,      best=0.5,  worst=2.5)
    if cat in (CompanyCategory.AI_CHIP, CompanyCategory.SEMI_EQUIP):
        evebitda_s = inverse_clamp(ev_ebitda, best=15.0, worst=55.0)
    else:
        evebitda_s = inverse_clamp(ev_ebitda, best=20.0, worst=80.0)
    fpe_s      = inverse_clamp(fpe,      best=20.0, worst=80.0)
    fcfy_s     = linear_clamp(fcf_yield, worst=0.0, best=0.05)

    subsection("各指标得分")
    print(f"  {'指标':<35} {'原始值':<12} {'公式':<42} {'得分':>6}")
    print("  " + "-" * 65)

    print(f"  {'PEG Ratio':<35} {peg:<12.2f} inverse_clamp(best=0.5, worst=2.5)       {peg_s:>6.1f}")
    print(f"    核对：({worst_best_formula(peg, 2.5, 0.5)})")

    evebitda_best = 15.0 if cat in (CompanyCategory.AI_CHIP, CompanyCategory.SEMI_EQUIP) else 20.0
    evebitda_worst = 55.0 if cat in (CompanyCategory.AI_CHIP, CompanyCategory.SEMI_EQUIP) else 80.0
    print(f"  {'EV/EBITDA':<35} {ev_ebitda:<12.1f} inverse_clamp(best={evebitda_best:.0f}, worst={evebitda_worst:.0f})         {evebitda_s:>6.1f}")
    print(f"    核对：({worst_best_formula(ev_ebitda, evebitda_worst, evebitda_best)})")

    if cat == CompanyCategory.AI_SOFTWARE:
        erg = ev_sales / rev_g_pct
        evrev_s = inverse_clamp(erg, best=0.10, worst=0.60)
        print(f"  {'ERG (EV/Rev÷RevG%)':<35} {erg:<12.3f} inverse_clamp(best=0.10, worst=0.60)  {evrev_s:>6.1f}")
        print(f"    核对：ERG = {ev_sales} ÷ {rev_g_pct:.1f}% = {erg:.3f}   ({worst_best_formula(erg, 0.60, 0.10)})")
    elif cat == CompanyCategory.AI_CHIP:
        erg = ev_sales / rev_g_pct
        evrev_s = inverse_clamp(erg, best=0.15, worst=0.80)
        print(f"  {'ERG (EV/Rev÷RevG%)':<35} {erg:<12.3f} inverse_clamp(best=0.15, worst=0.80)  {evrev_s:>6.1f}")
        print(f"    核对：ERG = {ev_sales} ÷ {rev_g_pct:.1f}% = {erg:.3f}   ({worst_best_formula(erg, 0.80, 0.15)})")
    elif cat == CompanyCategory.CYBERSECURITY:
        evrev_s = inverse_clamp(ev_sales, best=5.0, worst=25.0)
        print(f"  {'EV/Revenue':<35} {ev_sales:<12.1f} inverse_clamp(best=5, worst=25)          {evrev_s:>6.1f}")
        print(f"    核对：({worst_best_formula(ev_sales, 25, 5)})")
    else:  # SEMI_EQUIP
        evrev_s = inverse_clamp(ev_sales, best=6.0, worst=20.0)
        print(f"  {'EV/Revenue':<35} {ev_sales:<12.1f} inverse_clamp(best=6, worst=20)          {evrev_s:>6.1f}")
        print(f"    核对：({worst_best_formula(ev_sales, 20, 6)})")

    print(f"  {'Forward PE':<35} {fpe:<12.1f} inverse_clamp(best=20, worst=80)          {fpe_s:>6.1f}")
    print(f"    核对：({worst_best_formula(fpe, 80, 20)})  机构绝对估值锚（GS/MS/JPM均独立引用）")
    print(f"  {'FCF Yield':<35} {fcf_yield:<12.3f} linear_clamp(worst=0, best=0.05)         {fcfy_s:>6.1f}")
    print(f"    核对：({linear_formula(fcf_yield, 0.0, 0.05)})")

    subsection("加权合并（与 scoring_engine.calc_valuation_score 保持一致）")
    # 权重已更新：各类型新增 Forward PE（10-15%），从 PEG/EV/EBITDA 中扣出
    if cat == CompanyCategory.AI_CHIP:
        w = dict(peg=0.25, ev_ebitda=0.35, evrev=0.15, fcf=0.10, fpe=0.15)
    elif cat == CompanyCategory.AI_SOFTWARE:
        w = dict(peg=0.15, ev_ebitda=0.10, evrev=0.50, fcf=0.15, fpe=0.10)
    elif cat == CompanyCategory.CYBERSECURITY:
        w = dict(peg=0.20, ev_ebitda=0.15, evrev=0.40, fcf=0.15, fpe=0.10)
    else:  # SEMI_EQUIP
        w = dict(peg=0.25, ev_ebitda=0.40, evrev=0.15, fcf=0.10, fpe=0.10)

    print(f"  PEG分       {peg_s:6.1f} × {w['peg']:.2f} = {peg_s*w['peg']:6.2f}")
    print(f"  EV/EBITDA分 {evebitda_s:6.1f} × {w['ev_ebitda']:.2f} = {evebitda_s*w['ev_ebitda']:6.2f}")
    print(f"  ERG/EV/Rev分{evrev_s:6.1f} × {w['evrev']:.2f} = {evrev_s*w['evrev']:6.2f}")
    print(f"  FCF Yield分 {fcfy_s:6.1f} × {w['fcf']:.2f} = {fcfy_s*w['fcf']:6.2f}")
    print(f"  Forward PE分{fpe_s:6.1f} × {w['fpe']:.2f} = {fpe_s*w['fpe']:6.2f}")
    v_total = (peg_s*w['peg'] + evebitda_s*w['ev_ebitda'] + evrev_s*w['evrev']
             + fcfy_s*w['fcf'] + fpe_s*w['fpe'])
    assert abs(sum(w.values()) - 1.0) < 1e-9, f"权重之和={sum(w.values())}"
    print(f"  {'─'*45}")
    print(f"  ✅ valuation_score = {v_total:.1f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. 成长分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("② 成长分（growth_score）")

    rev_g  = safe_val(data.get("revenue_growth_yoy"), 0.10)
    eps_g  = safe_val(data.get("eps_growth_yoy"), 0.10)
    fcf_g  = safe_val(data.get("fcf_growth_yoy"), 0.10)
    fwd_g  = safe_val(data.get("next_year_revenue_growth_est"), rev_g)
    rev30  = safe_val(data.get("analyst_revision_30d"), 0.0)
    arr_g  = safe_val(data.get("arr_growth_yoy"), rev_g)

    if cat == CompanyCategory.AI_CHIP:
        rev_s = linear_clamp(rev_g,  worst=0.10, best=0.70)
        rev_range = "worst=10%, best=70%"
    elif cat == CompanyCategory.AI_SOFTWARE:
        rev_s = linear_clamp(rev_g,  worst=0.10, best=0.50)
        rev_range = "worst=10%, best=50%"
    elif cat == CompanyCategory.CYBERSECURITY:
        rev_s = linear_clamp(arr_g,  worst=0.10, best=0.35)
        rev_range = f"ARR增长 worst=10%, best=35%  (ARR_g={arr_g:.0%})"
    else:
        rev_s = linear_clamp(rev_g,  worst=-0.10, best=0.30)
        rev_range = "worst=-10%, best=30%"

    eps_s   = linear_clamp(eps_g,  worst=0.0,   best=0.60)
    fcfg_s  = linear_clamp(fcf_g,  worst=0.0,   best=0.50)
    fwd_s   = linear_clamp(fwd_g,  worst=0.05,  best=0.50)
    rev30_s = linear_clamp(rev30,  worst=-0.50, best=0.50)

    subsection("各指标得分")
    print(f"  {'收入增长YoY':<35} {rev_g:<12.1%} {rev_range:<42} {rev_s:>6.1f}")
    print(f"    核对：({linear_formula(rev_g, 0.10 if cat!=CompanyCategory.SEMI_EQUIP else -0.10, 0.70 if cat==CompanyCategory.AI_CHIP else 0.50)})")
    print(f"  {'EPS增长YoY':<35} {eps_g:<12.1%} linear(worst=0%, best=60%)               {eps_s:>6.1f}")
    print(f"    核对：({linear_formula(eps_g, 0.0, 0.60)})")
    print(f"  {'FCF增长YoY':<35} {fcf_g:<12.1%} linear(worst=0%, best=50%)               {fcfg_s:>6.1f}")
    print(f"    核对：({linear_formula(fcf_g, 0.0, 0.50)})")
    print(f"  {'NTM收入增长预期':<35} {fwd_g:<12.1%} linear(worst=5%, best=50%)               {fwd_s:>6.1f}")
    print(f"    核对：({linear_formula(fwd_g, 0.05, 0.50)})")
    print(f"  {'分析师上调动量30d':<35} {rev30:<12.1%} linear(worst=-50%, best=+50%)           {rev30_s:>6.1f}")
    print(f"    核对：({linear_formula(rev30, -0.50, 0.50)})")

    subsection("加权合并")
    print(f"  收入增长分     {rev_s:6.1f} × 0.35 = {rev_s*0.35:6.2f}")
    print(f"  EPS增长分      {eps_s:6.1f} × 0.20 = {eps_s*0.20:6.2f}")
    print(f"  FCF增长分      {fcfg_s:6.1f} × 0.15 = {fcfg_s*0.15:6.2f}")
    print(f"  NTM预期分      {fwd_s:6.1f} × 0.20 = {fwd_s*0.20:6.2f}")
    print(f"  分析师上调分   {rev30_s:6.1f} × 0.10 = {rev30_s*0.10:6.2f}")
    g_total = rev_s*0.35 + eps_s*0.20 + fcfg_s*0.15 + fwd_s*0.20 + rev30_s*0.10
    print(f"  {'─'*45}")
    print(f"  ✅ growth_score = {g_total:.1f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. 质量分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("③ 质量分（quality_score）")

    gm     = safe_val(data.get("gross_margin"), 0.60)
    fcfm   = safe_val(data.get("fcf_margin"), 0.10)
    roic   = safe_val(data.get("roic"), 0.15)
    de     = safe_val(data.get("debt_to_equity"), 0.50)
    pred   = safe_val(data.get("revenue_predictability_score"), 0.5)
    nrr    = safe_val(data.get("net_revenue_retention"), 1.10)
    rev_g2 = safe_val(data.get("revenue_growth_yoy"), 0.20)
    r40    = (rev_g2 + fcfm) * 100

    if cat == CompanyCategory.AI_SOFTWARE:
        gm_s   = linear_clamp(gm,   worst=0.60, best=0.85); gm_range="worst=60%, best=85%"
    elif cat == CompanyCategory.CYBERSECURITY:
        gm_s   = linear_clamp(gm,   worst=0.65, best=0.85); gm_range="worst=65%, best=85%"
    elif cat == CompanyCategory.AI_CHIP:
        gm_s   = linear_clamp(gm,   worst=0.40, best=0.70); gm_range="worst=40%, best=70%"
    else:
        gm_s   = linear_clamp(gm,   worst=0.35, best=0.58); gm_range="worst=35%, best=58%"

    fcfm_s = linear_clamp(fcfm,  worst=0.0,  best=0.40)
    roic_s = linear_clamp(roic,  worst=0.05, best=0.45)
    de_s   = inverse_clamp(de,   best=0.0,   worst=2.0)
    pred_s = linear_clamp(pred,  worst=0.0,  best=1.0)

    if cat in (CompanyCategory.AI_SOFTWARE, CompanyCategory.CYBERSECURITY):
        r40_s  = linear_clamp(r40,  worst=20,   best=70)
        nrr_s  = linear_clamp(nrr,  worst=0.90, best=1.35)
        r40_range = "worst=20, best=70"
    else:
        r40_s  = linear_clamp(r40,  worst=10,   best=50)
        nrr_s  = 50.0
        r40_range = "worst=10, best=50"

    subsection("各指标得分")
    print(f"  {'Rule of 40 计算':<35} RevG={rev_g2:.0%} + FCFm={fcfm:.0%} = {r40:.1f}")
    print(f"  {'Rule of 40分':<35} {r40:<12.1f} linear({r40_range})              {r40_s:>6.1f}")
    print(f"    核对：({linear_formula(r40, 20 if cat in (CompanyCategory.AI_SOFTWARE,CompanyCategory.CYBERSECURITY) else 10, 70 if cat in (CompanyCategory.AI_SOFTWARE,CompanyCategory.CYBERSECURITY) else 50)})")
    print(f"  {'毛利率':<35} {gm:<12.1%} linear({gm_range})    {gm_s:>6.1f}")
    print(f"    核对：({linear_formula(gm, 0.60 if cat==CompanyCategory.AI_SOFTWARE else 0.40, 0.85 if cat==CompanyCategory.AI_SOFTWARE else 0.70)})")
    print(f"  {'FCF Margin':<35} {fcfm:<12.1%} linear(worst=0%, best=40%)               {fcfm_s:>6.1f}")
    print(f"    核对：({linear_formula(fcfm, 0.0, 0.40)})")
    print(f"  {'ROIC':<35} {roic:<12.1%} linear(worst=5%, best=45%)               {roic_s:>6.1f}")
    print(f"    核对：({linear_formula(roic, 0.05, 0.45)})")
    print(f"  {'Debt/Equity (越低越好)':<35} {de:<12.2f} inverse(best=0, worst=2.0)              {de_s:>6.1f}")
    print(f"    核对：({worst_best_formula(de, 2.0, 0.0)})")
    print(f"  {'收入可预测性':<35} {pred:<12.2f} linear(worst=0, best=1.0)                {pred_s:>6.1f}")
    if cat in (CompanyCategory.AI_SOFTWARE, CompanyCategory.CYBERSECURITY):
        print(f"  {'NRR净收入留存':<35} {nrr:<12.2f} linear(worst=90%, best=135%)            {nrr_s:>6.1f}")
        print(f"    核对：({linear_formula(nrr, 0.90, 1.35)})")
    else:
        print(f"  {'NRR净收入留存':<35} {'N/A (非SaaS)':<12} 中性分50                                {nrr_s:>6.1f}")

    subsection("加权合并")
    if cat == CompanyCategory.AI_SOFTWARE:
        qw = dict(gm=0.20, fcf=0.20, r40=0.25, roic=0.10, de=0.05, pred=0.05, nrr=0.15)
    elif cat == CompanyCategory.CYBERSECURITY:
        qw = dict(gm=0.20, fcf=0.25, r40=0.20, roic=0.10, de=0.05, pred=0.05, nrr=0.15)
    elif cat == CompanyCategory.AI_CHIP:
        qw = dict(gm=0.25, fcf=0.30, r40=0.20, roic=0.15, de=0.10, pred=0.0,  nrr=0.0)
    else:
        qw = dict(gm=0.30, fcf=0.25, r40=0.15, roic=0.20, de=0.10, pred=0.0,  nrr=0.0)

    q_total = (gm_s*qw['gm'] + fcfm_s*qw['fcf'] + r40_s*qw['r40']
             + roic_s*qw['roic'] + de_s*qw['de'] + pred_s*qw['pred'] + nrr_s*qw['nrr'])
    print(f"  毛利率分       {gm_s:6.1f} × {qw['gm']:.2f} = {gm_s*qw['gm']:6.2f}")
    print(f"  FCF Margin分   {fcfm_s:6.1f} × {qw['fcf']:.2f} = {fcfm_s*qw['fcf']:6.2f}")
    print(f"  Rule of 40分   {r40_s:6.1f} × {qw['r40']:.2f} = {r40_s*qw['r40']:6.2f}")
    print(f"  ROIC分         {roic_s:6.1f} × {qw['roic']:.2f} = {roic_s*qw['roic']:6.2f}")
    print(f"  D/E分          {de_s:6.1f} × {qw['de']:.2f} = {de_s*qw['de']:6.2f}")
    if qw['pred'] > 0:
        print(f"  可预测性分     {pred_s:6.1f} × {qw['pred']:.2f} = {pred_s*qw['pred']:6.2f}")
    if qw['nrr'] > 0:
        print(f"  NRR分          {nrr_s:6.1f} × {qw['nrr']:.2f} = {nrr_s*qw['nrr']:6.2f}")
    print(f"  {'─'*45}")
    print(f"  ✅ quality_score = {q_total:.1f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. AI暴露分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("④ AI暴露分（ai_exposure_score）  ⚠️ 全部手动整理字段")

    ai_rev   = safe_val(data.get("ai_revenue_exposure_pct"), 0.30)
    ai_gr    = safe_val(data.get("ai_growth_contribution_pct"), 0.30)
    ai_pr    = safe_val(data.get("ai_profit_exposure_pct"), 0.30)
    dc       = safe_val(data.get("datacenter_exposure_pct"), 0.50)
    pkg      = safe_val(data.get("advanced_packaging_exposure_pct"), 0.20)
    plat     = safe_val(data.get("software_ai_platform_exposure_pct"), 0.25)
    backlog  = safe_val(data.get("ai_order_backlog_exposure"), 0.20)
    cyber_ai = safe_val(data.get("cybersecurity_ai_exposure_pct"), 0.20)

    ai_rev_s  = linear_clamp(ai_rev,  worst=0.10, best=0.85)
    ai_gr_s   = linear_clamp(ai_gr,   worst=0.10, best=0.80)
    ai_pr_s   = linear_clamp(ai_pr,   worst=0.10, best=0.85)
    dc_s      = linear_clamp(dc,      worst=0.25, best=0.85)
    pkg_s     = linear_clamp(pkg,     worst=0.05, best=0.60)
    plat_s    = linear_clamp(plat,    worst=0.10, best=0.70)
    backlog_s = linear_clamp(backlog, worst=0.05, best=0.60)
    cyber_s   = linear_clamp(cyber_ai,worst=0.05, best=0.60)

    subsection("各字段得分  ⚠️ 数据来源=手动整理/财报季更新")
    print(f"  {'AI收入占比':<35} {ai_rev:<12.0%} linear(worst=10%, best=85%)              {ai_rev_s:>6.1f}")
    print(f"    核对：({linear_formula(ai_rev, 0.10, 0.85)})")
    print(f"  {'AI增长贡献占比':<35} {ai_gr:<12.0%} linear(worst=10%, best=80%)              {ai_gr_s:>6.1f}")
    print(f"  {'AI利润占比':<35} {ai_pr:<12.0%} linear(worst=10%, best=85%)              {ai_pr_s:>6.1f}")

    if cat == CompanyCategory.AI_CHIP:
        print(f"  {'数据中心收入占比':<35} {dc:<12.0%} linear(worst=25%, best=85%)              {dc_s:>6.1f}")
        print(f"    核对：({linear_formula(dc, 0.25, 0.85)})")
        print(f"  {'先进封装暴露':<35} {pkg:<12.0%} linear(worst=5%, best=60%)               {pkg_s:>6.1f}")
        ai_total = ai_rev_s*0.30 + ai_gr_s*0.20 + dc_s*0.35 + pkg_s*0.15
        subsection("加权合并（AI芯片）")
        print(f"  AI收入分       {ai_rev_s:6.1f} × 0.30 = {ai_rev_s*0.30:6.2f}")
        print(f"  AI增长分       {ai_gr_s:6.1f} × 0.20 = {ai_gr_s*0.20:6.2f}")
        print(f"  数据中心分     {dc_s:6.1f} × 0.35 = {dc_s*0.35:6.2f}")
        print(f"  先进封装分     {pkg_s:6.1f} × 0.15 = {pkg_s*0.15:6.2f}")
    elif cat == CompanyCategory.AI_SOFTWARE:
        print(f"  {'AI平台功能占比':<35} {plat:<12.0%} linear(worst=10%, best=70%)              {plat_s:>6.1f}")
        print(f"  {'AI订单积压占比':<35} {backlog:<12.0%} linear(worst=5%, best=60%)               {backlog_s:>6.1f}")
        ai_total = ai_rev_s*0.30 + ai_gr_s*0.25 + plat_s*0.30 + backlog_s*0.15
        subsection("加权合并（AI软件）")
        print(f"  AI收入分       {ai_rev_s:6.1f} × 0.30 = {ai_rev_s*0.30:6.2f}")
        print(f"  AI增长分       {ai_gr_s:6.1f} × 0.25 = {ai_gr_s*0.25:6.2f}")
        print(f"  AI平台分       {plat_s:6.1f} × 0.30 = {plat_s*0.30:6.2f}")
        print(f"  AI积压分       {backlog_s:6.1f} × 0.15 = {backlog_s*0.15:6.2f}")
    elif cat == CompanyCategory.CYBERSECURITY:
        print(f"  {'网安AI功能占比':<35} {cyber_ai:<12.0%} linear(worst=5%, best=60%)               {cyber_s:>6.1f}")
        ai_total = ai_rev_s*0.25 + ai_gr_s*0.25 + ai_pr_s*0.20 + cyber_s*0.30
        subsection("加权合并（网络安全）")
        print(f"  AI收入分       {ai_rev_s:6.1f} × 0.25 = {ai_rev_s*0.25:6.2f}")
        print(f"  AI增长分       {ai_gr_s:6.1f} × 0.25 = {ai_gr_s*0.25:6.2f}")
        print(f"  AI利润分       {ai_pr_s:6.1f} × 0.20 = {ai_pr_s*0.20:6.2f}")
        print(f"  网安AI分       {cyber_s:6.1f} × 0.30 = {cyber_s*0.30:6.2f}")
    else:
        print(f"  {'先进封装暴露':<35} {pkg:<12.0%} linear(worst=10%, best=70%)              {pkg_s:>6.1f}")
        ai_total = ai_rev_s*0.25 + ai_gr_s*0.30 + ai_pr_s*0.15 + pkg_s*0.30
        subsection("加权合并（半导体设备）")
        print(f"  AI收入分       {ai_rev_s:6.1f} × 0.25 = {ai_rev_s*0.25:6.2f}")
        print(f"  AI增长分       {ai_gr_s:6.1f} × 0.30 = {ai_gr_s*0.30:6.2f}")
        print(f"  AI利润分       {ai_pr_s:6.1f} × 0.15 = {ai_pr_s*0.15:6.2f}")
        print(f"  先进封装分     {pkg_s:6.1f} × 0.30 = {pkg_s*0.30:6.2f}")

    print(f"  {'─'*45}")
    print(f"  ✅ ai_exposure_score = {ai_total:.1f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 5. 预期差分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("⑤ 预期差分（expectation_gap_score）")

    rev_beat  = safe_val(data.get("actual_revenue_vs_consensus"), 0.0)
    eps_beat  = safe_val(data.get("actual_eps_vs_consensus"), 0.0)
    guide_b   = safe_val(data.get("guidance_vs_consensus"), 0.0)
    reaction  = safe_val(data.get("earnings_reaction_score"), 0.0)
    mkt_exp   = safe_val(data.get("market_expectation_score"), 0.5)

    revb_s  = linear_clamp(rev_beat, worst=-0.08, best=0.10)
    epsb_s  = linear_clamp(eps_beat, worst=-0.10, best=0.15)
    guid_s  = linear_clamp(guide_b,  worst=-0.10, best=0.12)
    react_s = linear_clamp(reaction, worst=-0.15, best=0.20)
    mkt_s   = linear_clamp(1 - mkt_exp, worst=0.0, best=1.0)

    subsection("各指标得分")
    print(f"  {'实际收入 vs 预期':<35} {rev_beat:<12.1%} linear(worst=-8%, best=+10%)           {revb_s:>6.1f}")
    print(f"    核对：({linear_formula(rev_beat, -0.08, 0.10)})")
    print(f"  {'实际EPS vs 预期':<35} {eps_beat:<12.1%} linear(worst=-10%, best=+15%)          {epsb_s:>6.1f}")
    print(f"  {'指引 vs 市场预期':<35} {guide_b:<12.1%} linear(worst=-10%, best=+12%)          {guid_s:>6.1f}")
    print(f"  {'财报后次日涨跌幅':<35} {reaction:<12.1%} linear(worst=-15%, best=+20%)          {react_s:>6.1f}")
    print(f"  {'市场预期高低(越低越好)':<35} {mkt_exp:<12.2f} 1-mkt_exp={1-mkt_exp:.2f}, linear(0→1)           {mkt_s:>6.1f}")
    print(f"    注：market_expectation_score=0.75表示市场预期已很高，反转后=0.25 → 低分")

    subsection("加权合并")
    eg_total = revb_s*0.30 + epsb_s*0.25 + guid_s*0.25 + react_s*0.10 + mkt_s*0.10
    print(f"  收入beat分     {revb_s:6.1f} × 0.30 = {revb_s*0.30:6.2f}")
    print(f"  EPS beat分     {epsb_s:6.1f} × 0.25 = {epsb_s*0.25:6.2f}")
    print(f"  指引beat分     {guid_s:6.1f} × 0.25 = {guid_s*0.25:6.2f}")
    print(f"  市场反应分     {react_s:6.1f} × 0.10 = {react_s*0.10:6.2f}")
    print(f"  预期高低分     {mkt_s:6.1f} × 0.10 = {mkt_s*0.10:6.2f}")
    print(f"  {'─'*45}")
    print(f"  ✅ expectation_gap_score = {eg_total:.1f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 6. 风险扣分
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("⑥ 风险扣分（risk_penalty）  最大扣20分")

    beta    = safe_val(data.get("beta"), 1.2)
    vol30   = safe_val(data.get("volatility_30d"), 0.40)
    val_r   = safe_val(data.get("valuation_risk"), 0.5)
    conc    = safe_val(data.get("concentration_risk"), 0.3)
    liq     = safe_val(data.get("liquidity_risk"), 0.2)
    maxdd   = safe_val(data.get("max_drawdown_1y"), 0.30)

    beta_c  = linear_clamp(beta,  worst=0.5,  best=2.5)  / 100
    vol_c   = linear_clamp(vol30, worst=0.20, best=0.90) / 100
    val_c   = float(np.clip(val_r, 0, 1))
    conc_c  = float(np.clip((conc + liq) / 2, 0, 1))
    dd_c    = linear_clamp(maxdd, worst=0.10, best=0.70) / 100

    raw_p   = beta_c*0.25 + vol_c*0.20 + val_c*0.25 + conc_c*0.15 + dd_c*0.15
    penalty = float(np.clip(raw_p * 20, 0, 20))

    subsection("各风险组件  (每项0~1，1=最高风险)")
    print(f"  {'Beta风险':<35} beta={beta:<8.2f} linear(worst=0.5,best=2.5)/100         {beta_c:>8.4f}")
    print(f"    核对：beta={beta}, 得分={linear_clamp(beta,worst=0.5,best=2.5):.1f}/100 → 组件={beta_c:.4f}")
    print(f"  {'波动率30d年化':<35} vol={vol30:<9.0%} linear(worst=20%,best=90%)/100       {vol_c:>8.4f}")
    print(f"    核对：({linear_formula(vol30, 0.20, 0.90)})/100 → {vol_c:.4f}")
    print(f"  {'估值风险(手动0~1)':<35} {val_r:<12.2f} 直接使用，0=无险,1=极高估值风险        {val_c:>8.4f}")
    print(f"  {'集中度+流动性均值':<35} ({conc:.2f}+{liq:.2f})/2={conc_c:.3f}                              {conc_c:>8.4f}")
    print(f"  {'最大回撤1年':<35} dd={maxdd:<10.0%} linear(worst=10%,best=70%)/100       {dd_c:>8.4f}")

    subsection("合并计算")
    print(f"  raw_penalty = {beta_c:.4f}×0.25 + {vol_c:.4f}×0.20 + {val_c:.4f}×0.25 + {conc_c:.4f}×0.15 + {dd_c:.4f}×0.15")
    print(f"              = {beta_c*0.25:.4f} + {vol_c*0.20:.4f} + {val_c*0.25:.4f} + {conc_c*0.15:.4f} + {dd_c*0.15:.4f}")
    print(f"              = {raw_p:.4f}")
    print(f"  penalty     = {raw_p:.4f} × 20 = {raw_p*20:.2f}  (上限20)")
    print(f"  ✅ risk_penalty = {penalty:.2f}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 7. 最终分汇总
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    section("⑦ 最终分汇总（final_score）")

    w = weights
    raw = (v_total*w.valuation + g_total*w.growth + q_total*w.quality
         + ai_total*w.ai_exposure + eg_total*w.expectation_gap)
    final = float(np.clip(raw - penalty, 0, 100))

    if final >= 85:   rating = "⭐ Strong Buy"
    elif final >= 70: rating = "✅ Buy"
    elif final >= 55: rating = "👀 Watch"
    elif final >= 40: rating = "⚠️ Expensive"
    else:             rating = "🚫 Avoid"

    print()
    print(f"  {'子分':<25} {'分数':>7}   {'权重':>6}   {'加权分':>8}")
    print("  " + "─" * 55)
    print(f"  {'valuation_score':<25} {v_total:>7.1f} × {w.valuation:>5.2f} = {v_total*w.valuation:>8.2f}")
    print(f"  {'growth_score':<25} {g_total:>7.1f} × {w.growth:>5.2f} = {g_total*w.growth:>8.2f}")
    print(f"  {'quality_score':<25} {q_total:>7.1f} × {w.quality:>5.2f} = {q_total*w.quality:>8.2f}")
    print(f"  {'ai_exposure_score':<25} {ai_total:>7.1f} × {w.ai_exposure:>5.2f} = {ai_total*w.ai_exposure:>8.2f}")
    print(f"  {'expectation_gap_score':<25} {eg_total:>7.1f} × {w.expectation_gap:>5.2f} = {eg_total*w.expectation_gap:>8.2f}")
    print("  " + "─" * 55)
    print(f"  {'加权合计 (raw_score)':<25} {'':>7}   {'':>6}   {raw:>8.2f}")
    print(f"  {'- risk_penalty':<25} {'':>7}   {'':>6}   {-penalty:>8.2f}")
    print("  " + "═" * 55)
    print(f"  {'FINAL SCORE':<25} {'':>7}   {'':>6}   {final:>8.1f}")
    print()
    print(f"  进度条: [{_bar(final)}] {final:.1f}/100")
    print()
    print(f"  ★ 评级：{rating}")
    print()
    print("  ─── 核对检查清单 ────────────────────────────────────────")
    print(f"  □ 估值分原始值：PEG={data.get('peg_ratio')}, EV/EBITDA={data.get('ev_ebitda')}, EV/Sales={data.get('ev_sales')}")
    print(f"  □ 成长分原始值：RevG={data.get('revenue_growth_yoy'):.0%}, EPSG={data.get('eps_growth_yoy'):.0%}, FCFG={data.get('fcf_growth_yoy'):.0%}")
    print(f"  □ 质量分原始值：GM={data.get('gross_margin'):.0%}, FCFm={data.get('fcf_margin'):.0%}, ROIC={data.get('roic'):.0%}")
    print(f"  □ AI暴露字段：来源=手动整理，上次更新季度请确认")
    print(f"  □ 预期差：RevBeat={data.get('actual_revenue_vs_consensus'):.1%}, EPSBeat={data.get('actual_eps_vs_consensus'):.1%}")
    print(f"  □ 风险：Beta={data.get('beta')}, Vol30d={data.get('volatility_30d'):.0%}, MaxDD={data.get('max_drawdown_1y'):.0%}")
    print()


# ── 辅助：公式展示 ───────────────────────────────
def linear_formula(v, worst, best):
    numer = v - worst
    denom = best - worst
    if denom == 0:
        return "N/A"
    raw = numer / denom * 100
    clamped = max(0, min(100, raw))
    return f"({v:.4f}-{worst:.4f})/({best:.4f}-{worst:.4f})×100 = {raw:.1f} → clamp→{clamped:.1f}"

def worst_best_formula(v, worst, best):
    """inverse: higher v = lower score"""
    numer = worst - v
    denom = worst - best
    if denom == 0:
        return "N/A"
    raw = numer / denom * 100
    clamped = max(0, min(100, raw))
    return f"({worst:.4f}-{v:.4f})/({worst:.4f}-{best:.4f})×100 = {raw:.1f} → clamp→{clamped:.1f}"


if __name__ == "__main__":
    import sys
    from mock_data import MOCK_STOCKS

    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "NVDA"
    if ticker not in MOCK_STOCKS:
        print(f"可用股票：{list(MOCK_STOCKS.keys())}")
        sys.exit(1)

    audit_stock(ticker, MOCK_STOCKS[ticker])
