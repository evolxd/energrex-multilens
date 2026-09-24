# 确定性黄金快照测试模板

特征测试记录代码**现在**做了什么，不判断它**应该**做什么。
写完后必须在**未修改的原始代码**上：连续 3 次 + 乱序运行都 100% 通过，才能作为重构基线。

## 检查清单

**确定性（缺一项就不能作为基线）**
- [ ] 时间已冻结：`datetime.now/utcnow`、`date.today`、`time.time`（用 freezegun / time-machine，不直接 patch `datetime.datetime`）
- [ ] 随机已固定：`random`、`uuid.uuid1/uuid4`、`secrets`、`numpy.random`
- [ ] 外部 HTTP 已打桩，并禁止真实网络（`pytest-socket` 的 `--disable-socket` 兜底）
- [ ] LLM / AI SDK 已打桩为固定响应，**从未真实调用**
- [ ] 数据库 / 缓存 / 队列已替换为内存替身或 Mock
- [ ] 环境变量、`TZ`、locale 已固定
- [ ] 快照序列化稳定：key 排序、集合排序、`PYTHONHASHSEED=0`
- [ ] 模块级缓存（`@lru_cache`、全局单例）已在 fixture 中清空，用例之间互不影响
- [ ] 被测代码用到 asyncio 时，使用 `freeze_time(..., real_asyncio=True)`（freezegun ≥ 1.4.0）或 `time-machine`
- [ ] 快照中的路径已转成相对路径加 `as_posix()`；读写快照都显式 `encoding="utf-8"`
- [ ] 无法打桩的动态字段已用占位符脱敏，且单独断言其格式

**覆盖**
- [ ] 目标函数每个分支至少一个用例，`--cov-branch` 显示目标函数 100% 覆盖（或已列出例外）
- [ ] 边界输入：空值、空集合、单元素、极值、非法输入
- [ ] 异常路径：断言异常类型与消息
- [ ] 副作用：断言 Mock 的调用次数、参数、顺序

**快照纪律**
- [ ] 快照不存在时测试**失败**，不能静默生成后通过
- [ ] 只在原始代码上生成快照（`--update-golden`），重构期间禁用
- [ ] 快照纳入版本控制，随检查点提交
- [ ] 快照不含客户数据、凭据、未脱敏生产数据

## Python（pytest）

依赖（按项目已有依赖选用，不要为此引入整套新框架）：`freezegun` 或 `time-machine`、`responses`（requests）或 `respx`（httpx）、可选 `pytest-randomly`、`pytest-socket`。

`tests/conftest.py`：

```python
def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", default=False,
        help="仅在未修改的原始代码上使用：生成/覆盖黄金快照",
    )
```

`tests/golden/test_<target>_golden.py`：

```python
import json
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from freezegun import freeze_time

# 从调用方实际使用的公开入口导入，顺带锁定 re-export 关系
from mypkg.services import compute_value

GOLDEN_DIR = Path(__file__).parent / "snapshots"
MOD = "mypkg.services.valuation"  # 打桩目标：名字被查找的模块，不是定义它的模块

CASES = {
    "happy_path":   {"user_id": 1, "items": [{"sku": "A", "qty": 2}]},
    "empty_items":  {"user_id": 1, "items": []},
    "single_zero":  {"user_id": 1, "items": [{"sku": "A", "qty": 0}]},
    "multi_branch": {"user_id": 2, "items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 5}]},
}

_SCRUB = [  # 无法打桩的动态值 → 占位符
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<UUID>"),
    (re.compile(r'"elapsed_ms": \d+(\.\d+)?'), '"elapsed_ms": "<ELAPSED>"'),
]


def _canonical(obj) -> str:
    """稳定序列化：key 排序；set 先转成排序后的 list；最后做脱敏替换。"""
    def normalize(o):
        if isinstance(o, (set, frozenset)):
            return sorted(normalize(x) for x in o)
        if isinstance(o, dict):
            return {str(k): normalize(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [normalize(x) for x in o]
        return o
    text = json.dumps(normalize(obj), sort_keys=True, ensure_ascii=False, indent=2, default=repr)
    for pattern, placeholder in _SCRUB:
        text = pattern.sub(placeholder, text)
    return text + "\n"


def assert_golden(name: str, obj, request) -> None:
    snapshot = GOLDEN_DIR / f"{name}.json"
    actual = _canonical(obj)
    if request.config.getoption("--update-golden"):
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not snapshot.exists():
        pytest.fail(f"缺少黄金快照 {snapshot}；请在【未修改的原始代码】上运行 --update-golden 生成")
    assert actual == snapshot.read_text(encoding="utf-8"), f"输出与黄金快照 {name} 不一致 → 行为已改变"


@pytest.fixture
def frozen(monkeypatch):
    """冻结所有动态来源。新增依赖时在这里补，不要在各测试里零散打桩。"""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("APP_ENV", "test")
    uuids = (uuid.UUID(int=i) for i in range(1, 10_000))
    llm = MagicMock()
    llm.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="FIXED_LLM_RESPONSE"))]
    )
    with freeze_time("2026-01-01 00:00:00"), \
         patch("uuid.uuid4", side_effect=lambda: next(uuids)), \
         patch(f"{MOD}.random.random", return_value=0.5), \
         patch(f"{MOD}.fetch_price", return_value={"A": 9.9, "B": 1.5}) as fetch_price, \
         patch(f"{MOD}.llm_client", llm):
        yield {"fetch_price": fetch_price, "llm": llm}


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_output(name, frozen, request):
    assert_golden(name, compute_value(**CASES[name]), request)


def test_side_effects_order(frozen):
    compute_value(**CASES["multi_branch"])
    assert frozen["fetch_price"].call_args_list == [call(["A", "B"])]
    assert frozen["llm"].chat.completions.create.call_count == 1


def test_invalid_input_raises(frozen):
    with pytest.raises(ValueError, match="user_id"):
        compute_value(user_id=None, items=[])
```

HTTP 在库层打桩的写法（requests）：

```python
import responses

@responses.activate
def test_with_http(frozen, request):
    responses.get("https://api.example.com/price", json={"A": 9.9}, status=200)
    assert_golden("http_case", compute_value(**CASES["happy_path"]), request)
    assert len(responses.calls) == 1
```

运行顺序：

```bash
# 1) 在原始代码上生成快照（只做一次）
pytest tests/golden --update-golden
# 2) 稳定性门槛：连续 3 次（装了 pytest-randomly 会自动乱序，每次随机 seed 不同）
for i in 1 2 3; do PYTHONHASHSEED=0 pytest tests/golden -q || break; done
# 3) 分支覆盖率
pytest tests/golden --cov=mypkg.services.valuation --cov-branch --cov-report=term-missing
```

## TypeScript（Vitest）

ESM 的模块命名空间是只读的，`vi.spyOn(importedNamespace, "fn")` 在 ESM 下不生效，要用 `vi.mock` 做模块级打桩：

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../src/prices", () => ({ fetchPrices: vi.fn() }));
vi.mock("../src/llm", () => ({ llm: { complete: vi.fn() } }));

import { fetchPrices } from "../src/prices";
import { llm } from "../src/llm";
import { targetFunction } from "../src"; // 走 index 的 re-export，同时锁定导出关系

describe("targetFunction characterization", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    let n = 0;
    vi.spyOn(crypto, "randomUUID").mockImplementation(
      () => `00000000-0000-0000-0000-${String(++n).padStart(12, "0")}` as `${string}-${string}-${string}-${string}-${string}`,
    );
    vi.mocked(fetchPrices).mockResolvedValue({ A: 9.9 });
    vi.mocked(llm.complete).mockResolvedValue("FIXED_LLM_RESPONSE");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it.each([
    ["happy_path", { userId: 1, items: [{ sku: "A", qty: 2 }] }],
    ["empty_items", { userId: 1, items: [] }],
  ])("%s", async (name, input) => {
    await expect(targetFunction(input)).resolves.toMatchSnapshot(name);
  });

  it("calls dependencies exactly as before", async () => {
    await targetFunction({ userId: 1, items: [{ sku: "A", qty: 2 }] });
    expect(fetchPrices).toHaveBeenCalledTimes(1);
    expect(fetchPrices).toHaveBeenCalledWith(["A"]);
  });
});
```

在 CI 模式（`CI=true vitest run`）下，缺失的快照会直接失败，不会自动写入。重构期间**禁止** `vitest -u`。

## 输入样本来源（按优先级）

1. 现有测试中的 fixture
2. 按分支条件手工构造的最小输入
3. 生产日志 / 真实请求样本：**必须先脱敏**，不能把客户数据、凭据写进快照文件
