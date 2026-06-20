# ENERGREX 项目状态 2026-06-13

## 启动命令
cd C:\Users\evolx\ai_valuation
streamlit run home.py

## 关键文件
- home.py 主页入口
- app.py AI估值评分（84只股票）
- options_module.py 期权分析
- account_monitor.py 账户监控
- scoring/quant_engine.py 评分引擎
- data/energrex.db 数据库

## 数据库状态
- options_positions: 20条期权持仓（数据有误，需重建）
- positions: 5条股票持仓
- transactions: 127条交易记录
- option_realized_trades: 34条已实现交易
- account_balance: 1条余额记录

## 最大未解决问题
options_positions 表数据是从CSV推算的，不准确。
需要用以下真实数据完全替换（来自Firstrade截图核对）：
CRWV 09/18/2026 77.50 Put qty=-1 cost=7.7196
CRWV 09/18/2026 97.50 Put qty=1 cost=16.7202
FCX 09/18/2026 85.00 Call qty=-2 cost=3.00
FCX 01/21/2028 70.00 Call qty=5 cost=15.20022
META 01/15/2027 600.00 Call qty=1 cost=70.3302
META 01/15/2027 800.00 Call qty=-1 cost=20.3293
NOK 12/18/2026 17.00 Call qty=10 cost=4.60023
NVDA 01/15/2027 210.00 Call qty=1 cost=30.0602
PANW 01/15/2027 270.00 Call qty=1 cost=44.8502
PANW 01/15/2027 350.00 Call qty=-1 cost=20.8493
PLTR 01/15/2027 150.00 Call qty=1 cost=20.2002
PLTR 01/15/2027 160.00 Call qty=2 cost=22.0252
QQQ 08/31/2026 595.00 Put qty=-1 cost=7.3896
QQQ 08/31/2026 700.00 Put qty=1 cost=30.6902

## 下一步P0任务
1. 用上面真实数据重建 options_positions 表
2. 组合识别用真实数据重新运行
3. 交易绩效Tab显示34笔已实现交易统计
