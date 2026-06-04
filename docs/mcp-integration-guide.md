# MCP 接入指南（面向大模型 / Agent）

> 本指南让任意支持 MCP 的大模型/Agent **快速、正确地用上中转站的全部 30 个工具**。
> 配套：部署与运维见 [`mcp-gateway.md`](./mcp-gateway.md)。

---

## 0. 一分钟接入

| 项 | 值 |
|----|----|
| **端点** | `https://a-stock.tech-monthly.online/mcp` |
| **协议** | MCP **Streamable HTTP**（`POST`，JSON-RPC 2.0，返回普通 JSON，无状态） |
| **鉴权** | 请求头 `Authorization: Bearer <你的-MCP-API-KEY>`（缺失/错误一律 **401**） |
| **工具数** | **30**（数据/分析/行情/搜索；不含持仓、分析库、回测等私有工具） |

`/mcp` 与 `/mcp/` 均可（服务端会自动归一化）。

> **获取/重置 Key**：登录 WebUI →左侧 **「密钥」页**，可查看当前生效 Key、一键复制连接配置、随时**重置**（重置后旧 Key 立即失效、新 Key 立即生效，无需重建容器）。

### 客户端配置示例

**Claude Desktop / Cursor 等（HTTP MCP）** — 在 `mcpServers` 中加入：

```json
{
  "mcpServers": {
    "a-stock": {
      "type": "streamable-http",
      "url": "https://a-stock.tech-monthly.online/mcp",
      "headers": { "Authorization": "Bearer <你的-MCP-API-KEY>" }
    }
  }
}
```

> 不同客户端字段名略有差异（`type` 可能是 `http`/`streamableHttp`/`url` 直填）。核心只有两点：**URL 指向 `/mcp`** + **带 `Authorization: Bearer` 头**。

**裸 curl 探活**：

```bash
# 1) 握手（应 200，返回 serverInfo: a-stock-tool-gateway）
curl -s -X POST https://a-stock.tech-monthly.online/mcp \
  -H "Authorization: Bearer <KEY>" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# 2) 列出全部 30 个工具
curl -s -X POST https://a-stock.tech-monthly.online/mcp \
  -H "Authorization: Bearer <KEY>" \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 3) 调用一个工具（实时报价）
curl -s -X POST https://a-stock.tech-monthly.online/mcp \
  -H "Authorization: Bearer <KEY>" \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_realtime_quote","arguments":{"stock_code":"600519"}}}'
```

---

## 1. 调用前必读（避免踩坑）

1. **股票代码 `stock_code` 格式**：
   - A 股：纯 6 位数字，如 `600519`（贵州茅台）、`000001`、`300750`。
   - 港股：`hk` 前缀，如 `hk00700`（腾讯）。
   - 美股：股票符号，如 `AAPL`、`TSLA`。
2. **多数细分工具仅支持 A 股**（财务三表、质押、龙虎榜、筹码、盘中、资金流等）。给非 A 股代码会**优雅返回**（`{"info": ...}` 而非报错），不会崩。下表「市场」列标注 `A股` 的即为 A 股专属。
3. **搜索类工具需同时给 `stock_code` 和 `stock_name`**（两者都必填），否则检索不准。
4. **返回约定**（所有工具的 `tools/call` 结果是一段 JSON 文本）：
   - 有数据：`{"stock_code": "...", "items": [...]}` 或工具特定结构（见下）。
   - 无数据：`{"info": "..."}`（正常空结果，**不是错误**）。
   - 取数失败：`{"error": "..."}`。
5. **无需轮询/会话保持**：无状态，每次 `tools/call` 独立返回。
6. **数据时效**：盘中工具（实时报价、五档、盘中量能、分钟 K）在交易时段最有意义；非交易时段返回最近可得数据。
7. **省 token**：财务/历史类返回最多 60 行；需要更早数据时用日期参数（如 `get_repurchase` 的 `start_date`）。

---

## 2. 工具总览（30 个）

> 标 `*` 为必填参数；`枚举` 给出可选值；市场 `A股`=A股专属、`A/港/美`=多市场、`全市场`=不针对单只股。

### 2.1 行情与基础数据

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `get_realtime_quote` | 实时报价（价/涨跌幅/量比/换手/PE/PB/市值） | `stock_code*` | A/港/美 |
| `get_daily_history` | 日 K（OHLCV + MA5/10/20），最近 N 天 | `stock_code*`, `days`(默认60) | A/港/美 |
| `get_stock_info` | 基本面摘要（估值/成长/盈利/机构流向/所属板块/板块排名），紧凑省 token | `stock_code*` | A/港/美 |
| `get_chip_distribution` | 筹码分布（获利比例/平均成本/90%·70%集中度） | `stock_code*` | A股 |
| `get_capital_flow` | 主力资金流（今日/5日/10日净流入 + 板块资金榜） | `stock_code*` | A股 |
| `get_stock_sectors` | 个股所属板块/概念列表 | `stock_code*` | A股 |
| `get_risk_assessment` | 风险与估值评估（估值水平/历史分位/风险信号） | `stock_code*` | A股 |

### 2.2 盘中实时（A 股专属，交易时段用）

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `get_intraday_volume` | 盘中 5 分钟实时量能（最新 bar 量比、放量/缩量/正常判定、今日累计量、最近几根 bar） | `stock_code*` | A股 |
| `get_intraday_kline` | 分钟级 K 线 | `stock_code*`, `period`(枚举 `5m\|15m\|30m\|60m`,默认5m), `count`(默认240) | A股 |
| `get_order_book` | 五档盘口（买卖五档量价） | `stock_code*` | A股 |

### 2.3 技术分析

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `analyze_trend` | 综合技术面（MA 排列/乖离/MACD/RSI/量价/支撑阻力 + 0-100 买卖评分） | `stock_code*` | A/港/美 |
| `calculate_ma` | 均线（MA5..250 或自定义周期）值、乖离%、是否站上、多空排列 | `stock_code*`, `periods`(默认`5,10,20,30,60,120,250`), `days`(默认120) | A/港/美 |
| `analyze_pattern` | K 线/形态识别（十字星/锤子/启明星/吞没/双底/突破/箱体震荡…，含方向与强度） | `stock_code*`, `days`(默认60) | A/港/美 |
| `get_volume_analysis` | 量价关系（量比、涨跌日均量、放/缩量趋势、量价配合/背离） | `stock_code*`, `days`(默认30) | A/港/美 |

### 2.4 财务（A 股专属，按报告期）

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `get_income_statement` | 利润表 | `stock_code*` | A股 |
| `get_cashflow_statement` | 现金流量表 | `stock_code*` | A股 |
| `get_financial_indicators` | 财务指标（ROE/毛利率/资产负债率等） | `stock_code*` | A股 |

### 2.5 股东 / 股本事件（A 股专属）

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `get_dragon_tiger` | 龙虎榜上榜信息（含机构席位、买卖额） | `stock_code*` | A股 |
| `get_pledge_detail` | 股权质押明细 | `stock_code*` | A股 |
| `get_holder_trade` | 股东增减持记录 | `stock_code*` | A股 |
| `get_share_float` | 限售解禁明细 | `stock_code*` | A股 |
| `get_repurchase` | 股份回购记录（可选起止日 YYYYMMDD） | `stock_code*`, `start_date`, `end_date` | A股 |

### 2.6 全市场 / 板块

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `get_market_stats` | 全市场涨跌家数（涨/跌/平/涨停/跌停/成交额） | 无 | 全市场 |
| `get_market_indices` | 主要指数（沪深/标普/纳指等） | `region`(枚举 `cn\|hk\|us`,默认cn) | 全市场 |
| `get_sector_rankings` | 板块/行业涨跌榜（前 N 与后 N） | `top_n`(默认10) | 全市场 |
| `get_concept_rankings` | 概念/题材涨跌榜（领涨 top + 领跌 bottom） | `n`(默认5) | 全市场 |
| `get_hot_stocks` | 市场人气股榜 | `n`(默认10) | 全市场 |
| `get_limit_up_pool` | 当日涨停池 / 连板梯队 | `date`(YYYYMMDD,默认当日), `n`(默认20) | 全市场 |

### 2.7 新闻 / 情报搜索

| 工具 | 用途 | 参数 | 市场 |
|------|------|------|------|
| `search_stock_news` | 个股最新新闻（标题/摘要/来源/URL） | `stock_code*`, `stock_name*` | A/港/美 |
| `search_comprehensive_intel` | 多维情报（新闻+市场分析+风险核查+业绩展望+行业趋势），返回成文报告 | `stock_code*`, `stock_name*` | A/港/美 |

---

## 3. 推荐工作流（让模型一步到位）

> 按意图选「工具链」，避免无效来回。

- **「全面分析某只股」** → `get_realtime_quote` + `get_stock_info` + `analyze_trend` + `get_capital_flow`(A股) + `search_comprehensive_intel`（需 name）。
- **「这只股现在能不能买/技术面」** → `analyze_trend`（拿评分）→ 不足再 `calculate_ma` + `analyze_pattern` + `get_volume_analysis`。
- **「盘中异动/现在量能如何」**（交易时段，A股） → `get_intraday_volume` + `get_order_book` + `get_intraday_kline`。
- **「基本面/财务体检」**（A股） → `get_financial_indicators` + `get_income_statement` + `get_cashflow_statement` + `get_risk_assessment`。
- **「筹码/主力/资金」**（A股） → `get_chip_distribution` + `get_capital_flow` + `get_dragon_tiger`。
- **「股东行为/风险事件」**（A股） → `get_holder_trade` + `get_pledge_detail` + `get_share_float` + `get_repurchase`。
- **「今天大盘/题材怎么样」** → `get_market_stats` + `get_market_indices` + `get_sector_rankings` + `get_concept_rankings` + `get_limit_up_pool` + `get_hot_stocks`。
- **「某股最新消息」** → `search_stock_news`（务必同时给 `stock_code` 与 `stock_name`）。

**小贴士**：需要 `stock_name` 而手上只有代码时，可先 `get_stock_info`/`get_realtime_quote` 拿到名称，再喂给搜索工具。

---

## 4. tools/call 调用模板

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "<工具名>",
    "arguments": { "<参数名>": <值> }
  }
}
```

示例：

```jsonc
// 分钟 K（30 分钟，最近 100 根）
{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{
  "name":"get_intraday_kline","arguments":{"stock_code":"600519","period":"30m","count":100}}}

// 回购（指定区间）
{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{
  "name":"get_repurchase","arguments":{"stock_code":"600519","start_date":"20250101","end_date":"20250601"}}}

// 个股新闻（必须带 name）
{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{
  "name":"search_stock_news","arguments":{"stock_code":"600519","stock_name":"贵州茅台"}}}

// 概念榜（领涨/领跌各 10）
{"jsonrpc":"2.0","id":14,"method":"tools/call","params":{
  "name":"get_concept_rankings","arguments":{"n":10}}}
```

---

## 5. 错误与边界

| 现象 | 含义 | 处理 |
|------|------|------|
| HTTP **401** `{"error":"unauthorized"}` | key 缺失/错误 | 检查 `Authorization: Bearer` 头与 key 是否正确 |
| 结果含 `{"info": ...}` | 正常空结果（如非交易日、非 A 股调 A 股专属工具、无该数据） | 视为「无数据」，不要当失败重试 |
| 结果含 `{"error": ...}` | 取数失败（数据源临时不可用等） | 可少量重试或换工具；多数有降级兜底 |
| 工具名不存在 | `{"error":"Unknown tool: ..."}` | 先 `tools/list` 核对名称 |

**注意**：本期**未做限流**，但大量高频调用会消耗后端 Tushare 积分 / TickFlow 配额，请合理批量、避免风暴式请求。

---

## 6. 速记（给模型的最短规则）

- 端点 `…/mcp`，头 `Authorization: Bearer <KEY>`，JSON-RPC `tools/call`。
- 代码：A股 `600519` / 港股 `hk00700` / 美股 `AAPL`。
- 财务·盘中·龙虎榜·筹码·资金流·股东事件 = **仅 A 股**。
- 搜索类 = **code + name 都要给**。
- 返回 `items`=有数据、`info`=空、`error`=失败。
