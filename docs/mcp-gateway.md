# MCP 工具中转站

把本系统的数据 / 新闻 / 分析工具通过 **MCP（Model Context Protocol，Streamable HTTP）**
挂在现有 `stock-server` 进程的 `/mcp` 路由对外开放，供其他智能体（Claude / Cursor 等）
零胶水发现与调用。

> 关联设计：`docs/superpowers/specs/2026-06-03-mcp-tool-gateway-design.md`
> 实施计划：`docs/superpowers/plans/2026-06-03-mcp-tool-gateway.md`
> **面向大模型/Agent 的接入指南（30 工具目录、参数、工作流、调用示例）：[`mcp-integration-guide.md`](./mcp-integration-guide.md)**

## 端点

- **URL**：`https://a-stock.tech-monthly.online/mcp`
- **传输**：Streamable HTTP，`json_response=True`（返回普通 JSON，规避 Cloudflare 对长连 SSE 的限制）、`stateless=True`（无会话保持，利于反代）。
- **方法**：`POST /mcp`（JSON-RPC 2.0）。`/mcp` 与 `/mcp/` 均可——进程内有 `MCPPathNormalizerMiddleware` 在路由前把精确 `/mcp` 改写为 `/mcp/`，否则精确 `/mcp` 会落到前端 SPA 兜底路由返回 405。

## 鉴权

- 请求头：`Authorization: Bearer <key>`，`<key>` 取自配置 `MCP_API_KEYS`。
- **未配置任何 key → 一律 401**（安全默认，避免裸奔）。
- 鉴权失败响应体固定为 `{"error":"unauthorized"}`（由 ASGI 鉴权中间件返回），可借此区分「鉴权拦截 401」与「SDK 解析失败 4xx」。

## 配置（`data/.env`）

| 变量 | 说明 | 默认 |
|------|------|------|
| `MCP_API_KEYS` | 开放工具的 API Key，格式 `key1:label1,key2:label2`（label 仅人读，鉴权只比对 key）；为空则 `/mcp` 一律 401 | 空 |
| `MCP_DNS_REBINDING_PROTECTION` | 是否开启 MCP 传输层 DNS rebinding / Host 校验（部署在受信 Nginx 后默认 false） | `false` |
| `MCP_ALLOWED_HOSTS` | 开启校验时允许的 Host/Origin（逗号分隔），如 `a-stock.tech-monthly.online` | 空 |

> ⚠️ **`MCP_API_KEYS` 改动后必须重建容器才生效**：
> ```bash
> cd /opt/daily_stock_analysis && docker compose -f docker/docker-compose.yml up -d
> ```
> 鉴权中间件在进程启动时（`app = create_app()` 模块级实例化）把 key 集合固化进实例，**WebUI 设置页的热重载不会更新已固化的 MCP key**。

## 开放工具（30 个）

镜像现有 Agent 工具 14 个 + 新增数据工具 16 个，全部经 `DataFetcherManager` 取数，
继承数据源铁律优先级（TickFlow/Tushare 先、akshare 末位）。

- **镜像（14）**：`get_realtime_quote`、`get_daily_history`、`get_chip_distribution`、`get_stock_info`、`get_capital_flow`、`analyze_trend`、`calculate_ma`、`get_volume_analysis`、`analyze_pattern`、`get_intraday_volume`、`get_market_indices`、`get_sector_rankings`、`search_stock_news`、`search_comprehensive_intel`。
- **新增数据（16）**：`get_income_statement`、`get_cashflow_statement`、`get_financial_indicators`、`get_pledge_detail`、`get_holder_trade`、`get_share_float`、`get_repurchase`、`get_dragon_tiger`、`get_risk_assessment`、`get_stock_sectors`、`get_intraday_kline`、`get_order_book`、`get_limit_up_pool`、`get_hot_stocks`、`get_concept_rankings`、`get_market_stats`。

完整字段与映射见 spec 第 5 节。

## 排除项（不对外）

私有 / 持仓 / 分析库 / 回测类工具一律排除：
`get_portfolio_snapshot`、`get_analysis_context`、`get_stock_backtest_summary`、
`get_skill_backtest_summary`、`get_strategy_backtest_summary`。

## 限流

本期不实现，仅留可插拔扩展点 `api/mcp/rate_limit.py:NoopRateLimiter`。将来要保护
Tushare 积分 / TickFlow 配额时替换为令牌桶并在派发前调用 `.allow(key)`，无需改主路径。

## 客户端示例（curl 探活）

```bash
# 无 key → 401 {"error":"unauthorized"}
curl -s -w "\n%{http_code}\n" -X POST https://a-stock.tech-monthly.online/mcp

# 带 key 的 initialize（应 200 返回 JSON-RPC result）
curl -s -X POST https://a-stock.tech-monthly.online/mcp \
  -H "Authorization: Bearer <your-key>" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## 实现要点

- 注册表反射桥接：复用 `ToolDefinition` 注册表为单一真相源，`to_mcp_tool()` 反射成 MCP 工具，低层 `mcp.server.lowlevel.Server` 的 `list_tools/call_tool` 按名字派发到现有 handler。
- 不触碰 `data_provider/` 的数据源路由 / 优先级 / fetcher 链；新增的 7 个 granular 路由方法（财务三表/质押/回购/增减持/解禁）沿用既有 `_fetchers` 优先级链，只读 pass-through。
