# AlphaSift 选股：异步化 + LLM 接入 MiMo 设计文档

- 日期：2026-06-01
- 分支：`feat/alphasift-async-screen`（基线 `4db8eb5c` = 生产 origin/main）
- 作者：维护专员（Claude）

## 1. 背景与问题

生产环境（a-stock.tech-monthly.online）开启 AlphaSift 选股后，在 WebUI 点「运行选股」报错：

> 调用失败 链接上有服务器超时：服务端访问外部依赖时超时

### 实测根因（已用证据锁定）

在 `stock-analyzer` 容器内绕过所有网络层、直接调 `alphasift.dsa_adapter.screen('dual_low', market='cn', max_results=20, use_llm=True)`：

- **耗时 444.3 秒（≈7.4 分钟）**才完成，成功返回 20 个候选（snapshot_count=5193 → after_filter=360 → 20）。
- 操作本身是**全 A 股市场扫描 + 逐只指标/分析**，慢是固有特性。

请求链路与各层超时阈值：

```
浏览器 ──> Cloudflare(橙云) ──> Nginx ──> 容器(FastAPI)
            ~100s → 524        300s      实际需 444s
            前端 axios 另设 180s
```

| 层 | 超时阈值 | 是否先掐断 |
|----|---------|-----------|
| Cloudflare 橙云（免费/Pro 固定上限）| ~100s → 524 | ✅ 最先触发 |
| 前端 axios（`ALPHASIFT_SCREEN_TIMEOUT_MS=180000`）| 180s | 其次 |
| Nginx `proxy_read_timeout` | 300s（已调过，非瓶颈）| 否 |
| 后端实际耗时 | 444s | —— |

约 100s 时 Cloudflare 先回 524（文本含 "timeout"），前端 `error.ts` 把它归类为 `upstream_timeout` → 显示「连接上游服务超时：服务端访问外部依赖时超时」。

**结论：这不是 bug，是"7 分钟长任务塞进同步 HTTP 请求 + CDN 100s 硬上限"的结构性冲突。** 上游在本地/桌面部署（无 CDN）不会触发，属本部署环境特有问题。

### 次要问题：LLM 排序没接上 MiMo（一并修）

探针日志：`LLM ranking failed, falling back to screen_score: ... Missing credentials ... OPENAI_API_KEY`。

机理（读 AlphaSift 0.2.0 源码确认）：
- `data/.env` 同时设了 `LITELLM_MODEL`，AlphaSift `_resolve_llm_model` **优先**用它 → 模型解析为 `openai/<mimo模型>`。
- `_resolve_llm_api_key(model)`：`openai/*` → 只读 `OPENAI_API_KEY`，**不读** DSA 实际配置的 `LLM_MIMO_API_KEY`。
- `_resolve_llm_base_url(model)` 同理 → 只读 `OPENAI_BASE_URL`。
- 二者皆空 → litellm 报缺凭证 → 回退普通打分。

**关键缝隙**：AlphaSift 留了 `LLM_API_KEY` / `LLM_BASE_URL` 两个**最高优先级万能覆盖**（先于 provider 判断）。只要在调用前把 MiMo 的 key/base_url 注入这两个变量，AI 排序即可走 MiMo——**无需改第三方包**。

## 2. 目标与非目标

**目标**
1. WebUI 点「运行选股」不再因链路超时报错，能稳定拿到选股结果（页面轮询等待）。
2. AlphaSift 的 LLM 重排真正用上 MiMo。

**非目标 / YAGNI**
- 不做结果持久化（仅内存，刷新/重启即丢——选股是探索性操作）。
- 不做飞书推送（本次只做页面轮询交互）。
- 不改 AlphaSift 第三方包，不改 AlphaSift 的快照数据源体系。
- 不触碰 DSA「TickFlow+Tushare 优先、akshare 末位」数据源铁律（AlphaSift 用的是其自带快照源，与该体系独立）。
- 不删除/改变原同步 `/screen` 端点的契约（保留给桌面端/兼容）。

## 3. 设计

### 3.1 后端

**新增模块 `src/services/alphasift_screen_jobs.py`（单一职责、隔离）**
- `AlphaSiftScreenJobStore` 单例。
- 内存字典 `job_id -> JobRecord`：
  - `JobRecord{ job_id, status(pending|running|completed|failed), request(market/strategy/max_results), result(dict|None), error(str|None), created_at, started_at, finished_at }`
- `ThreadPoolExecutor(max_workers=1)`：选股吃内存，**串行**执行，避免容器 OOM（生产 server 容器有内存限制）。
- TTL/容量控制：仅保留最近 N 条（建议 20）且超过 1 小时的记录在新提交时清理，防止内存无限增长。
- 接口：`submit(market, strategy, max_results, run_fn) -> job_id`；`get(job_id) -> JobRecord | None`。
- worker 内捕获异常写入 `error`，正常写入 `result`。

**重构 `api/v1/endpoints/alphasift.py`**
- 把现有 `alphasift_screen()` 里"调用适配层 + 规范化返回"的核心抽成独立函数：
  `run_alphasift_screen(config, *, market, strategy, max_results) -> dict`（返回与现有 `/screen` 响应体一致的 dict）。
- **原 `POST /api/v1/alphasift/screen` 端点保留不变**，内部改为调用 `run_alphasift_screen`（行为等价）。
- job worker 也调用同一个 `run_alphasift_screen`，逻辑 DRY。

**新增追加式端点**
- `POST /api/v1/alphasift/screen/jobs`
  - body：`{market, strategy, max_results}`（与现有 `AlphaSiftScreenRequest` 同）。
  - 同步**快速**校验：`_ensure_alphasift_enabled` / `_ensure_supported_market` / `_ensure_supported_strategy`（秒级）。校验失败按现有错误码返回（403/422 等）。
  - 校验通过 → `store.submit(...)` 建 job → 立即返回 `{ job_id, status: "pending" }`。耗时 <1s，不触发 CDN 超时。
- `GET /api/v1/alphasift/screen/jobs/{job_id}`
  - 返回 `{ job_id, status }`；`completed` 时附带完整选股结果字段（同 `/screen` 响应体）；`failed` 时附 `error`（message + 可选 error code）；未知 job_id → 404。

> 鉴权：沿用现有全局认证中间件（`/api/v1/*` 需有效管理员会话），与现有 `/screen` 一致，新增端点无需特殊处理。

### 3.2 LLM 接入 MiMo

- 新增/扩展运行时环境准备（与现有 `_prepare_alphasift_runtime_env` 同款做法）：在调用 `run_alphasift_screen` 前，从 DSA 已解析的**主 LLM 渠道**（`config.llm_channels` 中与当前激活模型对应、或第一个 enabled 的渠道）取 `api_keys[0]` 与 `base_url`，分别注入进程环境的 `LLM_API_KEY` / `LLM_BASE_URL`。
- **仅当对应环境变量尚未设置时注入**（不覆盖用户显式配置）。
- 动态取值（按渠道），将来切 DeepSeek 等兜底渠道也自动适配。
- `LLM_API_KEY`/`LLM_BASE_URL` 不在 DSA 自身配置变量集合内，注入不影响 DSA 自有 LLM 路由。
- 敏感值只在进程内存中流转，不写日志、不回显。

### 3.3 前端

- `apps/dsa-web/src/api/alphasift.ts`
  - 新增 `submitScreenJob(payload) -> { jobId, status }`（POST `.../screen/jobs`，**短超时**，如 30s）。
  - 新增 `getScreenJob(jobId) -> { status, ...result }`（GET，短超时）。
  - 保留或基于上述重写原 `screen()`（页面改用 job 流）。
- `apps/dsa-web/src/pages/StockScreeningPage.tsx`
  - 「运行选股」handler 改为：`submitScreenJob` → 每 ~4s 轮询 `getScreenJob` 直到 `completed`/`failed`。
  - 轮询期间展示进度态：转圈 + 已用时 + 文案「选股需扫描全市场，预计需几分钟，请勿关闭页面」。
  - `completed` → 用现有候选渲染组件展示结果；`failed` → 用现有 `parseApiError` 错误展示。
  - **无客户端硬超时**（或设宽松上限如 15min），CDN 100s 不再相关（每次轮询请求都是秒级）。
  - 组件卸载/再次点击时清理轮询定时器，避免泄漏与竞态。

### 3.4 数据流

```
用户点击运行
  → POST /screen/jobs  (秒回 job_id, status=pending)
  → 后台线程: run_alphasift_screen(注入 LLM_API_KEY/BASE_URL → 调适配层 ~7min)
  → 前端每 4s: GET /screen/jobs/{id}
       pending/running → 继续轮询(更新已用时)
       completed → 渲染候选
       failed → 渲染错误
```

### 3.5 错误处理

- 提交时同步校验失败：直接返回现有错误码（403 disabled / 422 market/strategy）。
- 后台运行异常：worker 捕获，job 置 `failed` + error message（含 AlphaSift 抛出的原因）；轮询端点返回该 error，前端走 `parseApiError` 展示。
- 数据源降级（Tushare/efinance/akshare 个别失败重试）属正常现象，只要最终出结果即视为成功；不当作 job 失败。
- 未知/过期 job_id：404，前端提示「任务不存在或已过期，请重新运行」。

## 4. 测试策略

**后端（`tests/test_alphasift_api.py` 扩展）**
- mock 适配层 `screen`：
  - `POST /screen/jobs` 返回 job_id 且 status=pending。
  - 轮询 `GET /screen/jobs/{id}`：经历 running → completed，completed 带候选。
  - worker 抛异常 → job=failed 且带 error。
  - 未启用/非法市场/非法策略 → 提交端点返回对应错误码。
  - LLM 环境注入：给定含 MiMo 渠道的 config，调用前 `LLM_API_KEY`/`LLM_BASE_URL` 被正确注入；已存在时不覆盖。

**前端（`StockScreeningPage.test.tsx` / `alphasift.test.ts` 扩展）**
- mock submit + 轮询：首轮 running、次轮 completed → 渲染候选。
- 轮询期间显示进度文案；failed → 显示错误。

**既有命令**（与集成文档一致）
- `python -m pytest tests/test_alphasift_api.py -q`
- `python -m py_compile api/v1/endpoints/alphasift.py src/services/alphasift_screen_jobs.py`
- `cd apps/dsa-web && npm run test -- alphasift.test.ts StockScreeningPage.test.tsx --run`
- `cd apps/dsa-web && npm run lint && npm run build`

## 5. 部署与验收

1. 服务器先备份（沿用 `scripts/backup.sh` 或手动 tar `data/`）。
2. `git pull` 到生产 → Docker 重建镜像（多阶段构建会编译前端）→ `docker compose up -d` 重建两容器。
3. **真机实跑一次选股**（WebUI 或直调新端点）：确认
   - 提交秒回、轮询正常推进、最终出候选；
   - 结果中 `llm_ranked=true` 且候选带 LLM 字段（证明 MiMo 接上）；
   - 过程中 `docker stats` 观察 `stock-server` 内存不 OOM（注意：现有同步版本本就在 server 容器跑选股，异步不新增内存风险，仅确认）。
4. 失败则回滚镜像/分支。

## 6. ⚠️ 上游同步影响（必须登记）

本改动修改的**上游文件**（同步上游时需手工保留/重新合并）：
- `api/v1/endpoints/alphasift.py`（新增端点 + 抽取 `run_alphasift_screen` + LLM 环境注入）
- `apps/dsa-web/src/api/alphasift.ts`
- `apps/dsa-web/src/pages/StockScreeningPage.tsx`
- `tests/test_alphasift_api.py`（追加用例）

新增（无冲突）：`src/services/alphasift_screen_jobs.py`、本设计文档。

降冲突措施：改动尽量**追加式**（新端点、新模块），不删原 `/screen`。落地后登记进 `CLAUDE.md` 与自动记忆，与数据源铁律一同提醒同步时保留。

## 7. 回滚

- 代码回滚：切回 `4db8eb5c` 重建镜像即恢复同步版本。
- 功能回滚：设置页关闭 AlphaSift（`ALPHASIFT_ENABLED=false`），不影响个股分析/报告/通知主流程。
