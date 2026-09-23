# 重构作战流水线（Refactoring Workflow SOP）

> 长期遵照的标准作业流程。把 `arch`（宏观：分层边界、跨模块契约、拓扑图谱）和
> `cleancode-skills`（微观：单函数纪律、黄金快照、Git 检查点熔断）串成一条可重复执行的
> 四阶段流水线。任何一次跨模块抽取或核心引擎剥离，都按这四个阶段走，阶段之间的
> 【验收门槛】没过，不进入下一阶段。
>
> 进度实时写入项目根目录的 `.refactor_status.json`（结构见该文件本身），供
> `war_room.py` 未来读取展示。每个阶段开始/结束时更新 `current_phase` 与 `logs`。

## 已产出的架构诊断资产索引

按 `arch` 第四节规范，细节拓扑图只存放在各自的专属文档，此处只留索引：

| 模块 | 诊断文档 | 状态 |
|---|---|---|
| `account_monitor.py` | [docs/architecture/account_monitor.md](architecture/account_monitor.md) | 阶段一已完成，待启动阶段二 |

---

## 阶段一：架构侦察与契约设计

**对应武器**：`/arch`（主导）+ `/cleancode-skills` 的诊断脚本（辅助定位）

### 前置条件
- 目标模块已确定（不是"顺手重构看到的所有问题"，只挑本轮要动的 1 个模块/边界）
- 已按 `cleancode-skills` 模块一的规则，读完项目里的规则文件（`RULES.md`/`AGENTS.md`/
  `CLAUDE.md`/`docs/` 下的架构文档），确认以哪份为真理源、屏蔽哪些过期描述
- 工作区无该模块的未提交改动（`git status --porcelain -- <目标文件>` 为空）

### 核心动作
1. 用 `cleancode-skills/scripts/scan_long_functions.py` 定位目标范围内超标最严重的
   1~2 个函数/文件，作为本轮切入点
2. 对照 `arch` 第二节的三层拓扑标准，画出**现状图**：目标模块里哪些代码属于
   内核（纯计算）、哪些是中间层（编排/适配）、哪些是外围（UI/CLI/DB/网络），
   标出违反红线的具体行号（`st.*` 混入内核区、直接 IO、字典键反射调用点等）
3. 用 `cleancode-skills/scripts/export_sanity.py --json <baseline>.json` 建立
   对外契约名单——全项目扫描，包括 `mock.patch` 字符串目标、动态 `getattr`、
   配置文件里的引用
4. 对每一个要跨模块暴露的调用点，按 `arch` 第三节起草 `typing.Protocol` 或 ABC
   接口草案：显式声明输入字段、输出类型、异常类型
5. 把"现状图"和"目标图"（重构后应该长成的三层结构）画成 Mermaid，按 `arch`
   第四节存入 `docs/architecture/<MODULE_NAME>.md`；主文档（如
   `REFACTORING_WORKFLOW.md`、`ARCHITECTURE.md` 等）只留一句话索引链接，
   不堆细节图

### 验收门槛（Gate 1）
- [ ] 对外契约名单已生成并保存（`<module>-baseline.json`）
- [ ] 现状 Mermaid 图 + 目标 Mermaid 图已落盘到 `docs/architecture/<MODULE_NAME>.md`
- [ ] 每个计划新增的跨模块调用点，都有对应的 `Protocol`/ABC 接口草案
- [ ] 已按 `cleancode-skills` Step 1–3 的格式列出诊断表 + 拆分清单，并取得用户确认
- [ ] `.refactor_status.json`：`current_phase` 置为 `"1_recon"`，追加一条 log

---

## 阶段二：建立安全防线

**对应武器**：`/cleancode-skills` 模块三（测试先行与确定性黄金快照）

### 前置条件
- 阶段一 Gate 1 全部通过
- 已在独立分支上（`refactor/<module>-<target>`），不在 `master`/`main` 上直接改
- 已确认基线全量测试在原始代码上全绿（红色基线不重构）

### 核心动作
1. 按 `references/characterization-test-template.md` 编写黄金快照测试，覆盖
   目标函数每个分支、边界输入（空/单元素/极值/非法值）、异常路径
2. 确定性打桩：`freezegun` 冻结时间、固定 `random`/`uuid` 序列、Mock 外部
   HTTP/LLM/数据库；打桩位置必须是"名字被查找的模块"，不能在
   `from x import dep` 与 `x.dep` 之间乱换
3. 稳定性门槛：新测试在原始代码上**连续跑 3 次 + 乱序**，全部一致通过才算数
4. 反向验证：临时改坏实现里的一个常量/分支，确认对应快照**必须变红**，
   证明测试不是"假绿"；验证完立即还原，`git diff` 归零
5. 分支覆盖率检查（`--cov-branch`），未覆盖分支逐条定性：结构性走不到 vs
   缺场景，缺场景的补测试
6. 特征测试全绿后，单独提交作为 **Git 检查点**，记录 `CHECKPOINT=$(git rev-parse HEAD)`

### 验收门槛（Gate 2）
- [ ] 黄金快照测试在原始代码上 100% 通过，且连续 3 次 + 乱序稳定
- [ ] 反向验证已执行，证明测试能真正捕获行为回归
- [ ] 分支覆盖率报告已生成，未覆盖分支已逐条写明原因
- [ ] Git 检查点已提交，`CHECKPOINT` sha 已记录并写入 `.refactor_status.json`
- [ ] `.refactor_status.json`：`current_phase` 置为 `"2_safety_net"`，
      `progress.tests_total` 更新为当前全量用例数

---

## 阶段三：沙盒实施与核心重构

**对应武器**：`/cleancode-skills`（执行纪律）+ `/arch`（分层红线，双重约束）

### 前置条件
- 阶段二 Gate 2 全部通过，检查点已就位（回滚有退路）
- 拆分清单已获用户确认，且未变更范围

### 核心动作
1. 按清单**逐个**抽离辅助函数：单函数有效行数 < 40 行；每抽完一个立即跑一次
   相关测试，变红就撤销这一步、计入自愈次数（≤2 次自愈仍不过 → 熔断回滚）
2. 纯函数改造：抽出的辅助函数不读写全局状态、不做隐式 IO；原函数保留为
   编排外壳，签名不变
3. 隔离外围框架：新代码不得 `import streamlit`/直接发 DB 请求——如果目标模块
   本身就混着 UI 框架（如 `account_monitor.py` 顶部的 `import streamlit as st`），
   本阶段的目标就是把纯计算逻辑物理搬到一个**不引入 UI 框架**的新模块里，
   原模块改为对新模块做正常 `import`
3.（续）跨模块访问一律改为显式 `import` + 阶段一定义的 `Protocol`/类型签名，
   废除字典键反射（`_get_am()["fn"]`、`ns["fn"]` 这类写法）和依赖文件行号的
   AST 切片加载
4. 每一步都确认 CRLF 完整性：不对已有 CRLF 文件使用 `sed -i` 等会静默改写
   换行符的工具，只用 Edit 工具或二进制读写；改完检查裸 LF 数为 0
5. diff 范围自查：只触及清单内文件，不顺手改动无关代码

### 验收门槛（Gate 3）
- [ ] 所有新增/改造后的函数有效行数 < 40 行
- [ ] 目标范围内已无 `st.*`/直接 DB 调用混入核心计算逻辑
- [ ] 目标范围内已无字典键反射调用残留（旧调用点已改为显式 import）
- [ ] 每一步 diff 经自查，未超出清单范围；CRLF 裸 LF 数为 0
- [ ] `.refactor_status.json`：`current_phase` 置为 `"3_sandbox_refactor"`，
      每完成一个函数抽离追加一条 log

---

## 阶段四：验证与干净合入

**对应武器**：`/cleancode-skills` 模块四（Git 检查点与回滚熔断）

### 前置条件
- 阶段三 Gate 3 全部通过，所有计划内函数已完成迁移/抽离

### 核心动作
1. 全量测试回归：`PYTHONUTF8=1 python -m pytest tests -q -p no:cacheprovider`，
   核对通过数、失败数、跳过数与基线一致（跳过数增加也视为不通过）
2. `export_sanity.py --compare <baseline>.json`：确认对外契约名单一个不丢，
   新增辅助函数全部私有
3. CRLF 完整性最终检查（目标模块 + 所有改动过的测试文件）
4. 按功能边界拆分提交（不要一个巨大提交糊在一起），提交信息写明
   "行为由 <测试文件> 锁定"
5. 合入 `master` 前，先检查 `master` 是否已独立前进（`git log master..HEAD`
   / `git log HEAD..master`）；工作区若有未提交的无关文件，用
   `git stash push -u -m "<唯一标签>"` 保护（记录 SHA，禁止裸 `stash pop`），
   合并完成后按 SHA 精确恢复
6. 合入后在 `master` 上**重新跑一次全量测试**，确认依然全绿（若 `master` 自己
   也有独立新提交带来新测试，用例总数增加属正常，需核实差额来源，不能
   只看数字对不对就下结论）

### 验收门槛（Gate 4）
- [ ] 全量测试 100% 通过、0 失败（用例总数变化已核实来源）
- [ ] `export_sanity --compare` 通过
- [ ] CRLF 检查通过
- [ ] 合并到 `master` 后 `git status` 干净，无冲突标记残留
- [ ] **未 push**（除非用户明确指示），停在 `master` 汇报结果
- [ ] `.refactor_status.json`：`current_phase` 置回 `null`，`status` 置回
      `"IDLE"`，`progress` 更新为最终通过数，追加收尾 log

---

## 熔断规则（贯穿全部四个阶段）

- 需要修改测试断言/快照才能通过 → 立即熔断，0 次重试
- 需要改变对外签名/契约 → 立即熔断
- 同一阶段内累计 2 次自愈尝试仍未通过 → 熔断，回滚到最近的 Git 检查点，
  向用户报告阻塞原因，不擅自继续
- 熔断后 `.refactor_status.json` 的 `status` 置为 `"IDLE"`，`logs` 追加
  熔断原因，等待用户下一步指示

## 与 `.refactor_status.json` 的对应关系

| SOP 阶段 | `current_phase` 取值 |
|---|---|
| 阶段一 | `"1_recon"` |
| 阶段二 | `"2_safety_net"` |
| 阶段三 | `"3_sandbox_refactor"` |
| 阶段四 | `"4_verify_merge"` |
| 未在进行中 | `null`（同时 `status: "IDLE"`） |
