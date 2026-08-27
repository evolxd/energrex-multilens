# 归档说明

2026-08-27 归档。这是一套完整搭好但从未真正启用的 FastAPI + Postgres 后端
（`backend/`、`docker-compose.yml`、`Dockerfile.api`、`init.sql`）。

**为什么归档**：
- 从 2026-06-20 初始提交到归档这天，这几个文件没有被真正改动过——同一时间段
  里 scoring/、pages/、mispricing 引擎全都在持续演进，backend/ 是唯一两个多月
  零变化的角落。
- 它要解决的问题（并发写入安全、可 SQL 查询的历史数据）现在的 JSON/CSV 方案
  已经稳定跑了两个多月，没出过问题——这是单人用的本地仪表盘，不存在"多进程
  同时写"的真实场景。
- `render.yaml` 的生产部署直接跑 `home.py`，本来就没打算用这套后端。
- 启用它是新增运维负担（本地常驻 Docker，或花钱找托管 Postgres），不是减少。

**对现有系统的影响**：`scoring/db_client.py` 原本就是"先试连 Postgres，连不上
自动退回本地 JSON"的设计——挪走 backend/ 之后，那次 `import` 直接变成
`ImportError`，走的是同一条已经存在的 fallback 分支，行为不变。挪之前用
`python -m scoring.db_client --status` 和全量测试都验证过。

**什么时候可能用得上**：如果以后要把部分功能开放给别人看（比如按权限分层分享
给投资群朋友），那时候需要的是一整套全新的用户/角色/权限模型——这里的
`backend/models.py` 现在只有 UserOverride / MarketDataCache / ScoreSnapshot
三张表，没有任何用户权限相关的东西，到时候基本是从零设计，不是把这里的代码
接回去就能用。留着这个空壳子并不会替那件事省下多少工作，真正能重用的是
"FastAPI + SQLAlchemy + Postgres 这个组合在这个仓库里跑得通"这条经验，
从 git 历史里翻出来一样能确认。
