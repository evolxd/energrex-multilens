import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import json
from meal_data.meals import MEALS_30_DAYS, NUTRITION_GUIDELINES, WEEKLY_SHOPPING_LIST

# 配置页面
st.set_page_config(
    page_title="30天家庭菜谱计划",
    page_icon="🍳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义样式
st.markdown("""
<style>
    .meal-card {
        border-radius: 10px;
        padding: 20px;
        margin: 10px 0;
        background-color: #f0f8ff;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .key-point {
        padding: 8px;
        margin: 5px 0;
        background-color: #e8f5e9;
        border-left: 4px solid #4caf50;
        border-radius: 4px;
    }
    .nutrition-stat {
        text-align: center;
        padding: 10px;
    }
    .carbs { color: #ff9800; }
    .protein { color: #2196f3; }
    .fat { color: #f44336; }
</style>
""", unsafe_allow_html=True)

# 侧边栏导航
st.sidebar.title("🍳 30天家庭菜谱")
page = st.sidebar.radio(
    "选择页面",
    ["📅 菜谱日历", "🍽️ 单日详情", "📊 营养分析", "🛒 购物清单", "💡 烹饪贴士"]
)

# 日期计算
start_date = datetime(2026, 7, 26)

def get_day_info(day_num):
    """获取指定第几天的信息"""
    date = start_date + timedelta(days=day_num-1)
    week_num = (day_num - 1) // 7 + 1
    week_key = f"week{week_num}"
    day_in_week = (day_num - 1) % 7
    return date, week_key, day_in_week

# 页面1：菜谱日历
if page == "📅 菜谱日历":
    st.title("📅 30天菜谱日历")
    st.markdown("**按周浏览你的全月家庭菜谱计划**")

    week_select = st.selectbox(
        "选择周数",
        ["第1周 (7月26-8月1)", "第2周 (8月2-8)", "第3周 (8月9-15)",
         "第4周 (8月16-22)", "第5周 (8月23-24)"]
    )

    week_num = int(week_select[1])
    week_key = f"week{week_num}"
    week_meals = MEALS_30_DAYS.get(week_key, [])

    cols = st.columns(7)
    for idx, meal_day in enumerate(week_meals):
        day_date = meal_day["date"]
        with cols[idx % 7]:
            st.markdown(f"""
            <div class="meal-card">
                <h4>第{meal_day['day']}天</h4>
                <p style="color: #666; font-size: 12px;">{day_date}</p>
                <p><b>早餐:</b> {meal_day['meals']['breakfast']['name']}</p>
                <p><b>午餐:</b> {meal_day['meals']['lunch']['name']}</p>
                <p><b>晚餐:</b> {meal_day['meals']['dinner']['name']}</p>
            </div>
            """, unsafe_allow_html=True)

# 页面2：单日详情
elif page == "🍽️ 单日详情":
    st.title("🍽️ 单日详细菜谱")

    day_select = st.slider("选择第几天", 1, 30, 1)
    date, week_key, day_in_week = get_day_info(day_select)

    week_meals = MEALS_30_DAYS.get(week_key, [])
    meal_day = week_meals[day_in_week] if day_in_week < len(week_meals) else None

    if meal_day:
        st.subheader(f"📆 第{day_select}天 ({meal_day['date']})")

        # 三餐显示
        meal_types = ["breakfast", "lunch", "dinner"]
        meal_titles = ["🌅 早餐", "☀️ 午餐", "🌙 晚餐"]

        for meal_type, meal_title in zip(meal_types, meal_titles):
            meal_info = meal_day["meals"][meal_type]

            st.markdown(f"### {meal_title}")
            st.markdown(f"**菜名**: {meal_info['name']}")

            # 营养信息
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"""
                <div class="nutrition-stat carbs">
                    <b>碳水</b><br>{meal_info['carbs']}g
                </div>
                """, unsafe_allow_html=True)
            with col2:
                st.markdown(f"""
                <div class="nutrition-stat protein">
                    <b>蛋白质</b><br>{meal_info['protein']}g
                </div>
                """, unsafe_allow_html=True)
            with col3:
                st.markdown(f"""
                <div class="nutrition-stat fat">
                    <b>脂肪</b><br>{meal_info['fat']}g
                </div>
                """, unsafe_allow_html=True)

            # 烹饪关键点
            st.markdown("**🔑 烹饪关键点:**")
            for point in meal_info['key_points']:
                st.markdown(f"""
                <div class="key-point">
                    {point}
                </div>
                """, unsafe_allow_html=True)

            st.markdown("---")

        # 日总结
        total_carbs = sum(meal_day["meals"][meal]["carbs"] for meal in meal_types)
        total_protein = sum(meal_day["meals"][meal]["protein"] for meal in meal_types)
        total_fat = sum(meal_day["meals"][meal]["fat"] for meal in meal_types)

        st.markdown("### 📊 全天营养总计")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("碳水", f"{total_carbs}g", "⬇️ 低碳")
        with col2:
            st.metric("蛋白质", f"{total_protein}g", "⬆️ 高蛋白")
        with col3:
            st.metric("脂肪", f"{total_fat}g", "✓ 适量")
        with col4:
            total_calories = int(total_carbs * 4 + total_protein * 4 + total_fat * 9)
            st.metric("热量", f"{total_calories}cal", "✓ 均衡")

# 页面3：营养分析
elif page == "📊 营养分析":
    st.title("📊 营养分析与目标")

    st.markdown("### 📈 每日营养目标")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("碳水比例", NUTRITION_GUIDELINES["daily_targets"]["carbs_percentage"], "35-40%")
    with col2:
        st.metric("蛋白质比例", NUTRITION_GUIDELINES["daily_targets"]["protein_percentage"], "30-35%")
    with col3:
        st.metric("脂肪比例", NUTRITION_GUIDELINES["daily_targets"]["fat_percentage"], "25-30%")
    with col4:
        st.metric("每日热量", NUTRITION_GUIDELINES["daily_targets"]["calories_per_day"], "2000-2200")

    st.markdown("### 💡 低碳水饮食策略")
    for idx, strategy in enumerate(NUTRITION_GUIDELINES["low_carb_strategy"], 1):
        st.write(f"{idx}. {strategy}")

    st.markdown("### 🍳 烹饪小贴士")
    tips = NUTRITION_GUIDELINES["cooking_tips"]
    for category, tip in tips.items():
        st.write(f"**{category}**: {tip}")

    # 全月统计
    st.markdown("### 📊 全月营养统计")

    all_carbs = []
    all_protein = []
    all_fat = []

    for week_key in ["week1", "week2", "week3", "week4", "week5"]:
        for day in MEALS_30_DAYS.get(week_key, []):
            for meal_type in ["breakfast", "lunch", "dinner"]:
                meal = day["meals"][meal_type]
                all_carbs.append(meal["carbs"])
                all_protein.append(meal["protein"])
                all_fat.append(meal["fat"])

    df_nutrition = pd.DataFrame({
        "营养素": ["碳水", "蛋白质", "脂肪"],
        "平均每餐(g)": [
            f"{sum(all_carbs)/len(all_carbs):.1f}",
            f"{sum(all_protein)/len(all_protein):.1f}",
            f"{sum(all_fat)/len(all_fat):.1f}"
        ],
        "全月总计(g)": [
            f"{sum(all_carbs):.0f}",
            f"{sum(all_protein):.0f}",
            f"{sum(all_fat):.0f}"
        ]
    })

    st.dataframe(df_nutrition, use_container_width=True)

# 页面4：购物清单
elif page == "🛒 购物清单":
    st.title("🛒 购物清单")
    st.markdown("**每周建议购物清单**")

    tab1, tab2, tab3, tab4 = st.tabs(["蛋白质", "蔬菜", "调味料", "主食"])

    with tab1:
        st.markdown("### 🥩 蛋白质食材")
        for item in WEEKLY_SHOPPING_LIST["proteins"]:
            st.write(f"✓ {item}")

    with tab2:
        st.markdown("### 🥬 蔬菜")
        for item in WEEKLY_SHOPPING_LIST["vegetables"]:
            st.write(f"✓ {item}")

    with tab3:
        st.markdown("### 🧂 调味料")
        for item in WEEKLY_SHOPPING_LIST["seasonings"]:
            st.write(f"✓ {item}")

    with tab4:
        st.markdown("### 🍚 主食")
        for item in WEEKLY_SHOPPING_LIST["staples"]:
            st.write(f"✓ {item}")

    st.markdown("---")
    st.info("""
    💡 **购物建议**:
    - 每周买新鲜蔬菜和肉类
    - 腊肠、腊肉可买好保存
    - 豆腐现买现用（最多3天）
    - 鸡蛋买散装，每周30个
    - 调味料按季节补充
    """)

# 页面5：烹饪贴士
elif page == "💡 烹饪贴士":
    st.title("💡 烹饪贴士与常见问题")

    with st.expander("❄️ 低碳水怎么做到？", expanded=True):
        st.write("""
        1. **米饭减量**: 从原来的一碗减到半碗或三分之一碗
        2. **汤底选择**: 用清水+盐+油替代油腻汤底，保留鲜味
        3. **增加蛋白质**: 每餐至少1-2种蛋白质食物（鸡、鱼、豆腐、鸡蛋）
        4. **增加纤维**: 青菜、冬瓜、豆芽都是低碳水高纤维的好选择
        5. **烹饪方法**: 减少油炸，多用清蒸、白水煮、炖，保留营养
        """)

    with st.expander("🧂 为什么这么多腊肠腊肉？", expanded=False):
        st.write("""
        - **风味层次**: 腊肠腊肉自带咸香味，无需额外加油和盐
        - **油脂来源**: 腊肉的油脂融入菜肴，自然调味，热量有数据追踪
        - **方便快手**: 切了就能用，不需长时间烹饪
        - **适合4口之家**: 一点腊肠就能香满一碗饭
        - **平衡考虑**: 搭配清汤和青菜，营养均衡
        """)

    with st.expander("👨‍👩‍👧‍👦 怎么让小孩也吃这些菜？", expanded=False):
        st.write("""
        - **番茄系列**: 番茄鸡蛋、番茄汤，酸甜开胃，小孩易接受
        - **清汤面**: 清汤面条软滑易消化，加个荷包蛋或清蒸鱼
        - **高汤和炖菜**: 鸡汤、鱼汤、老鸭汤都是补气血好食物
        - **避免过辣**: 虽然四川江西菜有辣，但菜谱设计时已考虑温和程度
        - **提早引入**: 小孩从清汤版开始，慢慢适应咸香味
        """)

    with st.expander("🔥 油温怎么控制？", expanded=False):
        st.write("""
        - **150°**: 炸豆腐块、炸鸡块、炸鱼块，至表面微脆即出
        - **160°**: 炸腊肠、腊肉、鸡翅，至金黄即出
        - **170°+**: 快速定型用，不建议
        - **判断方法**: 放一根竹筷子，周围立即冒小气泡 = 150-160°
        - **关键**: 油温太高易焦，太低易吸油，影响口感和热量
        """)

    with st.expander("⏱️ 时间规划 - 如何高效做饭？", expanded=False):
        st.write("""
        **平时工作日（15-30分钟）：**
        - 快手炒菜：鱼香肉丝、麻婆豆腐、回锅肉、宫保鸡丁
        - 汤面：番茄面、清汤面、馄饨面，15分钟搞定
        - 一锅焖饭：鸡肉饭、腊肠饭，20分钟

        **周末有时间（1-2小时）：**
        - 炖汤：老鸭汤、鸡汤、牛肉番茄面，可一次做多天
        - 红烧类：红烧猪蹄、红烧鱼块，周末做好，剩菜加热吃
        - 干锅：干锅鸡，上桌保温，全家享受
        """)

    with st.expander("🧑‍🍳 关键烹饪技巧速查", expanded=False):
        st.write("""
        **豆腐**: 用盐水浸15分钟，防止煮时破碎
        **鱼片**: 用淡盐水腌10分钟，去腥；煮时不超2分钟，保持嫩滑
        **牛肉**: 切片腌10分钟，炒时全程大火，保持嫩度
        **鸡蛋**: 半生蛋6-8分钟，全熟12-15分钟
        **面条**: 单独煮至八分熟沥干，最后倒汤，面更爽口
        **汤底**: 清水+盐+油最清，鸡汤/高汤最鲜，豆汤最香
        **红油**: 干辣椒、花椒、油、盐，小火熬5分钟出香气
        **豆瓣酱**: 油热后加豆瓣酱炒红油（1-2分钟），是咸香的灵魂
        """)

st.sidebar.markdown("---")
st.sidebar.markdown("""
### 💬 使用提示
- 每周更新购物清单
- 根据冰箱存货灵活调整
- 记录全家人的喜好
- 定期称体重，跟踪效果
- 发现好菜记录下来！
""")

st.sidebar.markdown("""
---
**v1.0** | 2026年7月
为一家四口设计的低碳水菜谱系统
""")
