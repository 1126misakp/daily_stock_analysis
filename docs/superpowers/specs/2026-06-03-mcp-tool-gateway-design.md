# MCP 工具中转站设计文档

- **日期**：2026-06-03
- **分支**：`feat/mcp-tool-gateway`
- **状态**：设计已确认，待写实施计划
- **作者**：维护专员（与用户 brainstorming 共同定稿）

---

## 1. 背景与目标

### 1.1 用户诉求

把本项目（a-stock.tech-monthly.online 股票分析系统）已有的**数据接口、新闻接口和工具**，通过在线开放接口暴露给**其他智能体**直接调用。核心初衷：**其他智能体不必再各自重写调用脚本**，本项目充当一个"能力中转站"。

### 1.2 为什么用 MCP

"让其他智能体直接发现并调用工具、零胶水代码"正是 **MCP（Model Context Protocol）** 的设计目标。对接方（Claude / Cursor / 各类 Agent 框架）只需配置一个地址即可自动发现全部工具并调用，无需为每个接口手写请求代码。这比裸 REST 更贴合诉求（REST 仍需对接方按 OpenAPI 文档写请求代码）。

### 1.3 关键约束

1. **不新增服务器/容器/端口/域名**：MCP over Streamable HTTP，作为 ASGI 子应用挂载到现有 `stock-server` 进程的 `/mcp` 路由，复用现有 Nginx → Cloudflare → 8000 链路与全部基建。
2. **数据源铁律不可触碰**：本中转站只在数据源路由"之上"加一层调用入口，**完全不改动** `data_provider` 的 TickFlow/Tushare 优先、akshare 末位体系（见第 6 节合规检查）。
3. **配额可控**：每次调用消耗 Tushare 积分与 TickFlow 配额（限流 60 次/分钟），故需鉴权（API Key）区分调用方。限流本期不做，仅留扩展点。
4. **不暴露私有数据**：用户持仓、本地分析库、私有回测战绩一律排除。

---

## 2. 整体架构与数据流

```
外部智能体 (Claude / Cursor / 任意 Agent)
   │  MCP over Streamable HTTP + Authorization: Bearer <API_KEY>
   ▼
Cloudflare(橙云) ──HTTPS──> Nginx ──> stock-server 容器:8000
                                          │
                                  ┌───────┴────────┐
                                  │  FastAPI app    │
                                  │   /            WebUI                       │
                                  │   /api/v1/*    现有 REST（登录 cookie 保护）  │
                                  │   /mcp     ←─  新增 MCP 挂载                  │
                                  │               (ASGI 子应用, API Key 保护)     │
                                  └───────┬────────┘
                                          ▼
                            ToolRegistry（单一真相源）
                       现有工具(镜像) + 新增 16 个精选数据工具
                                          ▼
                          DataFetcherManager / search_service / 等底层
                       （TickFlow / Tushare 优先体系 —— 完全不动）
```

要点：
- MCP 入口与现有 WebUI / `/api/v1/*` 是**同一进程的不同路由**，不新增容器/端口/域名。
- 所有股票数据工具最终都经 `DataFetcherManager` 取数，自动继承数据源铁律的优先级。
- MCP 层是**薄反射层**：复用 `ToolDefinition` 注册表，行为与内部 Agent 调用完全一致。

---

## 3. 实现路线选择

| 路线 | 做法 | 取舍 | 决定 |
|------|------|------|------|
| **A. 注册表反射桥接** | 给 `ToolDefinition` 加 `to_mcp_tool()`；MCP server 自动把"选定工具"反射成 MCP 工具，按名字派发到现有 handler。新增数据工具也包装成 `ToolDefinition` 进同一注册表 | 单一真相源、几乎零重复、内部 Agent 将来也能复用新工具。最省、最不易漂移 | **✅ 采用** |
| B. 独立 MCP 进程/容器 | 单起进程 import handler | 隔离性好，但多容器/进程、多运维，违背"复用现有基建" | 否决 |
| C. 逐个手写 MCP 工具 | 每个工具手写 schema | 直白但和注册表重复、易漂移 | 否决 |

---

## 4. 组件拆分（单一职责）

1. **`ToolDefinition.to_mcp_tool()`**（改 `src/agent/tools/registry.py`）
   把工具转成 MCP 工具 schema，复用已有的 `_params_json_schema()`。纯函数、可单测。

2. **精选数据工具包**（新增 `src/agent/tools/dataset_tools.py`）
   把第 5.2 节确认开放的 16 个 `data_provider` 方法各包装成一个带说明的 `ToolDefinition`，注册进表。所有取数仍调 `DataFetcherManager.get_X()`，不绕过 manager。

3. **MCP server 装配**（新增 `api/mcp/server.py`）
   用官方 `mcp` Python SDK 低层 `Server`：
   - `@server.list_tools()` 返回"白名单内"工具反射出的 MCP 工具清单；
   - `@server.call_tool()` 按名字查 handler、执行、回传。
   - **白名单可配置**，控制对外开放的工具集（即第 5 节 30 个）。

4. **API Key 鉴权中间件**（新增 `api/mcp/auth.py`）
   校验 `Authorization: Bearer <token>`，识别是哪个 key（用于日志、将来限流），无效则 401。Key 集合从配置读取（见第 7 节）。

5. **限流扩展点**（新增 `api/mcp/rate_limit.py`，**本期仅留接口骨架，不实现逻辑**）
   预留每 Key 令牌桶位置，保护 Tushare 积分与 TickFlow 60 次/分钟配额。本期为空实现/直通，后续按需启用。

6. **挂载点**（改 `api/app.py` 的 `create_app`，约 L255 附近）
   `app.mount("/mcp", <MCP ASGI app>)`，套上组件 4（鉴权）与组件 5（限流直通）两层。

### 依赖
- 新增依赖：官方 `mcp` Python SDK（项目当前无任何 MCP 依赖）。按全局规范装在项目 `.venv` 内。

---

## 5. MCP 工具目录（最终 30 个）

### 5.1 镜像现有工具（14 个）

**行情/数据（6）**
| 工具 | 功能 | 取数对象 |
|------|------|---------|
| `get_realtime_quote` | 实时报价（价/涨跌幅/量比/量） | 调用方传入的股 |
| `get_daily_history` | 日 K 历史 OHLCV | 调用方传入的股 |
| `get_capital_flow` | 个股资金流 | 调用方传入的股 |
| `get_chip_distribution` | 筹码分布（获利比例/成本/集中度） | 调用方传入的股 |
| `get_stock_info` | 基本面（估值/成长/盈利/机构资金） | 调用方传入的股 |
| `get_intraday_volume` | 盘中 5 分钟实时量能画像（放量/缩量） | 调用方传入的股 |

**分析（4）**：`analyze_trend` 趋势分析 / `calculate_ma` 均线 / `get_volume_analysis` 量价分析 / `analyze_pattern` 形态识别 —— 均按调用方传入的股，现抓历史再算。

**市场（2）**：`get_market_indices` 大盘指数 / `get_sector_rankings` 板块排名 —— 全市场公开。

**搜索/新闻（2）**：`search_stock_news` 个股新闻 / `search_comprehensive_intel` 多维情报。

### 5.2 新增数据工具（16 个，均按调用方传入的股 / 全市场公开）

| 拟新增工具 | 底层 manager 方法 | 功能 |
|-----------|------------------|------|
| `get_income_statement` | `get_income_statement` | 利润表 |
| `get_cashflow_statement` | `get_cashflow_statement` | 现金流量表 |
| `get_financial_indicators` | `get_fina_indicator` | 财务指标（ROE/毛利率/负债率等） |
| `get_dragon_tiger` | `get_top_list` / `get_top_inst` | 龙虎榜（含机构席位） |
| `get_limit_up_pool` | `get_limit_up_pool` | 当日涨停池/连板梯队 |
| `get_hot_stocks` | `get_hot_stocks` | 市场人气股榜 |
| `get_concept_rankings` | `get_concept_rankings` | 概念/题材涨跌榜 |
| `get_stock_sectors` | `get_belong_boards` | 个股所属板块/概念 |
| `get_market_stats` | `get_market_stats` | 全市场涨跌家数统计 |
| `get_intraday_kline` | `get_intraday_kline` | 分钟 K（5/15/30/60m） |
| `get_order_book` | `get_order_book` | 五档盘口 |
| `get_price_percentile` | `get_percentile_price` | 价格历史分位（估值高低） |
| `get_pledge_detail` | `get_pledge_detail` | 股权质押明细 |
| `get_repurchase` | `get_repurchase` | 股份回购 |
| `get_holder_trade` | `get_holder_trade` | 股东增减持 |
| `get_share_float` | `get_share_float` | 限售解禁 |

### 5.3 明确排除（不进 MCP）

| 工具 | 排除理由 |
|------|---------|
| `get_portfolio_snapshot` | 用户私有持仓 |
| `get_analysis_context` | 用户私有分析库 |
| `get_stock_backtest_summary` | 读用户 DB 预算回测记录；对外部传入的股查无数据，只会误导 |
| `get_skill_backtest_summary` | 用户私有战绩（技能维度统计） |
| `get_strategy_backtest_summary` | 用户私有战绩（整体统计） |

> **关于回测**：现有回测系统的本质是"事后验证服务器自己产出的分析建议"（`BacktestService.run_backtest` 依赖 DB 中已有的 `analysis_history` 记录），**无法**对任意股+策略从零模拟。"按需回测任意股"是一个独立的中等规模新功能（可复用自研选股引擎的 8 个量化 scorer 套到任意股历史上生成信号），将作为**紧接着的下一个 spec** 单独设计，不纳入本中转站。

---

## 6. 数据源铁律合规检查（备查）

**核心结论：30 个工具全部合规。** 所有股票数据工具都经 `DataFetcherManager`（`data_provider`）取数，由 manager 强制 TickFlow/Tushare 优先、akshare 末位兜底。**没有任何工具绕过 manager 直连 akshare。**

`data_provider/base.py` 结构已核实：每个能力方法在 base.py 出现两次——`BaseFetcher`（L353）的抽象 stub（`return None`）+ `DataFetcherManager`（L616）的真实路由。manager 路由版**全部遍历 `self._fetchers` 优先级链**（顺序即铁律顺序：TickFlow priority=-2 → Tushare -1 → efinance → akshare 末位），部分（`get_main_indices` L2509、`get_market_stats` L2533）还显式 `_get_tickflow_fetcher()` 优先。

### 工具 → 接口 → 主力源映射

**🟢 TickFlow 主力**
| 工具 | manager 方法 | 源优先级 |
|------|-------------|---------|
| `get_realtime_quote` | `get_realtime_quote` | **TickFlow** → tencent → akshare |
| `get_daily_history` + 4 个分析工具 | `get_daily_data` | **TickFlow(-2)** → Tushare(-1) → efinance → akshare |
| `get_intraday_volume`、`get_intraday_kline` | `get_intraday_kline` | **TickFlow** 专属 |
| `get_order_book` | `get_order_book` | **TickFlow** 五档 |
| `get_market_indices` | `get_main_indices` | **TickFlow 优先** → _fetchers |
| `get_market_stats` | `get_market_stats` | **TickFlow 优先** → _fetchers |
| `get_limit_up_pool` | `get_limit_up_pool` | _fetchers（TickFlow 有实现，优先；akshare 兜底） |

**🔵 Tushare 主力**
| 工具 | manager 方法 | 源 |
|------|-------------|----|
| `get_capital_flow` | `get_capital_flow_context` | **Tushare** → akshare |
| `get_chip_distribution` | `get_chip_distribution` | **Tushare** cyq_chips → akshare |
| `get_stock_info` | `get_fundamental_context` + `get_belong_boards` | **Tushare** |
| `get_income_statement` / `get_cashflow_statement` / `get_financial_indicators` | tushare_fetcher | **Tushare** |
| `get_dragon_tiger` | `get_top_list` / `get_top_inst` | **Tushare** |
| `get_price_percentile` | `get_percentile_price` | **Tushare** |
| `get_pledge_detail` / `get_repurchase` / `get_holder_trade` / `get_share_float` | tushare_fetcher | **Tushare** |
| `get_stock_sectors` | `get_belong_boards` | **Tushare** 所属板块 → akshare |

**🟡 _fetchers 链路由（TickFlow/Tushare 先，akshare 末位）**
| 工具 | manager 方法 | 说明 |
|------|-------------|------|
| `get_sector_rankings` | `get_sector_rankings` | _fetchers 链 |
| `get_concept_rankings` | `get_concept_rankings` | _fetchers 链；实务上常由 akshare 兜底应答 |
| `get_hot_stocks` | `get_hot_stocks` | 同上 |

**⚪ 非股票数据源（铁律不管辖）**
| 工具 | 底层 | 说明 |
|------|------|------|
| `search_stock_news`、`search_comprehensive_intel` | `search_service` | 新闻搜索 Bocha/Tavily/Anspire，与行情数据源体系无关 |

### 需如实点出的一处
`get_hot_stocks`、`get_concept_rankings` 这两个能力，实务上很可能**只有 akshare 实现**（TickFlow/Tushare 不提供人气股/概念榜），故 akshare 是事实上的应答源。这**不违反铁律**：①仍走 manager 优先级链；②不存在"为 akshare 而把更高优先的 Tushare/TickFlow 降级"——根本没有更高优先的等价源，属铁律明确允许的"末位兜底"，非降级。

---

## 7. 鉴权与配置

- **鉴权方式**：每个对接方一个 API Key，请求头 `Authorization: Bearer <token>`。无效/缺失 → 401。
- **Key 存储**：复用项目配置单一真相源 `data/.env`（权限 600），新增配置项存放允许的 Key 集合（具体字段名与格式在实施计划中定，倾向 `MCP_API_KEYS=key1:label1,key2:label2` 便于区分调用方用于日志/将来限流）。改 Key 后按现有流程 `docker compose up -d` 重建生效。
- **敏感信息纪律**：Key 不回显明文、不写日志、不提交 git。

---

## 8. 错误处理

- 工具 handler 已有的降级容错（Tushare 403 → 腾讯、akshare 失败 → Tushare、数据缺失返回 `{"info": ...}` / `{"error": ...}`）原样保留，MCP 层透传。
- MCP 层只负责：鉴权失败 401；工具名不存在 → MCP 标准错误；handler 抛异常 → 捕获并返回结构化错误，不泄露内部堆栈。

---

## 9. 测试策略

沿用项目风格（`unittest.TestCase` + `MagicMock`/`patch`）：
1. **`to_mcp_tool()` 单测**：参数 schema 转换正确（含 enum/required/default）。
2. **反射清单单测**：白名单过滤后，`list_tools` 恰好返回 30 个、且排除项不出现。
3. **派发单测**：`call_tool` 按名字正确路由到 handler，参数透传、返回透传。
4. **新增 16 个数据工具单测**：mock manager 方法，验证 handler 调的是 `manager.get_X()`（不绕过 manager）、返回结构正确。
5. **鉴权单测**：无 Key/错误 Key → 401，正确 Key → 放行。
6. **铁律回归**：保留并通过 `./scripts/ci_gate.sh`，确认实时/日 K 仍 TickFlow、各 Tushare 能力仍优先、akshare 仍末位。

---

## 10. 同步上游注意（写入维护记忆）

本中转站新增以下文件/改动，同步上游（`ZhuLinsen/daily_stock_analysis`）时须二开侧优先、手工合并保留：
- 新增：`api/mcp/`（server/auth/rate_limit）、`src/agent/tools/dataset_tools.py`。
- 改动：`src/agent/tools/registry.py`（`to_mcp_tool()`）、`api/app.py`（`/mcp` 挂载）、`data/.env`（`MCP_API_KEYS`）、依赖清单（新增 `mcp`）。
- 与数据源铁律、自研选股引擎一样，属本地分叉，需在 CLAUDE.md / 自动记忆登记。

---

## 11. 非目标（YAGNI）

- 不做限流逻辑（仅留扩展点）。
- 不做按需回测引擎（独立 spec）。
- 不暴露任何写操作/触发分析的工具（本期纯只读数据/新闻/分析）。
- 不暴露用户私有数据（持仓/分析库/回测战绩）。
- 不新增独立 MCP 进程/容器/域名。
