# ENERGREX 正式估值证据标准 V1.0

正式估值不再接受“字段有数值、行尾有几个来源链接”作为通过条件。每个估值字段必须形成独立、可追溯、可复核的证据记录。

## 强制规则

1. 每个字段必须记录 `observed_at`、`available_at`、`retrieved_at`，三者不得互相替代。
2. `available_at` 或 `retrieved_at` 晚于估值截止时间时，视为前视数据并拒绝。
3. 两个包装器如果最终都来自 Yahoo，只算一个 `source_family`，不得形成交叉验证。
4. 两个来源必须来自不同 `source_family`，且 `source_locator` 不同。
5. 两个数值必须在字段专用容差内；超出容差时标记 `CONFLICTED`，不得取均值。
6. 缺失字段标记 `NEEDS_EVIDENCE`，不得用行业均值、零值或旧缓存补齐。
7. 只有全部必需字段为 `VERIFIED` 时，流水线才会生成 `evidence_mode=LIVE` 的统一估值请求。
8. 旧版 `results_validated.csv` 的来源链接是行级参考，不能证明字段级双源核验，因此默认保留为研究数据并降级复核。

## 状态

- `VERIFIED`：双独立来源、时间有效、单位一致、数值在容差内。
- `NEEDS_EVIDENCE`：只有一个独立来源，或缺少第二来源。
- `CONFLICTED`：存在独立来源，但数值超出容差。
- `INVALID`：时间、单位、字段或元数据无效。

## 当前职责边界

- 抓取器负责收集原始观察，不拥有正式估值字段。
- 证据流水线负责核验和提升字段。
- 统一估值服务只接收通过证据流水线的 LIVE 字段。
- Mispricing Engine 负责发现误价，不拥有正式目标价。
- IDI 与组合模块消费估值结果，不得绕过证据门。

## 已接入的真实来源

当前第一阶段只自动提升 `current_price`：

- Yahoo Finance：使用带交易日索引的下载数据，不允许静默退回无时间戳的 `previous_close`。
- Polygon：保留日线聚合记录的市场时间戳与具体接口定位。
- MarketData.app：仅当响应自身提供更新时间时才生成证据；缺少更新时间时直接拒绝。

`scoring/valuation_source_adapters.py` 至少需要两个不同
`source_family` 的价格观察，并要求差异不超过 1%。单一 API
不可用时不会自动用旧缓存、前收盘价或其他默认值补齐。

财务报表、稀释股数、净现金和一致预期尚未自动提升。它们仍需后续
SEC XBRL、公司正式文件及独立一致预期适配器，不能因为价格证据已通过
就把整个估值案例标记为 LIVE。

## 来源血缘与交叉验证模式

每条观察同时记录：

- `source_family`：数据通过哪个供应商或文档通道进入；
- `origin_family`：经济事实最初由谁产生；
- `lineage_id`：具体交易快照、申报或报告的血缘标识。

系统区分两种交叉验证：

- `INDEPENDENT_ORIGIN`：用于价格和一致预期。两个观察必须来自不同事实源。
- `INDEPENDENT_EXTRACTION`：用于公司财务事实。SEC XBRL与公司财报可以核对提取结果，但必须使用不同提取方法；系统明确承认它们仍源于同一家公司报告。

因此，Polygon与FMP即使都展示同一份SEC财报，也不能被描述成两个独立
经济事实。它们最多只能作为独立提取或转录校验。

## SEC XBRL基本面阶段

`scoring/sec_fundamental_evidence.py` 当前严格提取：

- `revenue_ttm`：只累计四个60–125天的离散季度；不把六个月或九个月累计值当成季度；
- `diluted_shares`：最近离散季度的加权平均稀释股数；
- `net_cash`：只有公司明确披露直接 `NetDebt` XBRL事实时才反号转换。

若缺少离散第四季度、精确SEC受理时间或直接净债务事实，字段保持
`PARTIAL/NEEDS_EVIDENCE`。系统不会跨不同债务分类、不同报告期或不同
会计口径拼接一个看似完整的净现金值。
