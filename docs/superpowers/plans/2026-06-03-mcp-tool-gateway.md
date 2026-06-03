# MCP 工具中转站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本项目现有的数据/新闻/分析工具，通过 MCP（Streamable HTTP）挂在现有 `stock-server` 进程的 `/mcp` 路由对外开放，供其他智能体零胶水调用。

**Architecture:** 注册表反射桥接——复用 `ToolDefinition` 注册表作单一真相源，新增 `to_mcp_tool()` 把工具反射成 MCP 工具，低层 `mcp.server.lowlevel.Server` 的 `list_tools/call_tool` 按名字派发到现有 handler；`StreamableHTTPSessionManager` 作为 ASGI 子应用 `app.mount("/mcp", ...)`，外套 API Key 鉴权中间件。16 个新数据工具包装成 `ToolDefinition`，其中 7 个 granular 能力在 `data_provider/base.py` 新增沿用现有 `_fetchers` 优先级链的只读薄路由方法。

**Tech Stack:** Python 3 / FastAPI / Starlette ASGI / 官方 `mcp` Python SDK / pandas / unittest+pytest

**关联 spec:** `docs/superpowers/specs/2026-06-03-mcp-tool-gateway-design.md`（含第 0 节实施前修订、第 6 节铁律合规检查）

---

## 仓库约定（AGENTS.md，务必遵守）

- commit message **用英文**，**不加 `Co-Authored-By`**；类型前缀 `feat:`/`test:`/`docs:`/`chore:`。
- 未经明确确认不 `git push`/`git tag`；本计划只做本地 commit。
- 新增配置项**必须**同步 `.env.example`；涉及 API 行为变化**必须**更新 `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式 `- [类型] 描述`，**禁止**加 `### 标题`）。
- 后端依赖装进项目 `.venv`（`source .venv/bin/activate` 后 `pip install`），禁止全局/`--user`。
- 后端验证：`./scripts/ci_gate.sh`；最低 `python -m py_compile <changed>`；测试 `python -m pytest -m "not network"`。
- 不写死密钥/端口/模型名。

---

## 文件结构（决定任务拆分）

**新建**
- `src/agent/tools/dataset_tools.py` — 16 个新数据工具的 `ToolDefinition` + `ALL_DATASET_TOOLS`。
- `api/mcp/__init__.py` — 包标识。
- `api/mcp/server.py` — 白名单注册表构建、低层 MCP Server、派发、SessionManager 单例、ASGI app 工厂。
- `api/mcp/auth.py` — API Key ASGI 鉴权中间件 + Key 加载。
- `api/mcp/rate_limit.py` — 限流扩展点（本期 no-op）。
- 测试：`tests/test_registry_to_mcp.py`、`tests/test_base_granular_routing.py`、`tests/test_dataset_tools.py`、`tests/test_mcp_server.py`、`tests/test_mcp_auth.py`、`tests/test_mcp_app_mount.py`。
- 文档：`docs/mcp-gateway.md`。

**修改**
- `src/agent/tools/registry.py` — `ToolDefinition.to_mcp_tool()` + `ToolRegistry.to_mcp_tools()`。
- `data_provider/base.py` — `DataFetcherManager` 新增 7 个只读薄路由方法。
- `api/app.py` — `app.mount("/mcp", ...)` + `app_lifespan` 集成 `session_manager.run()`。
- `src/config.py`（或现有 config 入口）— 新增 `mcp_api_keys` 等字段读取。
- `.env.example` — 新增 MCP 配置项。
- `requirements.txt` — 新增 `mcp`。
- `docs/CHANGELOG.md` — `[Unreleased]` 追加条目。

---

## Task 1: 新增 mcp 依赖并装入 .venv

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 在 requirements.txt 追加依赖**

在 `requirements.txt` 末尾新增一行（保持与现有风格一致，不要改动其他行）。**钉死大版本上限**，因为本计划所有 SDK API 形状（lowlevel.Server 装饰器签名、StreamableHTTPSessionManager 构造参数、TransportSecuritySettings 字段）都对版本敏感：

```
mcp>=1.9.0,<2
```

- [ ] **Step 2: 安装到项目虚拟环境**

Run:
```bash
cd "/Volumes/Mac硬盘/project/股票分析部署/daily_stock_analysis"
[ -d .venv ] || python -m venv .venv
source .venv/bin/activate
pip install "mcp>=1.9.0,<2"
pip show mcp | grep -i version   # 记录实际版本，后续任务以此版本的 API 为准
```
Expected: 安装成功，无错误，打印实际版本。

- [ ] **Step 3: 验证关键导入 + 装饰器签名契约可用**

不仅验证类能 import，还要**实际跑通 `Server` 的 `list_tools`/`call_tool` 装饰器注册**（不发请求，只确认装饰器接受我们计划用的回调签名）。不同 mcp 版本回调签名差异较大，这步能在最早期暴露版本不符。

Run:
```bash
source .venv/bin/activate && python -c "
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
import mcp.types as t
s = Server('probe')
@s.list_tools()
async def _lt():
    return []
@s.call_tool()
async def _ct(name, arguments):
    return []
print('mcp ok', t.Tool.__name__, t.TextContent.__name__, 'decorators ok')
"
```
Expected: 打印 `mcp ok Tool TextContent decorators ok`。
**若此处报 `TypeError`（call_tool/list_tools 签名不符）或导入路径报错**：说明实际安装版本 API 不同，**以实际版本签名为准**调整 Task 5 的 `_list_tools/_call_tool`，并把差异记入计划"风险点"，不要硬套本计划的签名。

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add mcp python sdk dependency for tool gateway"
```

---

## Task 2: ToolDefinition.to_mcp_tool() 与 ToolRegistry.to_mcp_tools()

将工具反射成 MCP `tools/list` 元素 `{name, description, inputSchema}`。**不在 registry.py 顶部 import mcp**（保持核心模块依赖纯净），返回普通 dict，由 `api/mcp/server.py` 再包成 `types.Tool`。

**Files:**
- Modify: `src/agent/tools/registry.py`
- Test: `tests/test_registry_to_mcp.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_registry_to_mcp.py`：

```python
# -*- coding: utf-8 -*-
"""ToolDefinition/ToolRegistry 的 MCP schema 转换测试。"""
import unittest

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry


def _sample_tool() -> ToolDefinition:
    return ToolDefinition(
        name="get_demo",
        description="Demo tool",
        parameters=[
            ToolParameter(name="stock_code", type="string", description="A股代码", required=True),
            ToolParameter(name="days", type="integer", description="天数", required=False, default=30),
            ToolParameter(name="region", type="string", description="市场", required=False,
                          enum=["cn", "hk", "us"]),
        ],
        handler=lambda stock_code, days=30, region="cn": {"ok": True},
        category="data",
    )


class TestToolToMcp(unittest.TestCase):
    def test_to_mcp_tool_shape(self):
        d = _sample_tool().to_mcp_tool()
        self.assertEqual(d["name"], "get_demo")
        self.assertEqual(d["description"], "Demo tool")
        schema = d["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("stock_code", schema["properties"])
        self.assertEqual(schema["properties"]["stock_code"]["type"], "string")
        self.assertEqual(schema["properties"]["region"]["enum"], ["cn", "hk", "us"])
        self.assertEqual(schema["required"], ["stock_code"])

    def test_registry_to_mcp_tools(self):
        reg = ToolRegistry()
        reg.register(_sample_tool())
        tools = reg.to_mcp_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "get_demo")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_registry_to_mcp.py -v`
Expected: FAIL，`AttributeError: 'ToolDefinition' object has no attribute 'to_mcp_tool'`。

- [ ] **Step 3: 实现 to_mcp_tool / to_mcp_tools**

在 `src/agent/tools/registry.py` 的 `ToolDefinition` 类内，紧跟现有 `to_openai_tool` 方法之后新增：

```python
    def to_mcp_tool(self) -> dict:
        """Convert to an MCP ``tools/list`` element: {name, description, inputSchema}.

        Returns a plain dict so this core module stays free of the mcp dependency;
        the MCP server layer wraps it into ``mcp.types.Tool``.
        """
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self._params_json_schema(),
        }
```

在 `ToolRegistry` 类内，紧跟现有 `to_openai_tools` 方法之后新增：

```python
    def to_mcp_tools(self) -> List[dict]:
        """Generate MCP tools/list elements for all registered tools."""
        return [t.to_mcp_tool() for t in self._tools.values()]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_registry_to_mcp.py -v`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools/registry.py tests/test_registry_to_mcp.py
git commit -m "feat: add MCP tool schema conversion to ToolDefinition/ToolRegistry"
```

---

## Task 3: DataFetcherManager 新增 7 个只读薄路由方法

为 7 个 granular 能力（财务三表、质押、回购、增减持、解禁）在 manager 层增加沿用 `_fetchers` 优先级链（Tushare 先 → akshare 末位）的只读 pass-through，照搬现有 `get_belong_boards` 的 `for fetcher in self._fetchers: if hasattr(...)` 模式。**不改动任何现有路由/优先级方法。**

**Files:**
- Modify: `data_provider/base.py`（`DataFetcherManager` 类内，建议加在现有 `get_dragon_tiger_context` 方法之后）
- Test: `tests/test_base_granular_routing.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_base_granular_routing.py`：

```python
# -*- coding: utf-8 -*-
"""DataFetcherManager granular 薄路由方法测试：Tushare 优先、akshare 末位、hasattr 守卫。"""
import unittest
from unittest.mock import MagicMock

import pandas as pd

from data_provider.base import DataFetcherManager


class _FetcherTushare:
    name = "tushare"
    def get_income_statement(self, stock_code):
        return pd.DataFrame([{"end_date": "20251231", "revenue": 100}])


class _FetcherAkshareOnlyName:
    """模拟没有该方法的 fetcher（hasattr 应跳过，不报错）。"""
    name = "akshare"


def _manager_with(fetchers):
    mgr = DataFetcherManager.__new__(DataFetcherManager)  # 跳过 __init__ 真实初始化
    mgr._fetchers = fetchers
    return mgr


class TestGranularRouting(unittest.TestCase):
    def test_income_statement_uses_first_fetcher_with_method(self):
        mgr = _manager_with([_FetcherAkshareOnlyName(), _FetcherTushare()])
        df = mgr.get_income_statement("600519")
        self.assertIsNotNone(df)
        self.assertEqual(df.iloc[0]["revenue"], 100)

    def test_returns_none_when_no_fetcher_has_data(self):
        empty = MagicMock()
        empty.name = "x"
        empty.get_pledge_detail.return_value = None
        mgr = _manager_with([empty])
        self.assertIsNone(mgr.get_pledge_detail("600519"))

    def test_repurchase_passes_date_args(self):
        f = MagicMock()
        f.name = "tushare"
        f.get_repurchase.return_value = pd.DataFrame([{"ann_date": "20250101"}])
        mgr = _manager_with([f])
        out = mgr.get_repurchase("600519", start_date="20250101", end_date="20250201")
        self.assertIsNotNone(out)
        f.get_repurchase.assert_called_once_with("600519", start_date="20250101", end_date="20250201")

    def test_non_a_share_returns_none_without_calling_fetcher(self):
        """granular 能力 A 股专属：港股/美股代码应早返回 None，不触达 fetcher。"""
        f = MagicMock()
        f.name = "tushare"
        mgr = _manager_with([f])
        self.assertIsNone(mgr.get_income_statement("AAPL"))
        self.assertIsNone(mgr.get_income_statement("hk00700"))
        f.get_income_statement.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_base_granular_routing.py -v`
Expected: FAIL，`AttributeError: 'DataFetcherManager' object has no attribute 'get_income_statement'`。

- [ ] **Step 3: 实现 7 个薄路由方法**

在 `data_provider/base.py` 的 `DataFetcherManager` 类内（紧跟 `get_dragon_tiger_context` 之后）新增。先确认文件顶部已 `import pandas as pd` 与 `from typing import Optional`（现有代码已用 `pd.DataFrame`/`Optional`，无需重复添加）：

```python
    def _route_single_code_df(self, method_name: str, stock_code: str, **kwargs) -> Optional["pd.DataFrame"]:
        """Generic read-only passthrough following the existing _fetchers priority
        chain (TickFlow/Tushare first, akshare last). Returns the first non-empty
        DataFrame; never alters routing/priority. Mirrors get_belong_boards()
        including its normalize + A-share guard (these granular capabilities are
        A-share-only via tushare_fetcher)."""
        stock_code = normalize_stock_code(stock_code)
        if _market_tag(stock_code) != "cn":
            return None
        for fetcher in self._fetchers:
            if not hasattr(fetcher, method_name):
                continue
            try:
                df = getattr(fetcher, method_name)(stock_code, **kwargs)
                if df is not None and not getattr(df, "empty", False):
                    logger.info(f"[{fetcher.name}] {method_name} 成功: {stock_code}")
                    return df
            except Exception as e:
                logger.warning(f"[{fetcher.name}] {method_name} 失败: {e}")
        return None

    def get_income_statement(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_income_statement", stock_code)

    def get_cashflow_statement(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_cashflow_statement", stock_code)

    def get_fina_indicator(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_fina_indicator", stock_code)

    def get_pledge_detail(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_pledge_detail", stock_code)

    def get_holder_trade(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_holder_trade", stock_code)

    def get_share_float(self, stock_code: str) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df("get_share_float", stock_code)

    def get_repurchase(self, stock_code: str, start_date: Optional[str] = None,
                       end_date: Optional[str] = None) -> Optional["pd.DataFrame"]:
        return self._route_single_code_df(
            "get_repurchase", stock_code, start_date=start_date, end_date=end_date)
```

> 注意 1：`normalize_stock_code` 与 `_market_tag` 已在 `base.py` 内被现有 `get_belong_boards` 使用，无需新增 import。实现前先 `grep -nE "def normalize_stock_code|def _market_tag|normalize_stock_code\(|_market_tag\(" data_provider/base.py` 确认两者在 `DataFetcherManager` 作用域可见（应为模块级函数或可访问符号）；若 `_market_tag` 实际名称不同（如 `_resolve_market`），以 `get_belong_boards` 里的真实调用为准照搬。
>
> 注意 2：`get_repurchase` 通过 `_route_single_code_df` 的 `**kwargs` 透传日期；`_route_single_code_df` 调用 `getattr(fetcher, method_name)(stock_code, **kwargs)`，与 fetcher 层 `get_repurchase(self, stock_code, start_date=None, end_date=None)` 签名一致。

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_base_granular_routing.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: py_compile 守住数据源核心文件**

Run: `source .venv/bin/activate && python -m py_compile data_provider/base.py && echo OK`
Expected: `OK`。

- [ ] **Step 6: Commit**

```bash
git add data_provider/base.py tests/test_base_granular_routing.py
git commit -m "feat: add read-only granular passthrough methods to DataFetcherManager"
```

---

## Task 4: dataset_tools.py — 16 个新数据工具

**Files:**
- Create: `src/agent/tools/dataset_tools.py`
- Test: `tests/test_dataset_tools.py`

工具清单（全部 category=`data`）：
- 单股 DataFrame（共用工厂）：`get_income_statement`、`get_cashflow_statement`、`get_financial_indicators`(→`get_fina_indicator`)、`get_pledge_detail`、`get_holder_trade`、`get_share_float`
- 单股 + 日期 DataFrame：`get_repurchase`
- 单股 dict（manager context）：`get_dragon_tiger`(→`get_dragon_tiger_context`)、`get_risk_assessment`(→`get_risk_context`)、`get_stock_sectors`(→`get_belong_boards`)
- 单股盘中：`get_intraday_kline`(DataFrame)、`get_order_book`(dict)
- 全市场：`get_limit_up_pool`、`get_hot_stocks`、`get_concept_rankings`、`get_market_stats`

> 映射说明：`get_dragon_tiger` 调 manager 已 routed 的 `get_dragon_tiger_context(stock_code)`（base.py L3395 存在），**以此为准，覆盖 spec 第 5.2 节表里 `get_top_list/get_top_inst` 的笔误**——那两个只在 fetcher 层、manager 无 routed 版，不能直接用。`get_dragon_tiger_context` 第二参 `budget_seconds` 可选，handler 只传 `stock_code` 正确。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_dataset_tools.py`：

```python
# -*- coding: utf-8 -*-
"""dataset_tools：新数据工具 handler 经 manager 取数、DataFrame 序列化、None 容错。"""
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

import src.agent.tools.dataset_tools as dt


class TestDatasetTools(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        patcher = patch.object(dt, "_get_fetcher_manager", return_value=self.mgr)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_all_dataset_tools_exact_16(self):
        names = {t.name for t in dt.ALL_DATASET_TOOLS}
        expected = {
            "get_income_statement", "get_cashflow_statement", "get_financial_indicators",
            "get_pledge_detail", "get_holder_trade", "get_share_float", "get_repurchase",
            "get_dragon_tiger", "get_risk_assessment", "get_stock_sectors",
            "get_intraday_kline", "get_order_book", "get_limit_up_pool",
            "get_hot_stocks", "get_concept_rankings", "get_market_stats",
        }
        self.assertEqual(len(dt.ALL_DATASET_TOOLS), 16)
        self.assertEqual(names, expected)  # 恰好这 16 个，锁死回归
        self.assertNotIn("get_price_percentile", names)  # 已被 get_risk_assessment 替换

    def test_income_statement_serializes_dataframe(self):
        self.mgr.get_income_statement.return_value = pd.DataFrame(
            [{"end_date": "20251231", "revenue": 100}])
        out = dt._handle_single_code_df("get_income_statement", "600519")
        self.assertEqual(out["stock_code"], "600519")
        self.assertEqual(out["items"][0]["revenue"], 100)
        self.mgr.get_income_statement.assert_called_once_with("600519")

    def test_none_returns_info(self):
        self.mgr.get_pledge_detail.return_value = None
        out = dt._handle_single_code_df("get_pledge_detail", "600519")
        self.assertIn("info", out)

    def test_dragon_tiger_returns_dict_passthrough(self):
        self.mgr.get_dragon_tiger_context.return_value = {"has_data": True}
        out = dt._handle_dragon_tiger("600519")
        self.assertEqual(out, {"has_data": True})

    def test_market_stats_no_arg(self):
        self.mgr.get_market_stats.return_value = {"up_count": 3000}
        out = dt._handle_market_stats()
        self.assertEqual(out["up_count"], 3000)

    def test_concept_rankings_tuple(self):
        self.mgr.get_concept_rankings.return_value = ([{"name": "AI"}], [{"name": "煤炭"}])
        out = dt._handle_concept_rankings(5)
        self.assertEqual(out["top"][0]["name"], "AI")
        self.assertEqual(out["bottom"][0]["name"], "煤炭")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_dataset_tools.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'src.agent.tools.dataset_tools'`。

- [ ] **Step 3: 实现 dataset_tools.py**

创建 `src/agent/tools/dataset_tools.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 中转站新增数据工具。

全部经 DataFetcherManager 取数，继承数据源铁律优先级（TickFlow/Tushare 先、
akshare 末位）。DataFrame 结果统一序列化为 records；None/空 → {"info": ...}。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)

_MAX_ROWS = 60


def _get_fetcher_manager():
    """复用 data_tools 已有的 manager 单例，避免进程内重复初始化 Tushare/TickFlow
    （AGENTS.md：优先复用现有入口，不新增平行实现）。"""
    from src.agent.tools.data_tools import _get_fetcher_manager as _shared
    return _shared()


def _df_to_records(df) -> Optional[List[Dict[str, Any]]]:
    if df is None or getattr(df, "empty", True):
        return None
    return df.head(_MAX_ROWS).to_dict(orient="records")


# ---------- handlers ----------

def _handle_single_code_df(method_name: str, stock_code: str, **kwargs) -> Dict[str, Any]:
    try:
        mgr = _get_fetcher_manager()
        df = getattr(mgr, method_name)(stock_code, **kwargs)
        recs = _df_to_records(df)
        if recs is None:
            return {"info": f"No data for {stock_code} ({method_name})."}
        return {"stock_code": stock_code, "items": recs}
    except Exception:
        logger.warning(f"[dataset_tools] {method_name} error", exc_info=True)
        return {"error": f"Failed to fetch {method_name} for {stock_code}."}


def _handle_repurchase(stock_code: str, start_date: str = "", end_date: str = "") -> Dict[str, Any]:
    return _handle_single_code_df(
        "get_repurchase", stock_code,
        start_date=start_date or None, end_date=end_date or None)


def _handle_dragon_tiger(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_dragon_tiger_context(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] dragon_tiger error", exc_info=True)
        return {"error": f"Failed to fetch dragon_tiger for {stock_code}."}


def _handle_risk_assessment(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_risk_context(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] risk_context error", exc_info=True)
        return {"error": f"Failed to fetch risk for {stock_code}."}


def _handle_stock_sectors(stock_code: str) -> Dict[str, Any]:
    try:
        boards = _get_fetcher_manager().get_belong_boards(stock_code) or []
        return {"stock_code": stock_code, "boards": boards}
    except Exception:
        logger.warning("[dataset_tools] belong_boards error", exc_info=True)
        return {"error": f"Failed to fetch sectors for {stock_code}."}


def _handle_intraday_kline(stock_code: str, period: str = "5m", count: int = 240) -> Dict[str, Any]:
    try:
        df = _get_fetcher_manager().get_intraday_kline(stock_code, period=period, count=count)
        recs = _df_to_records(df)
        if recs is None:
            return {"info": f"No intraday kline for {stock_code}."}
        return {"stock_code": stock_code, "period": period, "items": recs}
    except Exception:
        logger.warning("[dataset_tools] intraday_kline error", exc_info=True)
        return {"error": f"Failed to fetch intraday kline for {stock_code}."}


def _handle_order_book(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_order_book(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] order_book error", exc_info=True)
        return {"error": f"Failed to fetch order book for {stock_code}."}


def _handle_limit_up_pool(date: str = "", n: int = 20) -> Dict[str, Any]:
    try:
        data = _get_fetcher_manager().get_limit_up_pool(date=date or None, n=n) or []
        return {"items": data}
    except Exception:
        logger.warning("[dataset_tools] limit_up_pool error", exc_info=True)
        return {"error": "Failed to fetch limit up pool."}


def _handle_hot_stocks(n: int = 10) -> Dict[str, Any]:
    try:
        return {"items": _get_fetcher_manager().get_hot_stocks(n=n) or []}
    except Exception:
        logger.warning("[dataset_tools] hot_stocks error", exc_info=True)
        return {"error": "Failed to fetch hot stocks."}


def _handle_concept_rankings(n: int = 5) -> Dict[str, Any]:
    try:
        top, bottom = _get_fetcher_manager().get_concept_rankings(n=n)
        return {"top": top or [], "bottom": bottom or []}
    except Exception:
        logger.warning("[dataset_tools] concept_rankings error", exc_info=True)
        return {"error": "Failed to fetch concept rankings."}


def _handle_market_stats() -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_market_stats() or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] market_stats error", exc_info=True)
        return {"error": "Failed to fetch market stats."}


# ---------- ToolDefinition 工厂（单股 DataFrame 类，DRY）----------

_CODE_PARAM = ToolParameter(name="stock_code", type="string",
                            description="A股代码，如 '600519'", required=True)


def _make_single_code_df_tool(tool_name: str, method_name: str, description: str) -> ToolDefinition:
    def handler(stock_code: str, _m=method_name) -> Dict[str, Any]:
        return _handle_single_code_df(_m, stock_code)
    return ToolDefinition(name=tool_name, description=description,
                          parameters=[_CODE_PARAM], handler=handler, category="data")


_SINGLE_CODE_DF_SPECS = [
    ("get_income_statement", "get_income_statement", "获取个股利润表（按报告期，最近若干期）。"),
    ("get_cashflow_statement", "get_cashflow_statement", "获取个股现金流量表（按报告期）。"),
    ("get_financial_indicators", "get_fina_indicator", "获取个股财务指标（ROE/毛利率/资产负债率等）。"),
    ("get_pledge_detail", "get_pledge_detail", "获取个股股权质押明细。"),
    ("get_holder_trade", "get_holder_trade", "获取个股股东增减持记录。"),
    ("get_share_float", "get_share_float", "获取个股限售解禁明细。"),
]

# ---------- 显式 ToolDefinition（非统一签名）----------

get_repurchase_tool = ToolDefinition(
    name="get_repurchase",
    description="获取个股股份回购记录，可选起止日期(YYYYMMDD)。",
    parameters=[
        _CODE_PARAM,
        ToolParameter(name="start_date", type="string", description="起始日 YYYYMMDD", required=False),
        ToolParameter(name="end_date", type="string", description="截止日 YYYYMMDD", required=False),
    ],
    handler=_handle_repurchase, category="data",
)

get_dragon_tiger_tool = ToolDefinition(
    name="get_dragon_tiger",
    description="获取个股龙虎榜上榜信息（含机构席位、买卖额）。",
    parameters=[_CODE_PARAM], handler=_handle_dragon_tiger, category="data",
)

get_risk_assessment_tool = ToolDefinition(
    name="get_risk_assessment",
    description="获取个股风险与估值评估（估值水平、历史分位、风险信号）。",
    parameters=[_CODE_PARAM], handler=_handle_risk_assessment, category="data",
)

get_stock_sectors_tool = ToolDefinition(
    name="get_stock_sectors",
    description="获取个股所属板块/概念列表。",
    parameters=[_CODE_PARAM], handler=_handle_stock_sectors, category="data",
)

get_intraday_kline_tool = ToolDefinition(
    name="get_intraday_kline",
    description="获取个股分钟级K线（period: 5m/15m/30m/60m）。",
    parameters=[
        _CODE_PARAM,
        ToolParameter(name="period", type="string", description="周期", required=False,
                      enum=["5m", "15m", "30m", "60m"], default="5m"),
        ToolParameter(name="count", type="integer", description="返回根数(默认240)", required=False, default=240),
    ],
    handler=_handle_intraday_kline, category="data",
)

get_order_book_tool = ToolDefinition(
    name="get_order_book",
    description="获取个股五档盘口（买卖五档量价）。",
    parameters=[_CODE_PARAM], handler=_handle_order_book, category="data",
)

get_limit_up_pool_tool = ToolDefinition(
    name="get_limit_up_pool",
    description="获取当日涨停池/连板梯队。",
    parameters=[
        ToolParameter(name="date", type="string", description="日期 YYYYMMDD，默认当日", required=False),
        ToolParameter(name="n", type="integer", description="返回条数(默认20)", required=False, default=20),
    ],
    handler=_handle_limit_up_pool, category="data",
)

get_hot_stocks_tool = ToolDefinition(
    name="get_hot_stocks",
    description="获取市场人气股榜。",
    parameters=[ToolParameter(name="n", type="integer", description="返回条数(默认10)", required=False, default=10)],
    handler=_handle_hot_stocks, category="data",
)

get_concept_rankings_tool = ToolDefinition(
    name="get_concept_rankings",
    description="获取概念/题材涨跌榜（领涨 top 与领跌 bottom）。",
    parameters=[ToolParameter(name="n", type="integer", description="每侧条数(默认5)", required=False, default=5)],
    handler=_handle_concept_rankings, category="data",
)

get_market_stats_tool = ToolDefinition(
    name="get_market_stats",
    description="获取全市场涨跌家数统计（涨/跌/平/涨停/跌停/成交额）。",
    parameters=[], handler=_handle_market_stats, category="data",
)


ALL_DATASET_TOOLS: List[ToolDefinition] = (
    [_make_single_code_df_tool(t, m, d) for (t, m, d) in _SINGLE_CODE_DF_SPECS]
    + [
        get_repurchase_tool,
        get_dragon_tiger_tool,
        get_risk_assessment_tool,
        get_stock_sectors_tool,
        get_intraday_kline_tool,
        get_order_book_tool,
        get_limit_up_pool_tool,
        get_hot_stocks_tool,
        get_concept_rankings_tool,
        get_market_stats_tool,
    ]
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_dataset_tools.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools/dataset_tools.py tests/test_dataset_tools.py
git commit -m "feat: add 16 dataset tools for MCP gateway"
```

---

## Task 5: api/mcp/server.py — 白名单注册表 + 低层 Server + ASGI 工厂

**Files:**
- Create: `api/mcp/__init__.py`、`api/mcp/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_mcp_server.py`：

```python
# -*- coding: utf-8 -*-
"""MCP server：白名单注册表(恰好30个/排除项缺席) + 派发。"""
import json
import unittest
from unittest.mock import patch

import api.mcp.server as mcpsrv


class TestMcpRegistry(unittest.TestCase):
    def test_registry_has_exactly_30_tools(self):
        reg = mcpsrv.build_mcp_registry()
        self.assertEqual(len(reg), 30)

    def test_excluded_tools_absent(self):
        names = set(mcpsrv.build_mcp_registry().list_names())
        for blocked in ("get_portfolio_snapshot", "get_analysis_context",
                        "get_stock_backtest_summary", "get_skill_backtest_summary",
                        "get_strategy_backtest_summary"):
            self.assertNotIn(blocked, names)

    def test_key_tools_present(self):
        names = set(mcpsrv.build_mcp_registry().list_names())
        for t in ("get_realtime_quote", "search_stock_news", "get_dragon_tiger",
                  "get_income_statement", "get_risk_assessment"):
            self.assertIn(t, names)

    def test_no_duplicate_names_across_sources(self):
        """防止新增工具与镜像工具撞名被 register 静默覆盖（计数仍 30 却少了一个）。"""
        from src.agent.tools.data_tools import ALL_DATA_TOOLS
        from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
        from src.agent.tools.market_tools import ALL_MARKET_TOOLS
        from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
        from src.agent.tools.dataset_tools import ALL_DATASET_TOOLS
        names = [t.name for t in (ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_MARKET_TOOLS
                                  + ALL_SEARCH_TOOLS + ALL_DATASET_TOOLS)
                 if t.name not in mcpsrv.EXCLUDED_TOOL_NAMES]
        self.assertEqual(len(names), len(set(names)), f"duplicate tool names: {names}")

    def test_list_tools_payload_shape(self):
        tools = mcpsrv.build_mcp_registry().to_mcp_tools()
        self.assertEqual(len(tools), 30)
        self.assertTrue(all({"name", "description", "inputSchema"} <= set(t) for t in tools))

    def test_dispatch_routes_to_handler(self):
        reg = mcpsrv.build_mcp_registry()
        with patch.object(reg, "execute", return_value={"ok": 1}) as ex:
            out = mcpsrv._dispatch(reg, "get_realtime_quote", {"stock_code": "600519"})
        self.assertEqual(out, {"ok": 1})
        ex.assert_called_once_with("get_realtime_quote", stock_code="600519")

    def test_dispatch_unknown_tool(self):
        reg = mcpsrv.build_mcp_registry()
        out = mcpsrv._dispatch(reg, "no_such_tool", {})
        self.assertIn("error", out)

    def test_dispatch_handler_exception(self):
        reg = mcpsrv.build_mcp_registry()
        with patch.object(reg, "execute", side_effect=RuntimeError("boom")):
            out = mcpsrv._dispatch(reg, "get_realtime_quote", {"stock_code": "x"})
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'api.mcp'`。

- [ ] **Step 3: 创建包标识**

创建 `api/mcp/__init__.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 工具中转站子包：低层 MCP Server + Streamable HTTP 挂载 + API Key 鉴权。"""
```

- [ ] **Step 4: 实现 server.py**

创建 `api/mcp/server.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 工具中转站 server 装配。

- build_mcp_registry(): 白名单注册表（镜像 14 + 新增 16 = 30），排除私有/回测工具。
- 低层 mcp Server：list_tools 反射注册表，call_tool 按名字派发到 handler。
- StreamableHTTPSessionManager：单例，供 lifespan run() 与挂载 handle_request 共用。
- build_mcp_asgi_app(): 返回带 API Key 鉴权的 ASGI 子应用，供 app.mount("/mcp", ...)。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool

from src.agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 私有数据 + 回测工具排除（回测工具本就不在镜像列表里，这里再显式兜底）
EXCLUDED_TOOL_NAMES = {
    "get_portfolio_snapshot",
    "get_analysis_context",
    "get_stock_backtest_summary",
    "get_skill_backtest_summary",
    "get_strategy_backtest_summary",
}


def build_mcp_registry() -> ToolRegistry:
    """构建对外开放的白名单注册表（恰好 30 个工具）。"""
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.dataset_tools import ALL_DATASET_TOOLS

    registry = ToolRegistry()
    for tool_def in (ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_MARKET_TOOLS
                     + ALL_SEARCH_TOOLS + ALL_DATASET_TOOLS):
        if tool_def.name in EXCLUDED_TOOL_NAMES:
            continue
        registry.register(tool_def)
    return registry


def _dispatch(registry: ToolRegistry, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """同步派发（可单测）：执行工具 handler，错误转结构化 dict。

    先用 __contains__ 判断工具是否存在（避免 handler 内部自抛 KeyError 被误判为
    'Unknown tool'），再 try/except 包执行。"""
    if name not in registry:
        return {"error": f"Unknown tool: {name}"}
    try:
        return registry.execute(name, **(arguments or {}))
    except Exception:
        logger.warning(f"[mcp] tool '{name}' execution failed", exc_info=True)
        return {"error": f"Tool execution failed: {name}"}


# ---------- 单例装配 ----------

_registry: Optional[ToolRegistry] = None
_server: Optional[Server] = None
_session_manager: Optional[StreamableHTTPSessionManager] = None


def _get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_mcp_registry()
    return _registry


def _build_server() -> Server:
    server = Server("a-stock-tool-gateway")
    registry = _get_registry()

    @server.list_tools()
    async def _list_tools() -> List[types.Tool]:
        return [types.Tool(**d) for d in registry.to_mcp_tools()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        result = await run_in_threadpool(_dispatch, registry, name, arguments)
        text = json.dumps(result, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    return server


def _security_settings() -> Optional[TransportSecuritySettings]:
    """默认关闭 DNS rebinding 保护（部署在受信 Nginx 后，且有 API Key 网关）；
    可经 config 打开并配置 allowed_hosts/origins。"""
    from src.config import get_config
    cfg = get_config()
    enabled = bool(getattr(cfg, "mcp_dns_rebinding_protection", False))
    if not enabled:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h for h in str(getattr(cfg, "mcp_allowed_hosts", "")).split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts, allowed_origins=hosts,
    )


def get_mcp_session_manager() -> StreamableHTTPSessionManager:
    """单例 SessionManager（lifespan run() 与挂载 handle_request 共用同一个）。"""
    global _server, _session_manager
    if _session_manager is None:
        _server = _build_server()
        _session_manager = StreamableHTTPSessionManager(
            app=_server,
            event_store=None,
            json_response=True,   # 返回普通 JSON，规避 Cloudflare 对长连 SSE 的限制
            stateless=True,       # 网关无状态：每请求独立处理，利于反代/无会话保持
            security_settings=_security_settings(),
        )
    return _session_manager


def build_mcp_asgi_app():
    """返回带 API Key 鉴权的 ASGI 子应用，供 app.mount('/mcp', ...)。"""
    from api.mcp.auth import MCPAuthMiddleware, load_mcp_api_keys

    async def handle_streamable_http(scope, receive, send):
        await get_mcp_session_manager().handle_request(scope, receive, send)

    return MCPAuthMiddleware(handle_streamable_http, load_mcp_api_keys())
```

> 说明：`_call_tool` 用 `run_in_threadpool` 把同步 handler（含阻塞 I/O）移出事件循环。`build_mcp_asgi_app` 依赖 Task 6 的 `auth.py`，故 Task 6 完成前本模块的 `build_mcp_asgi_app` 不被调用；Task 5 的测试只覆盖 `build_mcp_registry`/`_dispatch`，不触发 auth import。

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_server.py -v`
Expected: PASS（7 passed）。

- [ ] **Step 6: Commit**

```bash
git add api/mcp/__init__.py api/mcp/server.py tests/test_mcp_server.py
git commit -m "feat: add MCP low-level server with reflected tool registry and dispatch"
```

---

## Task 6: api/mcp/auth.py — API Key ASGI 鉴权中间件

**Files:**
- Create: `api/mcp/auth.py`
- Test: `tests/test_mcp_auth.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_mcp_auth.py`：

```python
# -*- coding: utf-8 -*-
"""MCP API Key 鉴权中间件：无/错 key → 401；正确 key → 放行；未配置 → 拒绝。"""
import asyncio
import unittest

from api.mcp.auth import MCPAuthMiddleware, parse_api_keys


class _Spy:
    def __init__(self):
        self.called = False
    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(mw, headers):
    scope = {"type": "http", "headers": headers}
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(msg):
        sent.append(msg)
    asyncio.run(mw(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status


class TestParseKeys(unittest.TestCase):
    def test_parse_with_labels_and_plain(self):
        self.assertEqual(parse_api_keys("k1:alice, k2 ,"), {"k1", "k2"})
        self.assertEqual(parse_api_keys(""), set())


class TestAuthMiddleware(unittest.TestCase):
    def test_valid_key_passes(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [(b"authorization", b"Bearer secret1")])
        self.assertEqual(status, 200)
        self.assertTrue(spy.called)

    def test_missing_key_rejected(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [])
        self.assertEqual(status, 401)
        self.assertFalse(spy.called)

    def test_wrong_key_rejected(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [(b"authorization", b"Bearer nope")])
        self.assertEqual(status, 401)

    def test_unconfigured_denies_all(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, set())
        status = _run(mw, [(b"authorization", b"Bearer anything")])
        self.assertEqual(status, 401)
        self.assertFalse(spy.called)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'api.mcp.auth'`。

- [ ] **Step 3: 实现 auth.py**

创建 `api/mcp/auth.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 中转站 API Key 鉴权（ASGI 中间件）。

请求头 Authorization: Bearer <key>。Key 集合来自配置 mcp_api_keys，
格式 "key1:label1,key2:label2"（label 仅用于人读，鉴权只比对 key）。
未配置任何 key → 一律拒绝（安全默认，避免裸奔）。
"""
from __future__ import annotations

import json
import logging
from typing import Set

logger = logging.getLogger(__name__)


def parse_api_keys(raw: str) -> Set[str]:
    keys: Set[str] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key = item.split(":", 1)[0].strip()  # 去掉 :label
        if key:
            keys.add(key)
    return keys


def load_mcp_api_keys() -> Set[str]:
    from src.config import get_config
    cfg = get_config()
    return parse_api_keys(str(getattr(cfg, "mcp_api_keys", "") or ""))


class MCPAuthMiddleware:
    """包裹 MCP ASGI 子应用，做 Bearer key 校验。"""

    def __init__(self, app, api_keys: Set[str]):
        self.app = app
        self.api_keys = set(api_keys)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not self.api_keys or token not in self.api_keys:
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send):
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth.py -v`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add api/mcp/auth.py tests/test_mcp_auth.py
git commit -m "feat: add API key ASGI auth middleware for MCP gateway"
```

---

## Task 7: api/mcp/rate_limit.py — 限流扩展点（本期 no-op）

按 spec，本期不实现限流逻辑，仅留可插拔扩展点，避免将来改动牵动主路径。

**Files:**
- Create: `api/mcp/rate_limit.py`
- Test: `tests/test_mcp_auth.py`（追加一个用例，复用文件）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_mcp_auth.py` 顶部 import 处追加：

```python
from api.mcp.rate_limit import NoopRateLimiter
```

并在文件末尾 `if __name__` 之前追加：

```python
class TestRateLimitStub(unittest.TestCase):
    def test_noop_allows_everything(self):
        rl = NoopRateLimiter()
        self.assertTrue(rl.allow("any-key"))
        self.assertTrue(rl.allow(""))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth.py::TestRateLimitStub -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'api.mcp.rate_limit'`。

- [ ] **Step 3: 实现 rate_limit.py**

创建 `api/mcp/rate_limit.py`：

```python
# -*- coding: utf-8 -*-
"""MCP 中转站限流扩展点。

本期不做限流（spec 决定）。保留 NoopRateLimiter 作为占位与插拔点：
将来要保护 Tushare 积分 / TickFlow 60次/分钟配额时，替换为令牌桶实现并在
api/mcp/server.py 的派发前调用 .allow(key) 即可，无需改动主路径结构。
"""
from __future__ import annotations


class NoopRateLimiter:
    """永远放行。占位实现。"""

    def allow(self, api_key: str) -> bool:
        return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_auth.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add api/mcp/rate_limit.py tests/test_mcp_auth.py
git commit -m "chore: add no-op rate limiter extension point for MCP gateway"
```

---

## Task 8: 配置项 + .env.example

新增 `mcp_api_keys`、`mcp_dns_rebinding_protection`、`mcp_allowed_hosts` 三个配置字段。

> ⚠️ **关键约束（prd-reviewer 已核实 config.py 结构）**：`src/config.py` 的 `Config` 是 `@dataclass`，字段一律是**静态默认值**（如 `tushare_token: Optional[str] = None`），**没有任何字段在声明处写 `os.environ.get(...)`**。环境变量读取**集中在 `Config._load_from_env()` 的 `return cls(...)` 构造块里**逐字段 `os.getenv(...)` 注入；`get_config()` 走该路径才能拿到运行时 `data/.env` 的值。因此**必须两处都改**：①声明处加静态默认字段；②`_load_from_env` 的 `cls(...)` 加 `os.getenv` 注入。**绝不可只在声明处写 `os.environ.get`**——那样会在 import 时求值、读不到运行时 `data/.env`，且生产上 `/mcp` 全 401 或裸奔，而只断言 hasattr 的测试还会 PASS，把问题掩盖到生产。

**Files:**
- Modify: `src/config.py`（两处：dataclass 字段声明 + `_load_from_env` 的 `cls(...)`）
- Modify: `.env.example`
- Test: `tests/test_mcp_config.py`

- [ ] **Step 1: 定位两处锚点**

Run:
```bash
grep -nE "^class Config|def _load_from_env|def parse_env_bool|return cls\(|tushare_token" src/config.py | head -20
```
确认：①dataclass 字段区（找一个相邻的 `str`/`bool` 字段如 `tushare_token` 作锚点）；②`_load_from_env` 里 `return cls(` 的构造块（找相邻的 `os.getenv(...)` 行作锚点）；③`parse_env_bool` 辅助函数位置（约 L137，已被广泛使用，复用它解析 bool）。

- [ ] **Step 2: 写失败测试（断言真能从环境读到值，而非只 hasattr）**

创建 `tests/test_mcp_config.py`：

```python
# -*- coding: utf-8 -*-
"""确认 MCP 配置字段存在、有安全默认，且能从环境变量真实读取。"""
import unittest

from src.config import Config, get_config


class TestMcpConfigFields(unittest.TestCase):
    def test_fields_exist_with_safe_defaults(self):
        cfg = get_config()
        self.assertTrue(hasattr(cfg, "mcp_api_keys"))
        self.assertTrue(hasattr(cfg, "mcp_dns_rebinding_protection"))
        self.assertTrue(hasattr(cfg, "mcp_allowed_hosts"))
        self.assertFalse(bool(cfg.mcp_dns_rebinding_protection))  # 默认关闭

    def test_reads_from_environment(self):
        """漏改 _load_from_env 时此用例必失败（只改声明无法从环境读到值）。"""
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"MCP_API_KEYS": "k1:alice",
                                     "MCP_DNS_REBINDING_PROTECTION": "true"}):
            Config.reset_instance()
            try:
                cfg = get_config()
                self.assertEqual(cfg.mcp_api_keys, "k1:alice")
                self.assertTrue(bool(cfg.mcp_dns_rebinding_protection))
            finally:
                Config.reset_instance()  # 还原单例，避免污染其他测试


if __name__ == "__main__":
    unittest.main()
```

> 注：`Config.reset_instance()` 在 `src/config.py` 已存在（约 L2342）。Step 1 若发现实际方法名不同（如 `_reset`/`clear_instance`），以实际为准替换。

- [ ] **Step 3: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_config.py -v`
Expected: FAIL（字段不存在）。

- [ ] **Step 4: 在 src/config.py 两处同步添加（与现有 60+ 字段一致的模式）**

**4a. dataclass 字段区**（紧邻 `tushare_token` 等字符串字段，加静态默认值，**不写 os.environ.get**）：

```python
    # === MCP 工具中转站 ===
    mcp_api_keys: str = ""
    mcp_dns_rebinding_protection: bool = False
    mcp_allowed_hosts: str = ""
```

**4b. `_load_from_env` 的 `return cls(...)` 构造块**（紧邻其它 `os.getenv(...)` 注入行追加）：

```python
            mcp_api_keys=os.getenv('MCP_API_KEYS', ''),
            mcp_dns_rebinding_protection=parse_env_bool(
                os.getenv('MCP_DNS_REBINDING_PROTECTION'), default=False),
            mcp_allowed_hosts=os.getenv('MCP_ALLOWED_HOSTS', ''),
```

> `parse_env_bool` 是 config.py 现有辅助函数（Step 1 已定位），与其它 bool 字段同款用法；若其签名不是 `(value, default=...)`，以实际签名为准。

- [ ] **Step 5: 更新 .env.example**

在 `.env.example` 末尾追加（**不写真实 key**）：

```
# ===== MCP 工具中转站 =====
# 对外开放工具的 API Key（格式 key1:label1,key2:label2，label 仅人读；为空则 /mcp 一律 401）
MCP_API_KEYS=
# 是否开启 MCP 传输层 DNS rebinding/Host 校验（部署在受信 Nginx 后默认 false）
MCP_DNS_REBINDING_PROTECTION=false
# 开启校验时允许的 Host/Origin（逗号分隔），如 a-stock.tech-monthly.online
MCP_ALLOWED_HOSTS=
```

- [ ] **Step 6: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_config.py -v`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/config.py .env.example tests/test_mcp_config.py
git commit -m "feat: add MCP gateway config fields (api keys, transport security)"
```

---

## Task 9: api/app.py 挂载 /mcp + lifespan 集成

把 MCP ASGI 子应用挂到 `/mcp`，并把 `session_manager.run()` 接入现有 `app_lifespan`（SessionManager 生命周期必须随 app 启停）。

**Files:**
- Modify: `api/app.py`（`app_lifespan` 约 L172-186；`create_app` 路由注册约 L255 之后）
- Test: `tests/test_mcp_app_mount.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_mcp_app_mount.py`：

```python
# -*- coding: utf-8 -*-
"""确认 create_app 挂载了 /mcp 子应用。"""
import unittest

from api.app import create_app


class TestMcpMount(unittest.TestCase):
    def test_mcp_route_mounted(self):
        app = create_app()
        mounted = [getattr(r, "path", "") for r in app.routes]
        self.assertTrue(any(str(p).startswith("/mcp") for p in mounted),
                        f"/mcp not mounted; routes={mounted}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_app_mount.py -v`
Expected: FAIL（无 /mcp 路由）。

- [ ] **Step 3: 修改 app_lifespan 接入 session manager**

把 `api/app.py` 中现有 `app_lifespan`（L172-186）改为在 `yield` 外层进入 MCP session manager 的 `run()`：

```python
@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Initialize and release shared services for the app lifecycle."""
    app.state.system_config_service = SystemConfigService()
    _schedule_stock_index_background_refresh(app, "startup")
    from api.mcp.server import get_mcp_session_manager
    session_manager = get_mcp_session_manager()
    try:
        async with session_manager.run():
            yield
    finally:
        refresh_task = getattr(app.state, "stock_index_refresh_task", None)
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
        if hasattr(app.state, "system_config_service"):
            delattr(app.state, "system_config_service")
```

- [ ] **Step 4: 在 create_app 注册路由后挂载 /mcp**

在 `api/app.py` 的 `create_app` 中，现有 `app.include_router(api_v1_router)`（约 L255）之后、`add_error_handlers(app)` 之前，加入：

```python
    # MCP 工具中转站（挂在现有进程的 /mcp 路由，API Key 鉴权）
    from api.mcp.server import build_mcp_asgi_app
    app.mount("/mcp", build_mcp_asgi_app())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_app_mount.py -v`
Expected: PASS。

- [ ] **Step 6: 应用启动冒烟（lifespan 能正常进出）**

Run:
```bash
source .venv/bin/activate && python -c "
import asyncio
from api.app import create_app
app = create_app()
async def main():
    async with app.router.lifespan_context(app):
        print('lifespan entered ok')
asyncio.run(main())
print('lifespan exited ok')
"
```
Expected: 打印 `lifespan entered ok` 与 `lifespan exited ok`，无异常。

- [ ] **Step 7: 进程内端到端测试（验证真实 MCP initialize 穿过 auth+mount+BaseHTTPMiddleware+json_response 全链路）**

> 这是核心链路验证。现有 `AuthMiddleware` 是 `BaseHTTPMiddleware`（对非 `/api/v1/*` 放行，已确认不会拦 `/mcp`），但 `BaseHTTPMiddleware` 包裹挂载的原生 ASGI 子应用在某些版本对响应有兼容问题。不发真实 MCP 请求就判"完成"是完成幻觉——必须本地端到端验证，而非拖到 Task 11 生产环境。

在 `tests/test_mcp_app_mount.py` 追加（顶部 import 处加 `import os`）：

```python
class TestMcpInitializeE2E(unittest.TestCase):
    def test_initialize_through_full_chain(self):
        from unittest.mock import patch
        from starlette.testclient import TestClient
        from mcp.types import LATEST_PROTOCOL_VERSION
        from src.config import Config

        with patch.dict(os.environ, {"MCP_API_KEYS": "testkey"}):
            Config.reset_instance()
            try:
                app = create_app()  # 调用时 build_mcp_asgi_app() 读到 testkey
                init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}}
                hdr = {"Authorization": "Bearer testkey",
                       "Accept": "application/json, text/event-stream",
                       "Content-Type": "application/json"}
                with TestClient(app) as client:
                    r = client.post("/mcp", json=init, headers=hdr)
                    self.assertEqual(r.status_code, 200, f"chain broke: {r.status_code} {r.text[:300]}")
                    self.assertTrue("jsonrpc" in r.text or "result" in r.text, r.text[:300])
                    # 无 key 必须被鉴权中间件拦成 401（响应体为中间件固定体）
                    r401 = client.post("/mcp", json=init,
                                       headers={"Accept": "application/json, text/event-stream",
                                                "Content-Type": "application/json"})
                    self.assertEqual(r401.status_code, 401)
            finally:
                Config.reset_instance()
```

Run: `source .venv/bin/activate && python -m pytest tests/test_mcp_app_mount.py -v`
Expected: PASS（2 passed）。

**若此测试失败且定位到 `BaseHTTPMiddleware` 包裹/吞响应头问题**（典型表现：valid key 请求返回 500 或响应体被截断/缺 MCP 字段）——**回退方案**：不要乱改认证中间件逻辑；改为确保 `/mcp` 的 `Mount` 位于 `BaseHTTPMiddleware` 之外，即把 `app.mount("/mcp", ...)` 移到 `add_auth_middleware(app)`**之前**调用，或将 MCP 子应用包成独立 `Starlette(Mount(...))` 再 mount。把实际现象与采用的回退写入计划"风险点/未验证项"，必要时回报用户。

- [ ] **Step 8: Commit**

```bash
git add api/app.py tests/test_mcp_app_mount.py
git commit -m "feat: mount MCP gateway at /mcp and wire session manager lifespan"
```

---

## Task 10: 文档 + 全量回归

**Files:**
- Create: `docs/mcp-gateway.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 写使用文档**

创建 `docs/mcp-gateway.md`，内容覆盖：
- 端点：`https://a-stock.tech-monthly.online/mcp`（Streamable HTTP，json_response）。
- 鉴权：请求头 `Authorization: Bearer <MCP_API_KEYS 中的某个 key>`；未配置 key 则一律 401。
- 工具清单：30 个（指向 spec 第 5 节）。
- 配置：`data/.env` 的 `MCP_API_KEYS` / `MCP_DNS_REBINDING_PROTECTION` / `MCP_ALLOWED_HOSTS`。**`MCP_API_KEYS` 改动后必须 `docker compose -f docker/docker-compose.yml up -d` 重建容器才生效**——鉴权中间件在进程启动时把 key 集合固化进实例（`app = create_app()` 模块级实例化），**WebUI 设置页的热重载不会更新已固化的 MCP key**。
- 排除项：持仓/分析库/回测工具不对外。
- 不实现：限流（仅扩展点）。

- [ ] **Step 2: 更新 CHANGELOG（[Unreleased] 扁平格式）**

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段追加（每条独立一行，**不加 `###` 标题**）：

```
- [新功能] 新增 MCP 工具中转站（/mcp，Streamable HTTP），对外开放 30 个数据/新闻/分析工具供其他智能体零胶水调用，API Key 鉴权
- [新功能] DataFetcherManager 新增 7 个只读 granular 路由方法（财务三表/质押/回购/增减持/解禁），沿用既有数据源优先级链
- [文档] 新增 docs/mcp-gateway.md 与 MCP 配置项说明
```

- [ ] **Step 3: 全量后端回归**

Run: `source .venv/bin/activate && ./scripts/ci_gate.sh`
Expected: 通过（绿）。若 ci_gate 跑全量耗时长，至少先跑本特性相关测试：
```bash
source .venv/bin/activate && python -m pytest tests/test_registry_to_mcp.py tests/test_base_granular_routing.py tests/test_dataset_tools.py tests/test_mcp_server.py tests/test_mcp_auth.py tests/test_mcp_config.py tests/test_mcp_app_mount.py -v
```
Expected: 全部 PASS。

- [ ] **Step 4: 铁律烟雾自查（确认未触动既有数据源路由）**

Run: `source .venv/bin/activate && python -c "
from data_provider.base import DataFetcherManager
import inspect
src = inspect.getsource(DataFetcherManager.get_realtime_quote)
assert 'tickflow' in src.lower(), 'realtime 路由被改动！'
print('realtime 仍 TickFlow 优先, 铁律未触动')
"`
Expected: 打印确认信息。

- [ ] **Step 5: Commit**

```bash
git add docs/mcp-gateway.md docs/CHANGELOG.md
git commit -m "docs: add MCP gateway usage doc and changelog entries"
```

---

## Task 11（部署/运维，手动，需用户确认）— 上线 MCP 中转站

> 本任务改动生产服务器，**执行前需用户确认**。不属代码 commit 范畴。

- [ ] **Step 1: 服务器 data/.env 写入 API Key**

ssh 上服务器，在 `/opt/daily_stock_analysis/data/.env`（权限 600）追加 `MCP_API_KEYS=<为每个对接方生成的随机 key:label>`（key 用 `openssl rand -hex 24` 生成；**不回显明文、不写日志、不提交 git**）。注意：key 改动**必须靠 Step 2 重建容器生效**，WebUI 热重载不更新。

- [ ] **Step 2: 重建容器使配置生效**

```bash
cd /opt/daily_stock_analysis && docker compose -f docker/docker-compose.yml up -d
```

- [ ] **Step 3: 确认 Nginx 已代理 /mcp**

检查 `/etc/nginx/sites-available/stock.conf`：若现有 `location /` 已 `proxy_pass` 到 8000，则 `/mcp` 自动覆盖；否则新增 `location /mcp { proxy_pass http://127.0.0.1:8000; proxy_http_version 1.1; proxy_set_header Host $host; proxy_buffering off; }`。`nginx -t` 后 `systemctl reload nginx`。

- [ ] **Step 4: 生产端到端验收**

```bash
# 无 key 应 401，且响应体应为鉴权中间件固定体 {"error":"unauthorized"}
curl -s -w "\n%{http_code}\n" -X POST https://a-stock.tech-monthly.online/mcp
```
确认 401 的 body 是 `{"error":"unauthorized"}`（中间件返回），以区分"鉴权拦截 401"与"SDK 解析失败 4xx"——只有前者才证明鉴权链生效。
然后用一个真实 MCP 客户端（如 Claude/Cursor 配置该地址 + Bearer key）确认能发现 30 个工具并成功调用 `get_realtime_quote`。

- [ ] **Step 5: 更新维护记忆**

把"MCP 中转站本地分叉文件清单 + 同步上游须保留"写入项目自动记忆（参照 spec 第 10 节），并在维护专员 CLAUDE.md 的本地分叉小节登记。

---

## 风险点与开放问题（prd-reviewer 提出，执行/验收时关注）

**已通过计划内手段缓解的风险：**
- **BaseHTTPMiddleware 包裹 `/mcp` 子应用的兼容性** → Task 9 Step 7 进程内 initialize 端到端测试在本地即可暴露，并给了回退方案（把 mount 移到 auth 中间件之前）。
- **SDK 版本 API 漂移** → Task 1 Step 3 装饰器签名探针 + `mcp>=1.9.0,<2` 版本上限。
- **config 读取时机错误** → Task 8 重写为"声明 + `_load_from_env` 注入"两处同步 + 真读环境变量的测试。

**需用户决策的开放问题（AI 无法自判，验收前请确认）：**
1. **`json_response=True` + `stateless=True` 是否满足目标 MCP 客户端？** 选 json_response 是为规避 Cloudflare 对长连 SSE 的限制；但部分 MCP 客户端默认期望 SSE。Task 9 Step 7 的本地 initialize 测试能验证协议层可用，但**真实客户端（Claude/Cursor）兼容性需你在 Task 11 验收时确认**。若不兼容，回退为 `json_response=False`（SSE）并在 Nginx 关闭 `/mcp` 的 `proxy_buffering`。
2. **`mcp` 精确版本**：已钉 `>=1.9.0,<2`；若你希望完全可复现，Task 1 安装后把 `pip show mcp` 的实际版本号回填为精确 `==` 钉死。
3. **Nginx `/mcp` 是否一步到位关 `proxy_buffering`**：Task 11 Step 3 已给 `proxy_buffering off`（对 json_response 非必需，切回 SSE 时必需），保留即可，无需额外动作。

---

## Self-Review（写完计划后自查 + prd-reviewer 审查意见已吸收）

> **prd-reviewer 审查吸收记录（2026-06-03）**：3 个阻塞项（config 模式、A 股守卫、无重名断言）+ 4 个重要项（端到端测试、复用 manager 单例、装饰器探针、key 重建说明）+ 4 个改进项（精确 16 断言、dispatch 先查在不在、dragon_tiger 映射注记、curl 401 区分）+ 版本钉死，**全部已合入上文对应任务**；3 个开放问题列入上节供用户决策。

**1. Spec 覆盖：**
- MCP over Streamable HTTP 挂 /mcp → Task 5/9 ✓
- 注册表反射桥接（to_mcp_tool）→ Task 2 ✓
- 30 个工具（镜像 14 + 新增 16）→ Task 4 + Task 5（断言 len==30）✓
- 排除私有/回测工具 → Task 5（EXCLUDED + 不引入 ALL_BACKTEST_TOOLS）✓
- 7 个 base.py 薄路由方法（spec 第 0 节修订）→ Task 3 ✓
- get_price_percentile → get_risk_assessment（spec 第 0 节修订）→ Task 4 ✓
- API Key 鉴权 → Task 6 ✓
- 限流仅扩展点 → Task 7 ✓
- 配置 + .env.example + CHANGELOG → Task 8/10 ✓
- 铁律不触动 + ci_gate → Task 3/10 ✓
- 同步上游保留登记 → Task 11 Step 5 ✓

**2. Placeholder 扫描：** 各 code step 均含完整代码。Task 8 config 已逐行核实（`Config`@dataclass L602、`parse_env_bool` L137、`tushare_token` 两处模式 L621/L1421、`reset_instance` L2342）并给出精确两处改法，不再是示意。Task 3 的 `normalize_stock_code`(L68)/`_market_tag`(L206) 已核实存在。

**3. 类型一致性：** `to_mcp_tool()`/`to_mcp_tools()`、`build_mcp_registry()`/`_dispatch()`/`get_mcp_session_manager()`/`build_mcp_asgi_app()`、`MCPAuthMiddleware`/`parse_api_keys`/`load_mcp_api_keys`、`NoopRateLimiter.allow`、`ALL_DATASET_TOOLS`、`_handle_single_code_df` 等命名跨任务一致。
