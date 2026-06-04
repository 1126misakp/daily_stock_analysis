# MCP 密钥管理（前端 + 动态生效）设计

> 状态：已与用户确认，待转实施计划。
> 关联：[`docs/mcp-gateway.md`](../../mcp-gateway.md)、[`docs/mcp-integration-guide.md`](../../mcp-integration-guide.md)、MCP 中转站设计 `2026-06-03-mcp-tool-gateway-design.md`。

## 1. 目标与范围

在 WebUI（`a-stock.tech-monthly.online`）左侧导航新增「密钥」入口，提供 MCP 中转站 `/mcp` 的**单一受管 API Key** 的查看与重置；重置后**旧 key 立即失效、新 key 立即生效，无需重建容器**（规则同商用大模型 API Key）。

**范围内**：单一 key + 查看 + 重置；动态鉴权（每请求读当前 key）；端点地址与连接配置片段展示。
**范围外（YAGNI）**：多 key/标签、分级权限、调用配额/限流、审计日志、key 历史。

## 2. 数据模型与存储

- **单一受管 key**，存于生产配置真相源 `data/.env` 的 `MCP_API_KEYS`。
- UI 视角始终只有一把 key：
  - **读**：取 `MCP_API_KEYS` 解析后的**首个 key**（`parse_api_keys` 已存在，`api/mcp/auth.py`）作为「当前生效 key」。
  - **重置/首次生成**：`secrets.token_hex(24)` 生成新 key，**整体覆盖** `MCP_API_KEYS`（写 `MCP_API_KEYS=<newkey>`，不带 label，旧 key 一并失效）。
- 写回用 `ConfigManager.apply_updates(updates, sensitive_keys, mask_token)`（`src/core/config_manager.py:112`，加锁 + 原子 `os.replace` + 失败回退），传 `sensitive_keys={"MCP_API_KEYS"}`；新 key 非掩码、非原值，必落盘。权限保持 600（现有写入逻辑保留文件权限）。
- key 不写日志、不入 git；仅在端点响应体回传给已登录管理员。

## 3. 后端

### 3.1 动态鉴权（核心改动）
当前 `MCPAuthMiddleware`（`api/mcp/auth.py`）在 `build_mcp_asgi_app()`（`api/mcp/server.py:142`）构造时把 key 集合冻结进 `self.api_keys`，故改 key 需重建容器。改为：

- `MCPAuthMiddleware.__init__` 接收 **`key_provider: Callable[[], Set[str]]`**（而非冻结的 `Set[str]`），`__call__` 每请求调用 `key_provider()` 取当前 key 集合再比对。
- 新增**带短 TTL 缓存的 provider**（默认 **TTL≈3s**，避免每请求读盘）：直接读 `data/.env`（经 `ConfigManager.read_config_map()` 或 `parse_api_keys(load 当前 MCP_API_KEYS)`），多 worker 安全（都读同一文件）。
- `build_mcp_asgi_app()` 改为传入该 provider（替换原 `load_mcp_api_keys()` 冻结集合）。
- 重置端点写盘后**主动失效缓存**（或依赖 ≤3s TTL 自然过期），保证下一个 `/mcp` 请求即生效。
- 行为保持：provider 返回空集合 → 一律 401（未配置即拒绝，安全默认不变）。

> 兼容：保留 `parse_api_keys`/`load_mcp_api_keys` 供 provider 复用；`MCPAuthMiddleware` 既有「无 key→401、Bearer 比对」语义不变，仅 key 来源由「冻结」改「动态」。

### 3.2 新端点（`api/v1/endpoints/mcp_keys.py`，挂 `/api/v1/mcp-keys`）
经 `api/v1/router.py` 注册（`include_router(mcp_keys.router, prefix="/mcp-keys", tags=["MCP Keys"])`）。路径在 `/api/v1/*` 下，自动被 `AuthMiddleware`（`api/middlewares/auth.py`）的登录态保护——**仅已登录管理员可访问**。

- `GET /api/v1/mcp-keys` → `{ "key": "<当前key全文或null>", "endpoint": "<scheme>://<Host>/mcp", "configured": <bool> }`。
  - `key` 返回**明文全文**（管理员登录态 + HTTPS，前端负责脱敏显示与复制）。
  - `endpoint` **由请求的 scheme+Host 推导**拼 `/mcp`，不硬编码域名。
  - 无 key 时 `configured=false`、`key=null`。
- `POST /api/v1/mcp-keys/reset` → 生成新 key、`apply_updates` 写回、失效鉴权缓存、返回 `{ "key": "<newkey>", "endpoint": "...", "configured": true }`。
  - 首次无 key 时 reset 即「首次生成」（无需独立 generate 端点）。
- Service 层 `MCPKeyService`（`src/services/mcp_key_service.py`）封装读/生成/写，便于单测与端点解耦。

## 4. 前端（`apps/dsa-web/`）

- `components/layout/SidebarNav.tsx`：`navItems` 新增 `{ key: 'mcp-keys', label: '密钥', to: '/mcp-keys', icon: KeyRound }`（`lucide-react` 的 `KeyRound`），置于「设置」附近。
- 路由：在前端 React Router 配置（现有页面注册处，如 `App.tsx`/路由表文件，与 `/settings` 等同处）注册 `/mcp-keys` → `pages/MCPKeyPage.tsx`。
- `pages/MCPKeyPage.tsx`：单卡片
  - **端点地址**（一键复制）；
  - **当前 key**：默认脱敏 `0bc8••••••`，「显示/隐藏」切换 + 「复制」；无 key 时显示「尚未生成」+「生成」按钮；
  - **重置按钮**：二次确认弹窗（文案警示「旧 key 立即失效，正在使用的智能体须更新为新 key」）；
  - **连接配置片段**：可复制的 `Authorization: Bearer <key>` 与 `mcpServers` JSON（贴合「让智能体快速正确连上」）。
- `api/mcpKeys.ts`：`getMcpKey()`、`resetMcpKey()`，复用现有 `api/utils.ts` 请求封装与错误处理。
- 复用现有 settings/卡片视觉与 i18n 风格（中文标签）。

## 5. 错误处理与安全

| 场景 | 行为 |
|------|------|
| 未登录访问 `/api/v1/mcp-keys*` | 401（沿用 AuthMiddleware，端点无需额外处理） |
| 重置写盘失败 | 端点返回 error；`apply_updates` 原子写，失败不破坏现有 key（旧 key 仍有效） |
| 智能体用旧 key（重置后） | `/mcp` 返回 401 `{"error":"unauthorized"}` |
| 智能体用新 key | `/mcp` 正常 |
| key 暴露面 | 不入日志/不进 git；前端默认脱敏；传输走 HTTPS + 登录态 |

## 6. 测试

**后端**
- `MCPKeyService`：读取（首个 key/无 key）、生成（`token_hex` 长度/唯一）、重置覆盖写回（旧失效、新落盘）。
- **动态鉴权**：`MCPAuthMiddleware` 持 `key_provider`——provider 返回旧集合→旧 key 200/新 key 401；provider 切新集合后→旧 key 401/新 key 200（**证明免重启生效**）；空集合→一律 401；TTL 缓存命中/过期行为。
- 端点：`GET`/`POST reset` 结构与字段；登录态保护（未登录 401，复用现有 auth 测试模式）。
- 全量 `./scripts/ci_gate.sh` 守绿；**不触碰数据源铁律**（本特性仅 auth/config，不动 `data_provider` 路由）。

**前端**
- `MCPKeyPage`：渲染、脱敏/显示切换、复制、重置确认流、无 key→生成态（复用 `pages/__tests__`、`components/.../__tests__` 模式）。
- `SidebarNav`：新增项渲染（更新 `SidebarNav.test.tsx`）。
- `npm run lint && npm run build`。

## 7. 文件清单（决定任务拆分）

**后端**
- 改：`api/mcp/auth.py`（`MCPAuthMiddleware` 改 `key_provider` + TTL 缓存 provider）、`api/mcp/server.py`（`build_mcp_asgi_app` 传 provider + 暴露缓存失效钩子）、`api/v1/router.py`（注册新路由）。
- 新：`api/v1/endpoints/mcp_keys.py`、`src/services/mcp_key_service.py`、对应 `tests/test_mcp_key_*.py`、`tests/test_mcp_auth_dynamic.py`。

**前端**
- 改：`apps/dsa-web/src/components/layout/SidebarNav.tsx`、应用路由表、`SidebarNav.test.tsx`。
- 新：`apps/dsa-web/src/pages/MCPKeyPage.tsx`、`apps/dsa-web/src/api/mcpKeys.ts`、对应组件/页面测试。

**文档**
- 改：`docs/mcp-gateway.md`（说明 key 现可经 WebUI 查看/重置、即时生效）、`docs/mcp-integration-guide.md`（指向密钥页获取 key）、`docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）。

## 8. 部署

合并 main + 推送 origin → 服务器 `git pull` + `docker compose ... up -d --build`（前端构建产物随镜像；后端含 mcp 已在）。上线后验收：WebUI 登录→密钥页查看/重置→旧 key `/mcp` 401、新 key 200（免重建即时生效）。
