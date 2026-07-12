"""
多维智库视角 — investor_lenses.py
=================================
把 8 位投资人的思维框架做成「页面内置的、用股票自身数据算出来的白话判断」，
而不是复制去问 AI 的 prompt。每个视角一个函数，输入统一为
(ticker, data, category, scores)，输出 {name, icon, framework, dimension,
verdict, verdict_color, paragraph}，供 app.py 单股详情页渲染成卡片。

框架来源：与 skills（cathie-wood / jensen-huang / howard-marks / mary-meeker /
peter-thiel / satya-nadella / charlie-munger-perspective / damodaran）保持一致。
苏姿丰 Lisa Su 无专属 skill，按其公认框架（竞争卡位 / 份额争夺 / 执行 /
毛利率扩张证明竞争力）建模。

⚠️ 这是规则化的「框架提示」，不是这些投资人的真实判断——它只是把每个人最核心的
几个检查点，套在当前这只股票的数字上，给出一个方向性的白话读数，供人工复核。

data 字典可用字段（来自 app.py build_csv_data）：
  peg_ratio, ev_sales, forward_pe, fcf_yield, revenue_growth_yoy, eps_growth_yoy,
  next_year_revenue_growth_est, gross_margin, fcf_margin, roic, debt_to_equity,
  net_revenue_retention, rsi_14, price_vs_200dma, beta, max_drawdown_1y
scores 字典：valuation, growth, quality, ai_exposure, expectation_gap,
  momentum, risk_penalty, final（均为 0-100，risk_penalty 为 0-20）
"""
from __future__ import annotations
from scoring_engine import CompanyCategory

# 颜色语义（与 app.py 现有配色一致）
_GREEN = "#2F4A3C"   # 强 / 正面（与单股详情页 editorial 主题一致）
_BLUE  = "#4A6B5C"   # 中性 / 混合
_AMBER = "#A67C3D"   # 谨慎
_RED   = "#8B3A2E"   # 负面


def _g(d: dict, k: str, default=None):
    v = d.get(k)
    return default if v is None else v


# ─────────────────────────────────────────────────────────────
# 1. 木头姐 Cathie Wood —— 成长 / S曲线 / 颠覆式创新
# ─────────────────────────────────────────────────────────────
def lens_cathie_wood(ticker, data, category, scores) -> dict:
    g      = scores.get("growth", 50)
    rev_g  = _g(data, "revenue_growth_yoy", 0.15)
    fwd    = _g(data, "next_year_revenue_growth_est", rev_g)
    gm     = _g(data, "gross_margin", 0.40)

    # S 曲线阶段：靠当前增速 + 未来增速是否明显放缓来推断
    if rev_g >= 0.35 and fwd >= rev_g * 0.75:
        stage = "还在陡峭上升期——渗透率快速提升、增速没有明显放缓迹象，颠覆红利大概率还没释放完"
    elif rev_g >= 0.20 and fwd < rev_g * 0.70:
        stage = "已经在 S 曲线中后段——增速开始收敛（未来预期比当前增速明显低），用现在的高增长线性外推容易被打脸"
    elif rev_g >= 0.20:
        stage = "处在爬升期，但需要盯紧下一两个季度增速是否守得住"
    else:
        stage = f"接近平台期（增速仅 {rev_g:.0%}）——颠覆故事的最陡阶段可能已经过去"

    if g >= 75 and rev_g >= 0.30:
        verdict, color = "高确信成长标的", _GREEN
    elif g >= 55:
        verdict, color = "成长仍在，但要盯紧减速信号", _BLUE
    else:
        verdict, color = "颠覆叙事支撑不足", _AMBER

    para = (
        f"当前收入增速 {rev_g:.0%}，"
        + (f"卖方预期未来 {fwd:.0%}。" if fwd is not None else "。")
        + f"从 S 曲线看，这家公司{stage}。"
        f"毛利率 {gm:.0%} 是 Wright's Law 成本曲线的间接体现——毛利率能持续走高，"
        f"往往意味着规模效应/成本下降还在按颠覆性技术的规律进行；如果毛利率停滞，"
        f"成长故事可能比市场想的更早遇到天花板。"
    )
    return {
        "name": "颠覆成长视角", "icon": "",
        "framework": "参考框架：Cathie Wood · S曲线 · 颠覆式创新", "dimension": "成长",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 2. 黄仁勋 Jensen Huang —— AI产业卡位 / 平台层级
# ─────────────────────────────────────────────────────────────
def lens_jensen_huang(ticker, data, category, scores) -> dict:
    ai = scores.get("ai_exposure", 50)
    gm = _g(data, "gross_margin", 0.40)

    layer = {
        CompanyCategory.AI_CHIP:       "AI 工厂的「卖铲子」环节——算力/芯片基础设施层，最先吃到需求，但也最容易被下一代架构或新进入者替代",
        CompanyCategory.SEMI_EQUIP:    "更上游的「造铲子的机器」——设备/材料层，卡位深、替代难，但增长节奏跟着资本开支周期走",
        CompanyCategory.AI_SOFTWARE:   "平台/应用层——护城河取决于是否有独特数据和用户粘性，而不只是算力",
        CompanyCategory.CYBERSECURITY: "应用层（安全）——AI 是否真正嵌进了产品能力，还是只是营销话术，需要看订单和留存",
        CompanyCategory.MEGA_TECH:     "全栈玩家——自研芯片 + 平台 + 应用都沾，关键看它在哪一层真正掌握了瓶颈定价权",
    }.get(category, "产业链位置需结合业务结构判断")

    if ai >= 75 and gm >= 0.50:
        verdict, color = "卡住了 AI 工厂的高价值环节", _GREEN
    elif ai >= 50:
        verdict, color = "受益于 AI，但位置有被替代风险", _BLUE
    else:
        verdict, color = "AI 暴露有限，更多是概念蹭热度", _AMBER

    para = (
        f"AI 暴露评分 {ai:.0f}。在 AI 产业分工里，这家公司属于：{layer}。"
        f"毛利率 {gm:.0%} 是判断它有没有卡到「瓶颈环节」的关键信号——真正掌握瓶颈的公司"
        f"能拿到高毛利并维持住；毛利率被压说明它所在的环节正在被商品化、议价权在流失。"
        f"该镜头的核心问题：瓶颈会不会随技术演进转移到别的层，把现在的赢家甩下？"
    )
    return {
        "name": "AI瓶颈视角", "icon": "",
        "framework": "参考框架：Jensen Huang · AI产业卡位 · 平台层级", "dimension": "AI暴露",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 3. 苏姿丰 Lisa Su —— 竞争卡位 / 份额争夺 / 执行（新增，无专属 skill）
# ─────────────────────────────────────────────────────────────
def lens_lisa_su(ticker, data, category, scores) -> dict:
    g      = scores.get("growth", 50)
    rev_g  = _g(data, "revenue_growth_yoy", 0.15)
    gm     = _g(data, "gross_margin", 0.40)
    roic   = _g(data, "roic", 0.15)

    applies = category in (CompanyCategory.AI_CHIP, CompanyCategory.SEMI_EQUIP)
    scope_note = "" if applies else "（⚠️ 该视角主要针对芯片/硬件的份额争夺，对本板块参考意义有限，仅供执行力侧写）"

    # 苏姿丰框架：能不能从巨头手里抢份额，靠的是路线图执行 + 毛利率扩张证明竞争力
    if rev_g >= 0.25 and gm >= 0.45 and roic >= 0.15:
        verdict, color = "正在从对手手里抢份额，执行在兑现", _GREEN
        body = "增速快于行业、毛利率站得住、资本回报也不错——这三条同时成立，通常意味着它不是靠降价换量，而是靠产品力实打实抢份额。"
    elif rev_g >= 0.15:
        verdict, color = "追赶中，需毛利率持续扩张来验证", _BLUE
        body = "增长有，但毛利率/资本回报还没证明它拿到了真正的竞争优势——路线图执行镜头下，关键标志是「份额涨的同时毛利率也涨」，只有一个不够。"
    else:
        verdict, color = "竞争力/份额趋势存疑", _AMBER
        body = "增速偏慢，暂时看不到从对手手里抢下地盘的迹象；要么产品周期还没到，要么在被更强的对手压制。"

    para = (
        f"收入增速 {rev_g:.0%}、毛利率 {gm:.0%}、ROIC {roic:.0%}。{body}"
        f"该镜头关注「路线图能不能一代代按时兑现」——一次两次执行到位不算，"
        f"要看它有没有能力连续多代产品都不掉链子。{scope_note}"
    )
    return {
        "name": "路线图执行视角", "icon": "",
        "framework": "参考框架：Lisa Su · 竞争卡位 · 份额争夺", "dimension": "成长/质量",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 4. 纳德拉 Satya Nadella —— AI变现模式 / 数据护城河
# ─────────────────────────────────────────────────────────────
def lens_satya_nadella(ticker, data, category, scores) -> dict:
    ai   = scores.get("ai_exposure", 50)
    nrr  = _g(data, "net_revenue_retention")
    fcfm = _g(data, "fcf_margin", 0.15)
    gm   = _g(data, "gross_margin", 0.40)

    if nrr is not None and nrr >= 1.15:
        mode = (f"偏 App Server 式的强变现——净收入留存 {nrr:.0%}，说明老客户在持续加购、"
                f"离不开它，AI 是包在整体解决方案里卖的，议价权强")
        v_hint = _GREEN
    elif category == CompanyCategory.AI_CHIP and gm >= 0.55:
        mode = ("卖的是算力但拿到了高毛利，接近「准平台」——短期吃满 AI 资本开支红利，"
                "但要警惕这本质仍是卖资源，一旦供给跟上、议价权会松动")
        v_hint = _BLUE
    else:
        mode = ("更接近 Token 工厂 / 资源型变现——按调用量、按算力卖，议价权弱、容易被价格战卷进去，"
                "缺少「客户离不开」的锁定")
        v_hint = _AMBER

    if ai >= 70 and (nrr is not None and nrr >= 1.15):
        verdict, color = "强变现 + 数据护城河", _GREEN
    elif ai >= 50:
        verdict, color = "变现路径待验证", _BLUE
    else:
        verdict, color = "资源型变现，易被生态方吃掉", v_hint

    para = (
        f"AI 暴露 {ai:.0f}，FCF 利润率 {fcfm:.0%}"
        + (f"，净收入留存 {nrr:.0%}。" if nrr is not None else "。")
        + f"变现模式判断：{mode}。该镜头的核心问题——它有没有别人拿不到的数据护城河？"
        f"有，就能对生态里的其他玩家收「过路费」；没有，它做的 AI 功能迟早被更大的平台方"
        f"直接抄进自己的产品里，白忙一场。"
    )
    return {
        "name": "AI变现视角", "icon": "",
        "framework": "参考框架：Satya Nadella · 数据护城河 · 生态税", "dimension": "AI暴露",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 5. 霍华德·马克斯 Howard Marks —— 周期 / 钟摆 / 风险
# ─────────────────────────────────────────────────────────────
def lens_howard_marks(ticker, data, category, scores) -> dict:
    v    = scores.get("valuation", 50)
    rp   = scores.get("risk_penalty", 8)
    rsi  = _g(data, "rsi_14")
    v200 = _g(data, "price_vs_200dma")
    de   = _g(data, "debt_to_equity", 0.5)
    beta = _g(data, "beta", 1.2)

    # 钟摆位置
    if (rsi is not None and rsi >= 70) or (v200 is not None and v200 >= 0.25):
        pendulum, p_state = "贪婪端", "greed"
    elif (rsi is not None and rsi <= 35) or (v200 is not None and v200 <= -0.15):
        pendulum, p_state = "恐惧端", "fear"
    else:
        pendulum, p_state = "中间地带", "mid"

    # 永久损失风险（≠波动）：主要看负债
    loss_risk = "偏高（负债较重，基本面出问题时回不来的概率更大）" if de and de > 2.0 else "可控（负债不重，主要风险是估值波动而非本金永久损失）"

    if p_state == "greed" and v < 45:
        verdict, color = "贪婪端 + 估值偏贵，赔率不佳", _AMBER
        judge = "钟摆已经摆到贪婪端、估值也偏贵——即使公司质量真好，未来几年的好消息很可能已经被提前花掉了，这个时点的赔率不划算。"
    elif p_state == "fear" and v >= 55:
        verdict, color = "情绪偏恐惧，可能存在错杀", _GREEN
        judge = "钟摆摆到恐惧端、估值反而便宜——如果基本面没有实质恶化，这种被市场情绪打下去的位置，往往是逆向布局的机会。"
    elif p_state == "greed":
        verdict, color = "情绪偏热，但估值尚可", _BLUE
        judge = "价格动能已经把情绪推到贪婪端，不过估值本身还没到离谱的程度——涨势可以享受，但要清醒：贪婪端的追高，容错空间在收窄。"
    elif p_state == "fear":
        verdict, color = "情绪偏冷，但估值未见便宜", _BLUE
        judge = "情绪已经偏恐惧，但估值还没跌出明显的安全边际——市场在担心的东西可能是真的，别只因为跌了就抄底，先确认基本面有没有实质问题。"
    else:
        verdict, color = "周期温度中性", _BLUE
        judge = "钟摆在中间地带，没有明显的贪婪或恐惧极端，估值和情绪暂时都不构成强烈的方向性信号。"

    para = (
        f"情绪钟摆当前在{pendulum}"
        + (f"（RSI {rsi:.0f}" if rsi is not None else "（")
        + (f"、价格 vs 200日均 {v200:+.0%}）。" if v200 is not None else "）。")
        + f"{judge} Beta {beta:.2f}，永久性资本损失风险{loss_risk}——"
        f"周期风险镜头强调：要担心的是本金永久亏掉，不是短期波动。"
    )
    return {
        "name": "周期风险视角", "icon": "",
        "framework": "参考框架：Howard Marks · 钟摆心理 · 永久损失", "dimension": "质量/风险",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 6. Mary Meeker —— 采用曲线 / 预期差
# ─────────────────────────────────────────────────────────────
def lens_mary_meeker(ticker, data, category, scores) -> dict:
    eg  = scores.get("expectation_gap", 50)
    fwd = _g(data, "next_year_revenue_growth_est")
    g   = scores.get("growth", 50)

    if eg >= 65:
        verdict, color = "采用曲线还有超预期空间", _GREEN
        body = "预期差评分偏高，说明市场对它的预期还没打满——采用曲线的实际速度有机会跑赢共识，剩下的超预期空间较大。"
    elif eg >= 45:
        verdict, color = "采用速度大致已被定价", _BLUE
        body = "预期差中性，市场对它渗透/采用节奏的定价大致到位，需要有新的、尚未被共识捕捉到的数据信号才能带来惊喜。"
    else:
        verdict, color = "预期已打满，超预期空间小", _AMBER
        body = "预期差偏低，说明乐观预期已经被充分甚至过度计入——采用曲线哪怕继续向上，也未必能再制造超预期，反而一次不及预期就容易被重罚。"

    para = (
        f"预期差评分 {eg:.0f}"
        + (f"，卖方预期未来增速 {fwd:.0%}。" if fwd is not None else "。")
        + f"{body} 采用曲线镜头关注「别人还没注意到的领先数据」——"
        f"某个新兴市场率先验证了模式、或海外渗透率数据领先几个季度，这类信号常常提前预示"
        f"接下来会发生什么，但大部分本土投资者没看。"
    )
    return {
        "name": "采用曲线视角", "icon": "",
        "framework": "参考框架：Mary Meeker · 数据优先 · 预期差", "dimension": "预期差",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 7. 彼得·蒂尔 Peter Thiel —— 0→1 vs 1→N / 垄断
# ─────────────────────────────────────────────────────────────
def lens_peter_thiel(ticker, data, category, scores) -> dict:
    gm   = _g(data, "gross_margin", 0.40)
    roic = _g(data, "roic", 0.15)
    g    = scores.get("growth", 50)

    if gm >= 0.60 and roic >= 0.20:
        verdict, color = "有垄断/准垄断特征，像 0→1 创造者", _GREEN
        body = ("高毛利 + 高资本回报同时成立，是「垄断租」的典型信号——说明它大概率创造了一个"
                "别人一时进不来的新类别，拥有定价权，而不是在红海里拼杀。")
    elif gm >= 0.40:
        verdict, color = "有一定壁垒，但谈不上垄断", _BLUE
        body = ("毛利率说明它有护城河、但还不到垄断级别——处在「有差异化但仍面临竞争」的中间地带，"
                "需要盯紧壁垒是在变宽还是在变窄。")
    else:
        verdict, color = "商品化竞争，1→N 无垄断租", _AMBER
        body = ("毛利率偏薄，更像在一个已经被充分竞争的市场里做 1→N 的复制扩张——"
                "缺少垄断带来的定价权，规模再大也难享受高利润。")

    para = (
        f"毛利率 {gm:.0%}、ROIC {roic:.0%}。{body}"
        f"垄断镜头的核心问题：这家公司掌握了什么「大多数人不相信、但正确」的秘密？"
        f"以及 10 年后，它会是这个市场的最后赢家吗——垄断才是目标，竞争本身就是失败。"
    )
    return {
        "name": "垄断质量视角", "icon": "",
        "framework": "参考框架：Peter Thiel · 0→1 · 垄断 · 秘密", "dimension": "元判断",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 8. 查理·芒格 Charlie Munger —— 能力圈 / 逆向 / 质量+公道价格
# ─────────────────────────────────────────────────────────────
def lens_charlie_munger(ticker, data, category, scores) -> dict:
    q    = scores.get("quality", 50)
    v    = scores.get("valuation", 50)
    roic = _g(data, "roic", 0.15)
    de   = _g(data, "debt_to_equity", 0.5)

    good_biz = q >= 70 and roic >= 0.15 and (de is None or de < 1.0)

    if good_biz and v >= 45:
        verdict, color = "简单持久的好生意 + 价格还算公道", _GREEN
        body = ("高质量、高资本回报、负债不重，估值也没贵到离谱——这正是芒格最想要的组合："
                "用公道的价格持有一家高质量公司，然后让复利和时间发挥作用。")
    elif good_biz and v < 45:
        verdict, color = "好生意，但价格不够公道", _AMBER
        body = ("生意本身是好的，但现在的价格已经偏贵——能力圈镜头会把它放进「太难/等待」的篮子里，"
                "耐心等一个更合理的价格，而不是追高。")
    else:
        verdict, color = "质量存疑，划入「回避」篮子", _RED
        body = ("资本回报、负债或整体质量还没达到「无可争议的好生意」标准——按能力圈框架，"
                "看不懂或不够确定的，直接放进「太难」堆里回避，不勉强出手。")

    para = (
        f"质量评分 {q:.0f}、ROIC {roic:.0%}"
        + (f"、负债权益比 {de:.2f}。" if de is not None else "。")
        + f"{body} 能力圈框架的三个篮子——「可以投」「不能投」「太难」——大多数标的都该进第三个；"
        f"只在能力圈内、又简单又便宜的极少数机会上重手出击。"
    )
    return {
        "name": "能力圈质量视角", "icon": "",
        "framework": "参考框架：Charlie Munger · 能力圈 · 质量+公道价格", "dimension": "元判断",
        "verdict": verdict, "verdict_color": color, "paragraph": para,
    }


# ─────────────────────────────────────────────────────────────
# 驱动：按维度顺序返回全部视角
# ─────────────────────────────────────────────────────────────
_LENS_ORDER = [
    lens_cathie_wood,     # 成长
    lens_jensen_huang,    # AI暴露
    lens_lisa_su,         # 成长/质量（竞争）
    lens_satya_nadella,   # AI暴露
    lens_howard_marks,    # 质量/风险
    lens_mary_meeker,     # 预期差
    lens_peter_thiel,     # 元判断
    lens_charlie_munger,  # 元判断
]


def all_investor_lenses(ticker: str, data: dict, category, scores: dict) -> list[dict]:
    """返回 8 个投资人视角的分析结果（每个是一个 dict）。任何单个视角出错都跳过，
    不影响其余视角和主页面。"""
    out = []
    for fn in _LENS_ORDER:
        try:
            out.append(fn(ticker, data, category, scores))
        except Exception as e:  # noqa: BLE001
            out.append({
                "name": fn.__name__, "icon": "", "framework": "", "dimension": "",
                "verdict": "计算失败", "verdict_color": _AMBER,
                "paragraph": f"该视角计算出错：{e}",
            })
    return out
