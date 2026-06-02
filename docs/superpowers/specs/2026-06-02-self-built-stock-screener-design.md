# 自研选股引擎设计（替换 AlphaSift）

- 日期：2026-06-02
- 状态：设计已与用户确认，待写实施计划
- 背景：AlphaSift 选股的外部数据源全部不可用（实测全挂），且其 `screen()` 取数低效（实测 444 秒触发 WebUI 超时）。决定**移除 AlphaSift 渠道**，用本项目自有数据接口 + 大模型，自研选股引擎，保留"按策略选股"的产品能力。

---

## 1. 目标与非目标

### 目标
- 移除对 AlphaSift 包及其专属端点/前端逻辑的依赖。
- 用本项目 `data_provider`（Tushare 批量接口）+ 现有 LLM 封装，实现"按策略 + 用户偏好选股"。
- 上线 8 个选股策略，复用项目自带 `strategies/` 目录里的策略 YAML（中文、A 股语境、规则可见可改）。
- 新增"用户偏好"自由文本输入，支持成分股/板块偏好与操盘风格偏好。
- 选股结果体验对齐原 AlphaSift（候选列表 + 评分 + 选股理由 + 风险提示），前端结果卡片尽量复用。

### 非目标
- 不触碰数据源优先铁律（实时/日K 主力链 = TickFlow，资金流/财务等 = Tushare，akshare 末位）。选股取数虽走 Tushare 批量接口，但属**独立调用路径**，不改动 `data_provider` 的路由/优先级/fetcher 链。
- 不做策略回测（已有独立 BacktestPage）。
- 不追求精确复刻 AlphaSift 原 8 个策略（其逻辑封在包内、为黑盒，本就无法复刻）。

---

## 2. 总体架构

```
前端 StockScreeningPage（基本复用）
   │ 提交选股 job(strategy?, preference?, max_results) → 轮询
   ▼
后端新模块 src/services/stock_screener/
   ├─ 第1段 全市场足切 (screener)
   │    ① get_stock_list() 拿全 A 股（5000+）
   │    ② fetch_market_snapshot(n_days)：按交易日批量取最近 N 日全市场 daily + daily_basic
   │    ③ 按所选策略的量化信号，本地 pandas 算分 → 候选池（截断到上限）
   │    （+ 偏好中的板块/成分股做附加硬过滤）
   └─ 第2段 LLM 轻量重排 (ranker)
        ④ 候选池关键指标打包 → 复用现有 LLM 封装，一次调用
        ⑤ 输出排序 + 选股理由 + 风险提示 → 取前 max_results 只
```

异步化：**复用并泛化现有 `src/services/alphasift_screen_jobs.py`**（内存 job store / 单 worker / 幂等复用 / TTL / 容量上限），改名为 `screen_jobs.py`，job key 改为 `(strategy, preference_hash, max_results)`。前端"提交 + 轮询"机制原样保留（4s 间隔、15min 硬超时、404/抖动重试、epoch 守卫）。

---

## 3. 组件设计

### 3.1 数据取数层：`fetch_market_snapshot`
- 位置：`data_provider`（新增方法，不改既有方法签名与路由）。
- 行为：给定回看天数 N，用 Tushare 的 `daily`（按 `trade_date` 一次返回全市场某日日K）与 `daily_basic`（按 `trade_date` 一次返回全市场 PE/PB/换手/量比/市值等），取最近 N 个交易日，拼成全市场多日行情 DataFrame（约几十次 API 调用，**不逐股**）。
- 复用底层 `_fundamental_df()`（已能调任意 Tushare 接口）。
- 失败降级：取数失败抛明确异常，由端点捕获返回友好错误；不崩溃。
- 缓存：同一交易日的 snapshot 在 job 生命周期内可复用（避免一次选股内重复拉取）。

### 3.2 第1段：策略 scorer
- 每个上线策略 = 一个独立 scorer 函数：`scorer(market_df) -> DataFrame[code, name, signal_score, signal_detail]`。
- 量化判据**直接取自对应策略 YAML 的 `instructions`** 里已写明的可量化条件（示例：均线金叉 = MA5 在近 3 日上穿 MA10 且量比 > 1.2 且乖离 < 5%）。
- 候选池上限：策略命中后按 `signal_score` 取 Top（默认 80）喂第 2 段，避免候选过多。

### 3.3 上线的 8 个策略

| # | YAML id | 名称 | 类型 | 操盘风格（卡片展示） |
|---|---------|------|------|----------|
| 1 | ma_golden_cross | 均线金叉 | 趋势 | 趋势确认、稳健追涨 |
| 2 | volume_breakout | 放量突破 | 趋势 | 追涨、激进 |
| 3 | bottom_volume | 底部放量 | 反转 | 抄底、左侧反转 |
| 4 | shrink_pullback | 缩量回踩 | 趋势 | 回调低吸、稳健 |
| 5 | one_yang_three_yin | 一阳夹三阴 | 形态 | 趋势延续入场、中性 |
| 6 | growth_quality | 成长质量 | 基本面 | 中长线价值、保守 |
| 7 | box_oscillation | 箱体震荡 | 框架 | 高抛低吸、稳健 |
| 8 | bull_trend | 多头趋势 | 趋势 | 趋势跟随、中性偏进取 |

- **操盘风格来源**：在上述 8 个策略 YAML 各新增一个 `trading_style` 字段（值为简短中文描述，如上表），由 `SkillRegistry`（`src/agent/skills/base.py`）解析。`Skill` 数据结构需支持读取该可选字段（缺省为空串，向后兼容其它策略 YAML）。
- 策略列表端点向前端返回每个策略的 `id / name / category / description / trading_style`。

### 3.4 用户偏好（新增）
- 前端新增"用户偏好"自由文本输入框（占位示例："喜欢科技股、偏好抄底、规避高估值"）。
- 偏好被拆为两类作用：
  - **成分股/板块类**（如"科技股""医药"）→ 作用于**第1段**，对候选池做板块/标的硬过滤（用 `get_belong_boards` / `stock_basic` 的行业字段）。
  - **操盘风格类**（如"抄底""激进"）→ 作用于**第2段 LLM**，作为排序与取舍的最高优先指令。
- 偏好为可选自由文本，不强制结构化；板块识别可由第2段 LLM 在候选过滤前先做一次轻量解析，或在 prompt 中交给 LLM 判断（实现时择一，优先简单方案）。

### 3.5 输入组合与校验（行为表）

| 输入 | 第1段足切 | 第2段 LLM | 校验结果 |
|------|----------|-----------|---------|
| 仅策略 | 按策略信号扫全市场 → 候选池 | 按策略语义排序 + 理由 | 通过 |
| 策略 + 偏好 | 按策略信号扫全市场；偏好含板块/成分股时叠加过滤 | 偏好优先排序/取舍 + 策略语义；冲突时偏好优先（仅在候选范围内） | 通过 |
| 仅偏好（含板块/成分股） | 偏好板块/成分股 + 基础流动性过滤 → 候选池，按量价活跃度粗排截断（默认 150） | 完全按偏好排序 + 理由 | 通过 |
| 仅偏好（无板块/成分股，如只写"激进"） | —— | —— | 拒绝，提示"请补充板块/成分股偏好，或选择一个策略" |
| 策略与偏好都空 | —— | —— | 拒绝，提示"策略和用户偏好至少填写一个" |

**冲突优先边界（已与用户确认并接受）**：策略决定"候选从哪来"，偏好决定"候选里怎么排/怎么取舍"。当策略风格与偏好风格相反（如放量突破 + 抄底偏好），候选池受策略约束，第2段在候选范围内尽量贴合偏好（可宁缺毋滥、返回更少），并在结果说明里如实标注，不假装完全按偏好。

### 3.6 第2段：LLM 轻量重排 ranker
- 复用现有 LLM 封装（`src/analyzer.py` 的 LiteLLM 多渠道通道，运行时自动走 MiMo）。**不再注入 AlphaSift 那套 `LLM_API_KEY`/`LLM_BASE_URL` 临时环境变量**。
- 单次（或少数几次）调用：传候选池摘要（code / name / 命中信号 / 关键指标如涨跌幅/量比/PE/PB/所属板块），以及策略说明 + 用户偏好文本，要求模型输出排序、每只一句选股理由（`reason`/`llm_thesis`）、风险提示（`llm_risks`）。
- 输出结构沿用前端已有 `candidates[]` 字段（code/name/score/reason/llm_thesis/llm_catalysts/llm_risks 等），**前端结果卡片几乎不改**。
- 降级：LLM 调用失败 → 直接用第1段 `signal_score` 排序返回，并在 `warnings` 标注"LLM 重排不可用，已按量化打分排序"。

### 3.7 异步 job 与端点契约
- 端点改名（去 AlphaSift）：
  - `POST /api/v1/screen/jobs`：提交选股 job，body：`{ strategy?: string, preference?: string, max_results: int=20 }`（strategy 与 preference 至少一个非空）。
  - `GET /api/v1/screen/jobs/{job_id}`：纯内存查询 job 状态/结果，**不触发任何选股计算**。
  - `GET /api/v1/screen/strategies`：返回 8 个策略元信息（含 `trading_style`）。
- 响应字段沿用：`enabled / candidates[] / candidateCount / run_id / strategy / preference / snapshot_count / after_filter_count / llm_ranked / llm_selection_logic / llm_portfolio_risk / warnings / source_errors`。
- 认证：新端点经由 `api/middlewares/auth.py` 自动受保护（与现有端点一致）。

---

## 4. 移除清单（AlphaSift 下线）

- 删除/改写 `api/v1/endpoints/alphasift.py`：移除 `/status`、`/install`、`/screen`、AlphaSift 包探测、`_alphasift_llm_env` 临时环境注入；新选股端点落到 `api/v1/endpoints/screen.py`（新文件）。
- `src/services/alphasift_screen_jobs.py` → 泛化改名 `src/services/screen_jobs.py`。
- 前端 `apps/dsa-web/src/api/alphasift.ts` → 改名 `screen.ts`，更新 `submitScreenJob`/`getScreenJob`/`getStrategies` 指向新端点；`StockScreeningPage.tsx` 更新导入、新增偏好输入框、策略卡片展示 `trading_style`、校验"策略与偏好至少一个"。
- 移除对 `alphasift` 包的安装/依赖（requirements、Dockerfile、docker-compose 若有相关项）。
- 旧测试 `tests/test_alphasift_api.py` → 改写为 `tests/test_screen_api.py`，覆盖新端点与行为表。

---

## 5. 错误处理与降级

- 取数失败 / 当日非交易日：返回明确提示，不崩溃。
- 某策略当日 0 命中：返回空候选 + 提示"今日无标的命中该策略"。
- LLM 重排失败：降级为第1段量化打分排序（见 §3.6）。
- 偏好板块无法识别且无策略：按 §3.5 拒绝并提示。

---

## 6. 测试

- 第1段 scorer（TDD）：用构造的小样本 `market_df` 验证 8 个策略各自命中逻辑、边界（空命中、字段缺失）。
- 取数层：mock Tushare 返回，验证 `fetch_market_snapshot` 拼接与失败降级。
- 第2段 ranker：mock LLM，验证重排输出解析、偏好优先指令拼装、LLM 失败降级。
- 端点：按 §3.5 行为表逐组合验证（仅策略 / 策略+偏好 / 仅偏好含板块 / 仅偏好无板块拒绝 / 都空拒绝）。
- 端到端冒烟：本地跑一次全市场选股，确认耗时可接受（异步 job 无 CDN 100s 限制；目标第1段取数+足切控制在分钟级）。

---

## 7. 铁律 / 文档 / 记忆更新

- `CLAUDE.md`：移除"AlphaSift 选股异步化分叉"条目；新增"自研选股引擎"说明；明确本模块**不触碰数据源铁律**（选股取数走 Tushare 批量接口，是独立调用路径）。
- 自动记忆 `dsa-alphasift-async-divergence`：标记 AlphaSift 已下线、被自研引擎替换。
- 新增/更新相关运维说明（选股入口、策略列表、偏好用法）。

---

## 8. 关键默认值（实现时可微调）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| snapshot 回看天数 N | 60 | 足够算 MA/量比等；按策略最大需求取 |
| 策略候选池上限（喂 LLM 前） | 80 | 按 signal_score 取 Top |
| 仅偏好（含板块）候选上限 | 150 | 按量价活跃度粗排截断 |
| max_results（返回数） | 20 | 与原 AlphaSift 默认一致，前端可调 1–100 |
| 前端轮询间隔 / 硬超时 | 4s / 15min | 沿用现有 |
