---
name: cleancode-skills
description: >
  架构治理与代码安全重构流程：防止项目后期代码腐化与烂尾，在不破坏现有业务的前提下，
  对复杂/超长函数做增量重构与安全隔离。五道防线：单点真理源检查 → 增量不追溯 + 导出兼容检查 →
  确定性黄金快照测试 → Git 检查点与原子化回滚熔断 → 诊断-裁决-清单-执行回归 SOP。
  每轮只动 1~2 个目标函数，拆分清单必须经用户确认，测试不全绿绝不提交。
  触发场景：用户说「重构这个函数」「这个文件太长了」「拆一下这个大函数」「代码腐化了」
  「项目快烂尾了」「做一次架构治理」「安全重构」「函数超过 N 行」「抽私有辅助函数」
  「refactor safely」「characterization test / 黄金快照测试」。
  不在以下情况触发：新功能开发、纯 bug 修复、用户明确要求整体重写 / 大改架构。
---

# 架构治理与代码安全重构（Project Refactor Governance）

> **行为不变是底线，范围最小是纪律，测试全绿是许可证，Git 检查点是退路。**

本流程的目标不是"把代码变漂亮"，而是**在输出 100% 不变的前提下，把最危险的一两处复杂度隔离出来**；
做不到就完整退回，绝不留下半成品。

## 五条铁律（违反任何一条 → 立即停下并向用户说明）

1. 没有读完规则文件，不下结论。
2. 没被选中的旧代码一行不改；对外契约名单中的名字一个不丢。
3. 没有在原始代码上**确定性**全绿的测试，不动实现。
4. 没有用户确认的拆分清单，不写实现；没有 Git 检查点，不开始重构。
5. 回归不全绿就不提交：2 次自愈失败即熔断回滚，严禁带病提交。

## 附带资源

| 文件 | 用途 | 使用时机 |
|---|---|---|
| `scripts/scan_long_functions.py` | 按有效行数 / 分支数列出超标函数（Python） | Step 1 |
| `scripts/export_sanity.py` | 全项目扫描对外契约名单；`--json` 存基线，`--compare` 做重构后硬校验 | Step 0 / Step 2 / Step 4 |
| `references/characterization-test-template.md` | 确定性黄金快照测试模板与打桩清单（pytest / Vitest） | Step 4a |

脚本路径相对本 Skill 目录（`~/.claude/skills/project-refactor-governance/`），只读，不修改项目文件。
非 Python 项目：用对应生态工具替代（ESLint `max-lines-per-function` / `complexity`、`ts-prune`、`gocyclo` 等），流程不变。

---

## 模块一：单点真理源与文档优先级（Single Source of Truth）

### 1.1 查找规则文件

以下位置找到的都要读，不能只读第一个：

- `RULES.md`、`AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`，以及子目录中的同名文件（对该子目录生效）
- `docs/` 下的架构 / 运维 / ADR 文档
- Code Style 配置：`.editorconfig`、`pyproject.toml`（ruff/black/mypy 段）、`.eslintrc*`、`.prettierrc*`、`setup.cfg`、`.golangci.yml`
- 测试与 CI：`pytest.ini`、`conftest.py`、`jest/vitest.config.*`、`.github/workflows/*`。**全量测试命令以 CI 实际执行的为准**，并记下 lint / 类型检查命令

### 1.2 工程规范与业务标准隔离

读到的约束分成两类，**分别列出，不得混用**：

| 类别 | 例子 | 本流程对它的态度 |
|---|---|---|
| **代码工程规范** | 函数行数上限、模块解耦、命名、私有函数约定、目录分层 | 重构的**目标** |
| **业务交付标准** | 算法口径、计算公式、数值精度、API 契约、返回字段、错误码、日志 / 埋点格式 | 重构的**禁区**，只能由测试锁定 |

归类不清时默认归为"业务交付标准"（更保守的一侧），并在 Step 2 说明。

### 1.3 冲突与版本裁决

1. 用户**本次对话中显式指定**的文件优先级最高。
2. 其次是**最新**的运维 / 架构文档。"最新"以 `git log -1 --format=%cI -- <file>` 为准，不信文件内自称的日期。
3. 被新文档覆盖的旧背景说明视为**过期**，不作为决策依据。
4. 在 Step 2 写明："以 X 为真理源，屏蔽了 Y 中关于 Z 的过期描述"，让用户能纠正。

项目中没有任何规则文件时，使用 2.1 的默认规范，并告知用户。

---

## 模块二：增量生效、不追溯与导出兼容（Forward-Looking Scope & Export Sanity）

### 2.1 默认工程规范（项目规则未另行规定时）

- 单函数有效行数 ≤ 40（不含 docstring、注释、空行；与 `scan_long_functions.py` 口径一致）
- 单一职责：每个辅助函数的职责能用一句话说清
- 辅助函数**私有**，放在**同一文件**、紧邻调用者：Python 用 `_` 前缀；JS/TS 不导出；Go 用小写开头
- 优先纯函数：不读写全局状态、不做 I/O；I/O 与副作用留在原函数（编排外壳）中

项目规则中的阈值**覆盖**以上默认值。

### 2.2 增量约束与严禁追溯

规则只对两类代码生效：本次新写 / 修改的代码，以及用户在 Step 3 确认过的目标函数。

- 不"顺手"重构同文件里其他超标函数，列入报告的"后续候选"即可。
- 不改格式、不批量改名、不调整 import 顺序、不升级依赖、不改类型标注风格，除非它就在目标函数内部且拆分必需。
- 发现旧 bug **不修**，单独报告。修 bug 会改变行为，与"输出不变"的前提冲突，应作为独立任务处理。
- diff 只能触及：目标函数、新增私有辅助函数、新增测试 / 快照文件。

### 2.3 导出兼容检查（Export Sanity）—— 重构前必做

动手前扫描**全项目**：业务代码、`main.py` / 应用入口、`routers/`（路由注册）、`tests/`（包括 `mock.patch` 字符串目标）、任务调度 / 配置文件。建立**对外契约名单**：

```bash
python <skill>/scripts/export_sanity.py path/to/target.py --root . --json .refactor-baseline.json
```

脚本能识别：`from x import name`、`import x` / `import x as y` 后的属性访问、相对导入、`__init__.py` re-export、`__all__`、`import *`、`getattr/hasattr/setattr` 动态访问、字符串引用（`mock.patch("pkg.mod.fn")`、`"pkg.mod:app"`、Celery 任务名）、配置文件（toml/yaml/ini/json/Procfile/Dockerfile）中的引用。
静态分析覆盖不到的情况要**再手动 Grep 一遍**：字符串拼接出的模块名、`importlib.import_module(变量)`、插件注册表、序列化（pickle 存储的类路径）。
`.refactor-baseline.json` 属于临时文件，不要提交（必要时加进 `.git/info/exclude`）。

**兼容规则：**

- 契约名单中的每个名字，重构后必须**同名、同签名**留在原模块：参数顺序、默认值、关键字参数、返回结构、异常类型、副作用顺序与次数都不变。
- 本流程原则上**不移动、不改名**公开函数。确需变动时，在原模块保留兼容别名 `old_name = new_name`，并保持 re-export 链（`__init__.py`、`__all__`、barrel `index.ts`）原样。
- **不改变名字的查找位置。** 测试里的 `mock.patch("pkg.mod.dep")` 依赖 `dep` 在 `pkg.mod` 命名空间中被查找。辅助函数必须留在同一模块，继续通过模块级名字调用 `dep`；不要把 `from x import dep` 改成 `import x; x.dep`，反过来也不行，否则打桩会静默失效，测试"假绿"。
- 新增辅助函数一律私有。模块没有 `__all__` 时，`import *` 会导出所有非 `_` 名字，新增公开名会改变调用方的命名空间。
- 装饰器（`@router.get`、`@lru_cache`、`@app.task`、`@retry`）留在原函数上，不挪到辅助函数上。

---

## 模块三：测试先行与确定性黄金快照（Test-Driven Safety Net & Deterministic Mocking）

### 3.1 基线

- 在未修改的代码上运行**全量**测试，记录通过 / 失败 / 跳过数。
- 基线本身有失败 → **停下报告**，不在红色基线上重构。

### 3.2 特征测试（Characterization / Golden Test）

特征测试记录代码**现在**做了什么，不判断对错。当前输出看起来像 bug，也照原样锁定。

- **覆盖**：以目标函数内每个 `if/elif/match/try/except/循环` 分支为清单逐一覆盖，外加边界输入（空、单元素、极值、非法值）和异常路径。
- **全量比对**：返回值做结构 + 内容的全量比对（稳定序列化后与快照逐字节比较），不只断言个别字段。
- **副作用**：对打桩对象断言调用次数、参数、顺序。
- **分支覆盖率门槛**：`pytest --cov=<pkg> --cov-branch --cov-report=term-missing`，目标函数的行与分支应 100% 覆盖；做不到时逐条列出未覆盖分支及原因，交用户决定。需要 JSON 报告时，路径用仓库内的相对路径（见避坑指南第 8 条）。
  - 未覆盖的分支要逐条判断：是**结构上走不到**（防御性代码，原样保留，写进报告），还是**场景缺失**（补用例）。不能笼统地以"覆盖不到"放过。
  - 被测代码通过 `exec(compile(..., 真实文件路径, "exec"))` 加载时，只要 compile 用的是真实文件路径，coverage 仍然能按文件名统计到。

### 3.3 确定性打桩（强制）

以下动态来源**必须**显式冻结或打桩，否则不得作为黄金快照：

| 来源 | 冻结方式 |
|---|---|
| 时间：`datetime.now/utcnow`、`date.today`、`time.time`、`time.monotonic` | `freezegun` / `time-machine`；JS 用 `vi.useFakeTimers().setSystemTime()`。**不要直接 patch `datetime.datetime`**（C 类型不能打补丁） |
| 随机：`random.*`、`uuid.uuid1/4`、`secrets.*`、`numpy.random`、`Math.random`、`crypto.randomUUID` | 固定 seed 或 patch 成固定序列 |
| 外部 HTTP | `responses` / `respx` / `pytest-httpx` / `msw`；**禁止**真实网络请求（可用 `pytest-socket` 兜底） |
| LLM / AI 调用 | patch SDK client，返回固定响应。LLM 输出不确定，**绝不能**真实调用后录进快照 |
| 数据库 / 缓存 / 消息队列 | 内存替身或 Mock；只读查询可用固定 fixture 数据 |
| 环境：环境变量、时区 `TZ`、locale、当前目录 | `monkeypatch.setenv`、固定 `TZ=UTC` |
| 顺序不确定：`set` 迭代、`PYTHONHASHSEED`、并发完成顺序、文件系统 `listdir` | 快照序列化前排序；固定 `PYTHONHASHSEED=0` |
| 无法打桩的动态字段（自增 ID、耗时） | 快照前用占位符脱敏（如 `"<UUID>"`、`"<ELAPSED>"`），并在测试中单独断言其格式 |

**防 Flaky 门槛**：新测试在原始代码上**连续运行 3 次**，且测试顺序打乱（如 `pytest -p randomly` 或 `--random-order`）后仍 100% 通过，才能认定为有效基线。任何一次不一致，都说明还有未冻结的动态来源，必须先修测试。

快照数据**必须脱敏**：不得含客户数据、凭据、生产原始记录。详见 `references/characterization-test-template.md`。

### 3.4 可选：反向验证（证明测试真能抓回归）

临时改动目标函数中的一个常量或分支条件，确认至少一个特征测试变红，然后**立即还原**（`git diff` 为空才继续）。这一步用来证明快照没有"假绿"。

### 3.5 绿灯许可

- 特征测试 + 全量测试在**原始代码**上全绿，才允许进入重构。
- 重构中**不得**修改测试断言、更新快照（`--update-golden` / `vitest -u`）、加 `skip/xfail` 让测试通过。快照变化就是行为变化，直接触发熔断（见 4.2）。

---

## 模块四：Git 检查点与原子化回滚熔断（Atomic Rollback & Circuit Breaker）

### 4.1 前置条件（Step 4 开始前全部满足）

1. **必须在 Git 仓库中。** 非 Git 项目 → 停下，请用户 `git init` 并提交当前状态。没有回退路径不开始重构。
2. **目标文件无未提交改动**：`git status --porcelain -- <目标文件> <测试目录>` 必须为空。有改动说明那是用户正在做的工作，**请用户先提交或暂存，绝不在其上执行回滚命令**，否则会毁掉用户的代码。
3. **工作分支**：当前在默认分支（main/master）时，新建 `refactor/<模块>-<函数>` 分支。
4. **检查点提交**：特征测试在原始代码上全绿后单独提交（`test: characterization tests for <func>`），记录 `CHECKPOINT=$(git rev-parse HEAD)`。之后所有回滚都回到这里。

### 4.2 熔断触发条件

**立即熔断（0 次重试）：**

- 需要修改特征测试断言或快照才能通过
- 需要改变公开签名、返回结构，或 `export_sanity.py --compare` 返回非 0
- diff 超出 Step 3 确认的文件 / 函数范围
- 发现基线测试本身不确定（flaky）

**有限自愈后熔断：**

- 回归测试失败时，允许**只修改实现代码**（不碰测试和快照）后重跑，称为一次"自愈尝试"。
- 同一轮重构**累计 2 次**自愈尝试仍未 100% 全绿 → 熔断。
- 每次自愈前先写明失败原因假设；不允许用同一个思路重复尝试。

### 4.3 回滚动作（原子化，只撤销本轮自己的改动）

```bash
# 1. 已修改的跟踪文件恢复到检查点（只列出本轮改过的文件，不用 "."）
git restore --source="$CHECKPOINT" --staged --worktree -- <本轮修改的文件...>
# 2. 本轮新建、未跟踪的文件：逐个核对确属本轮创建后删除（不使用 git clean）
# 3. 验证已回到检查点
git diff "$CHECKPOINT" --stat          # 应为空（被误跟踪的 __pycache__/*.pyc 等构建产物除外，不要去恢复它们）
<全量测试命令>                          # 应全绿
```

**禁止使用：** `git reset --hard`、`git checkout .`、`git clean -fd`、`git stash drop`、`git push --force`、`--no-verify`。回滚范围只限本轮重构自己改动的文件。

### 4.4 熔断报告

回滚完成后向用户报告，然后**停止**：不自动开始新一轮尝试。

- 阻塞原因：失败测试名、关键断言 diff 或报错摘要
- 已做的 2 次自愈尝试各自的假设与结果
- 当前状态：已回到检查点 `<sha>`，全量测试全绿
- 可选方案：换一种拆分切法 / 缩小拆分范围 / 用户接受某项行为变化（需用户明确授权并重新生成快照）

---

## 模块五：标准执行流程（SOP）

### Step 0 — 前置扫描

读规则文件（模块一），确认测试 / lint / CI 命令，跑基线全量测试，检查 Git 状态（4.1 的第 1、2 条）。任何一项不满足，先向用户报告。

### Step 1 — 诊断

```bash
python <skill>/scripts/scan_long_functions.py . --max-lines <项目阈值>
```

排除生成代码、vendor、迁移脚本、测试文件。结合行数、分支数、外部引用数，选出**最严重的 1~2 个**函数，其余列为"后续候选"。

| 排名 | 文件:行 | 函数 | 有效行数 | 分支数 | 外部引用 | 备注 |

### Step 2 — 裁决

1. **架构地位**：在调用链中的位置（入口 / 编排层 / 核心计算 / 工具层）
2. **对外契约**：`export_sanity.py` 的结果，哪些名字不可变、哪些测试打桩依赖本模块命名空间
3. **真理源裁决**：采用了哪些规则、屏蔽了哪些过期描述
4. **工程规范 vs 业务标准**：哪些是可调整的结构，哪些是必须锁死的业务逻辑
5. **拆解策略**：原函数保留为编排外壳（签名不变），内部按职责段落抽出私有辅助函数；说明切法理由及风险点（见下方"拆分陷阱"）
6. **测试现状**：现有覆盖、需补的特征测试、需要冻结的动态来源

### Step 3 — 清单（**必须等待用户确认**）

| 顺序 | 辅助函数 | 预估行数 | 职责（一句话） | 输入 → 输出 | 纯函数? | 风险点 |
|---|---|---|---|---|---|---|
| 1 | `_parse_params` | ~15 | 校验并规范化入参 | raw dict → Params | 是 | — |
| … | | | | | | |
| — | `<原函数>`（外壳） | ~12 | 按顺序编排以上步骤 | 不变 | — | 签名不变 |

同时列出：新增测试文件与覆盖场景、需打桩的依赖、预计改动的文件清单、分支名。
**输出清单后停下，请用户确认或调整。** 未确认前只允许做只读分析。

### Step 4 — 执行与回归

- **4a 安全网**：按模块三写特征测试 → 原始代码上连续 3 次 + 乱序全绿 → 检查点提交，记录 `CHECKPOINT`。
- **4b 逐个抽离**：每次只抽一个辅助函数，抽完立即跑相关测试。变红就先撤销这一步，再计入自愈次数。
- **4c 全量回归**：全量测试 + lint + 类型检查（命令与 CI 一致），并执行导出兼容校验：
  ```bash
  python <skill>/scripts/export_sanity.py path/to/target.py --root . --compare .refactor-baseline.json
  ```
- **4d diff 自查**：`git diff $CHECKPOINT --stat` 只含清单内的文件；目标函数签名、装饰器、re-export 无变化。
- **4e 提交**：全部通过后提交 `refactor: extract helpers from <func> (behavior unchanged)`，正文列出抽出的辅助函数及"行为由 <测试文件> 锁定"。
- 未经用户要求不 push、不开 PR。任何一步触发 4.2 → 按 4.3 回滚、按 4.4 报告。

### 拆分陷阱（抽离时逐条自查）

- 被抽出的代码块含 `return` / `break` / `continue` / `yield` / `await`：控制流语义会改变，需要改成返回标志位或保留在外壳中
- 块内修改了后续还会用到的局部变量：辅助函数必须显式返回这些值，且不能漏掉
- `try/except` 范围变化：异常可能被新的边界提前捕获或漏捕
- 短路求值与求值顺序：`a() and b()` 拆开后调用次数可能变化
- 浮点运算的顺序与结合方式不能改：`(a+b)+c ≠ a+(b+c)`，会导致快照数值变化
- 可变默认参数、闭包捕获的变量
- 日志格式中含 `%(funcName)s` / `%(lineno)d`：把日志语句挪进辅助函数会改变日志输出。日志格式属于业务标准时，日志调用留在外壳里
- 依赖调用栈的代码：`inspect.stack()`、`traceback`、`sys._getframe`、递归深度

---

## 运行时避坑指南（执行时逐条遵守）

以下都是实测踩过或已验证存在的坑。每条都是硬规则；各模块的详细说明见括号内章节。

### A. 编码与平台（Windows 重灾区）

1. **显式 UTF-8**：所有 `open()` / `read_text()` / `write_text()` 显式传 `encoding="utf-8"`；**读取**项目源码用 `encoding="utf-8-sig"`，兼容编辑器写入的 BOM。严禁依赖系统默认编码（cp1252 / gbk）。
2. **CLI 输出**：脚本开头 `sys.stdout.reconfigure(encoding="utf-8")`；在 Windows 上跑 pytest 前设 `PYTHONUTF8=1`，否则中文断言信息会触发 `UnicodeEncodeError`。
3. **非 UTF-8 源文件**（如 GBK）：不得跳过了事，列出文件名，人工 Grep 补查引用；**不得**顺手转码，那属于范围外改动。
4. **换行符**：快照写入用 `newline="\n"`，读取用默认的通用换行模式；`core.autocrlf=true` 时可在 `.gitattributes` 中为快照目录设 `eol=lf`（需征得用户同意，属于新增配置）。
5. **路径**：快照中出现路径时统一用 `Path(...).as_posix()` 并转为相对路径，严禁写入绝对路径、用户名、临时目录。
6. **Shell 语法差异**：本文命令为 bash 写法。PowerShell 等价写法：
   - `$env:PYTHONHASHSEED="0"; pytest ...`（bash 为 `PYTHONHASHSEED=0 pytest ...`）
   - `$CHECKPOINT = git rev-parse HEAD`；回滚：`git restore --source=$CHECKPOINT --staged --worktree -- <文件...>`
   - 循环：`1..3 | % { pytest tests/golden -q; if (-not $?) { break } }`
7. **Git Bash 的 `sed -i` 会静默改写换行符**：在 CRLF 文件上执行 `sed -i`，会把**整个文件**的 CRLF 全部转成 LF，命令本身没有任何报错。`autocrlf=true` 时 `git diff` 仍然只显示改动的那几行，所以很难察觉，但工作区文件已经被整体改写。
   - 动手前用 `git ls-files --eol <文件>` 确认换行符（`w/crlf` 就是 CRLF）
   - 修改 CRLF 文件时用 Edit 工具，或者用 Python 以二进制方式读写；**不要对它用 `sed -i`**
   - 每次改完核对一遍：`python -c "b=open('f','rb').read(); print(b.count(b'\n')-b.count(b'\r\n'))"` 应输出 0（裸 LF 数为 0）
   - 已经被转换时，用 Python 把 `\n` 替换回 `\r\n` 恢复（先确认文件里 CRLF 数为 0，避免变成 `\r\r\n`）
8. **`--cov-report=json:路径` 不能用 Windows 绝对路径**：pytest-cov 按冒号切分这个参数，`json:C:/x/cov.json` 中盘符的冒号会把路径截断，报告写到意料之外的位置，后续读取时 `FileNotFoundError`。改用**仓库内已被 gitignore 的相对路径**，例如 `--cov-report=json:.pytest_cache/cov.json`，用完删掉。
9. **`/tmp` 在 Git Bash 和 Windows 版 Python 里不是同一个目录**：Git Bash 的 `/tmp` 映射到 `%LOCALAPPDATA%\Temp`；把 `/tmp/x.json` 作为参数传给 Windows 版 Python 时，会被解析成 `C:\tmp\x.json`。于是 bash 写入的文件 Python 读不到，Python 写入的文件 bash 也找不到。需要在两边共享的中间文件，一律用**完整的 Windows 路径**（会话临时目录），或者用仓库内被 gitignore 的相对路径。

### B. 环境与依赖

10. **用项目自己的解释器**：先确认 `python -c "import sys; print(sys.executable)"` 指向项目 venv，统一用 `python -m pytest`，不要用全局 `pytest`。
11. **不私自装依赖**：项目里没有 freezegun / responses 等库时，**先问用户**是否加入 dev 依赖。安装会修改依赖清单，属于范围外改动。用户不同意时，退回 `unittest.mock` 加占位符脱敏。
12. **构建产物**：`__pycache__/`、`.pytest_cache/`、覆盖率文件、`.refactor-baseline.json` 不计入 diff 检查，也不提交。

### C. Mock 禁区（3.3、2.3）

13. **不直接 patch `datetime.datetime` / `date`**（C 类型不能打补丁），用 `freezegun` / `time-machine`，或对输出做占位符脱敏。
14. **打桩打在名字被查找的位置**：`patch("被测模块.dep")`，而不是 `patch("定义模块.dep")`。不能把 `from x import dep` 与 `x.dep` 两种写法互换，否则 patch 静默失效。
15. **asyncio + freezegun**：冻结时间会连带冻结 `time.monotonic`，可能让 `asyncio.sleep` / 超时逻辑挂死。使用 `freeze_time(..., real_asyncio=True)`（freezegun ≥ 1.4.0）或改用 `time-machine`。
16. **模块级缓存**：`@lru_cache`、模块全局变量、单例会在用例之间串味，导致结果依赖执行顺序。在 fixture 中 `cache_clear()` 或重置全局状态。

### D. 测试假绿（全部视为"未通过"）

17. **快照缺失必须报错**：快照不存在时严禁自动生成后 Pass；只有显式传 `--update-golden` 时才写入，而且只能在原始代码上执行（3.5）。
18. **跑了 0 个测试不算绿**：pytest 退出码 5（no tests collected）视为失败。每次回归核对**收集数、通过数、跳过数**与基线一致。
19. **跳过数增加 = 失败**：`importorskip`、条件 `skipif` 因环境变化多跳过了用例，全绿也不算数。
20. **Mock 没被调用 = 可疑**：每个关键打桩都要断言 `called` / `call_count`。patch 打错位置时，测试会走真实实现或空分支，仍然"通过"。
21. **扫描工具的警告不能忽略**：`export_sanity.py` 报出的"无法解析 / 非常量 getattr / 字符串引用模块"都要人工处理，处理完才能宣布兼容。

---

## 与模型路由规则协同

环境中有全局路由规则时（例如 CLAUDE.md 规定样板 / 测试初稿交给其他模型起草），遵守该规则：特征测试初稿可以外包起草，但**重构实现、熔断判断和最终审查必须由主模型完成**。发给外部模型的内容不得包含凭据或未脱敏数据。

## 完成报告（每次结束必须输出，含中途停止或熔断）

- **状态**：完成 / 部分完成 / 已熔断回滚（附检查点 sha）
- **范围**：文件与函数；原有效行数 → 外壳行数 + 各辅助函数行数
- **真理源**：采用的规则文件，屏蔽的过期描述
- **测试**：新增测试数、覆盖场景、分支覆盖率、3 次稳定性结果、全量测试结果（附命令）
- **兼容性**：`export_sanity --compare` 结果；签名、装饰器、re-export 无变化
- **自愈 / 熔断**：自愈尝试次数，是否触发熔断及原因
- **发现但未处理**：顺带发现的 bug、后续候选函数
- **需用户确认的事项**
