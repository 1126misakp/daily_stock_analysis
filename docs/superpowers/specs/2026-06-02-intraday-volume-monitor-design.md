# 盘中分钟级量能（放量/缩量）监控 — 设计文档

- 日期：2026-06-02
- 状态：设计已确认，待写实施计划
- 作者：维护专员（与用户 brainstorming 确认）

## 1. 背景与目标

现有系统只有**日线级**放量告警（告警中心 `volume_spike`：今日成交量 vs 近 20 日均量），且该告警 worker（`AGENT_EVENT_MONITOR_ENABLED`）当前未启用；不支持缩量、不支持盘中分钟级异动。

本功能新增一个**独立的盘中分钟级量能监控器**：在交易时段内，每 5 分钟扫描一组股票的 5 分钟 K 线成交量，与"同时段历史基线"对比，识别**放量 / 缩量**异动，合并成一条飞书消息推送。

### 能力可行性（已实测，2026-06-03 盘中）
- TickFlow 分钟 K（`get_intraday_kline`）可用，最细 **5 分钟**（`5m/15m/30m/60m`，无 1m），每根含 `open/high/low/close/volume/amount`。
- 历史深度：5m 可拉满 2000 根（≈2 个月），足够算 20 日同时段基线。
- 盘中实时滚动更新，单股调用 0.2~0.5s。
- **限流 = 60 次/分钟**（`RateLimitError ... K线查询限流 (60/min) ... status_code=429`，带"还需等待 XX ms"提示）。本功能负载（标的数 × 每 5 分钟一轮）远低于此。

### 非目标（YAGNI）
- 不做 1 分钟/逐 tick 级监控（TickFlow 当前档位不支持，用户接受 5 分钟颗粒度）。
- 不做前端配置页/触发历史入库（用户选择"纯后台：.env 开关 + 飞书推送"）。
- 不改动告警中心、日线 `volume_spike`、`data_provider/` 数据源路由（铁律）。

## 2. 需求规格（已与用户确认）

| 项 | 决定 |
|----|------|
| 监控标的 | **STOCK_LIST ∪ 持仓股**，去重；无持仓时自动只剩自选股 |
| 颗粒度 | 5 分钟 K 线 |
| 扫描频率 | **每 5 分钟一轮** |
| 量能基准（口径 B） | 当前这根 5m 成交量 vs **近 20 交易日同一时刻**的 5m 均量 |
| 放量阈值 | 量比 ≥ **2.0**（可配） |
| 缩量阈值 | 量比 ≤ **0.5**（可配） |
| 冷却/去重 | **同股 + 同类型，当日仅首次**（放量、缩量分别计） |
| 推送形式 | **每轮合并成一条飞书汇总**；本轮无异动则不推 |
| 推送渠道 | 现有 `NotificationService().send(content)`（**不带 route_type**，与 `send_daily_report` 一致，发往全部已配置渠道=飞书；生产未配 `NOTIFICATION_*_CHANNELS`） |
| 启停与可见性 | 纯后台：`data/.env` 开关 + 参数；触发即推飞书 |
| 运行时段 | 交易日连续竞价 + 尾盘集合竞价（`MarketPhase ∈ {INTRADAY, CLOSING_AUCTION}`） |

## 3. 架构（方案 A：独立监控器）

**运行位置**：`stock-analyzer` 容器（`main.py --schedule`），注册进现有 `src/scheduler.py` 后台任务框架——与现有 `agent_event_monitor` 并列、互不影响。

### 3.1 新增文件

| 文件 | 职责 | 依赖 |
|------|------|------|
| `src/services/intraday_volume_monitor.py` | 监控器主体：编排一轮扫描（时段判断→取标的→取数→判定→去重→合并推送），暴露 `run_once()` | 下列三个 + `DataFetcherManager` + `NotificationService` + `trading_calendar` |
| `src/services/intraday_volume/__init__.py` | 子包导出 | — |
| `src/services/intraday_volume/universe.py` | 解析监控标的 = STOCK_LIST ∪ 持仓股，去重 + 代码规范化 | `config` + `portfolio_service` |
| `src/services/intraday_volume/baseline.py` | 计算并缓存"每只股票近 N 交易日各 5 分钟时刻的均量"，提供 `get_slot_baseline(code, slot) -> Optional[float]` | `DataFetcherManager.get_intraday_kline` |
| `src/services/intraday_volume/detector.py` | **纯函数**：`classify(current_volume, baseline_volume, up_ratio, down_ratio) -> VolumeSignal`（放量/缩量/正常 + 量比） | 无（纯计算） |

### 3.2 改动的现有文件（最小化）

- `main.py`：在现有 `background_tasks` 注册块（约 958–976 行）内，按 `INTRADAY_VOLUME_MONITOR_ENABLED` 追加一个后台任务，间隔 = `INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES × 60`，`run_immediately=True`，`name="intraday_volume_monitor"`。
- `src/config.py`：新增配置字段解析（见 §6）。

> 仅此两处改动，且都是"在既有扩展点旁并列新增"，不修改任何已上线逻辑。

### 3.3 组件边界
- `detector.py` 纯计算、无 IO → 单测最易、覆盖阈值边界。
- `universe.py` / `baseline.py` 各自单一职责、可独立测。
- `intraday_volume_monitor.py` 只做编排，不含判定/取数细节。
- **铁律遵守**：取数只经 `DataFetcherManager.get_intraday_kline()`（内部只路由 TickFlow，`capability="intraday_kline"`），不触碰 `data_provider/base.py` 的路由/优先级/fetcher 链。

## 4. 数据流与核心算法

### 4.1 一轮 `run_once()` 流程
```
1. 时段判断：infer_market_phase("CN") ∉ {INTRADAY, CLOSING_AUCTION} → 直接 return（空转零成本）
2. 解析标的：universe.resolve() → 去重后的股票代码列表 codes
3. 当日去重集合：从内存当日状态取 already_alerted（key=(code, signal_type)）；若日期变更则重置
4. 遍历 codes（串行，天然限速）：
   a. df = manager.get_intraday_kline(code, period="5m", count=PROBE_COUNT)
   b. 取"最后一根已收的 5m bar"：当前时刻所属未走完的 bar 不算（见 4.3）；得 (slot, current_volume)
   c. baseline = baseline.get_slot_baseline(code, slot)；若 None（数据不足）→ 跳过该股
   d. signal = detector.classify(current_volume, baseline, UP_RATIO, DOWN_RATIO)
   e. 若 signal 为 放量/缩量 且 (code, type) 不在 already_alerted → 收集进本轮 hits，并加入 already_alerted
5. 若 hits 非空：渲染一条飞书汇总消息，NotificationService().send(content, route_type=..., severity="info")
6. 返回统计 {scanned, hits, skipped, notified}
```

### 4.2 同时段历史基线（口径 B）
- **slot 定义**：5m bar 的结束时刻（如 `09:35`、`10:05`、`14:55`），用 `HH:MM` 字符串标识。
- **基线值**：近 `BASELINE_DAYS`（默认 20）交易日里，**同一 slot** 的 5m 成交量均值。
- **计算来源**：开盘前/首次用到时，对每只股票拉一次 `get_intraday_kline(code, "5m", count=BASELINE_DAYS*48 + 余量)`（48 = 一个交易日的 5m bar 数），按 slot 分组求均值；**当日缓存一次**（内存），同一交易日内不重复拉，跨交易日失效重算。
- **数据不足**：某 slot 历史样本数 < `BASELINE_MIN_SAMPLES`（默认 5）→ 该 slot 基线返回 None → 当根跳过（不误报）。新股/长期停牌天然被此规则保护。

### 4.3 "当前根"的选取（避免用未走完的 bar 误判）
- TickFlow 盘中返回的最后一根可能是"正在累积、未走完"的 5m bar，量偏小 → 会误报缩量。
- 规则：取**倒数第二根**（最后一根已完整收盘的 5m bar）作为"当前根"。即每轮判定的是"刚刚走完的那 5 分钟"。这与 5 分钟扫描频率天然对齐。
- 边界：尾盘最后一根（`14:55–15:00`）要到 15:00 之后才"已收"，而那时市场阶段已是 `POSTMARKET`（不在运行时段），故这根**必然不被覆盖**。这是明确的已知取舍（不为最后一根单独加逻辑，YAGNI）；若用户在意尾盘放量需另行扩展运行时段。

### 4.4 判定（detector，纯函数）
```
ratio = current_volume / baseline_volume      # baseline_volume > 0 已由 baseline 层保证
if ratio >= up_ratio:   signal = SURGE   (放量)
elif ratio <= down_ratio: signal = SHRINK (缩量)
else:                   signal = NORMAL
返回 VolumeSignal(type, ratio, current_volume, baseline_volume)
```

### 4.5 飞书汇总消息样式（草案）
```
📊 盘中量能异动 14:05（5分钟）
🔴 放量
  · 招商银行 600036  量比 2.4x  现价 38.56 (+1.2%)
  · 京东方A 000725  量比 3.1x  现价 4.12 (-0.8%)
🔵 缩量
  · XX股份 0000XX  量比 0.4x  现价 ...
```
- 现价/涨跌幅取自当根 5m bar 的 close，或顺带调一次 `get_realtime_quote`（可选，能省则省；MVP 可只用 K 线 close 与较前收盘的涨跌幅）。MVP 实现以 K 线数据为准，不额外调实时报价。

## 5. 错误处理与边界

| 场景 | 处理 |
|------|------|
| 非交易时段/非交易日 | `infer_market_phase` 判定后直接 return，不取数 |
| `infer_market_phase` 返回 UNKNOWN | 视为不在运行时段，跳过本轮（fail-closed，不误推） |
| 某股 `get_intraday_kline` 返回 None / 空 | 跳过该股，计入 skipped，不影响其它股 |
| 基线数据不足 | 该 slot 基线 None → 跳过该股该轮 |
| TickFlow 429 限流 | **监控器不处理**。TickFlow 数据源层 `_call_with_intraday_retry` 已对分钟K做限流/瞬时失败重试，失败后**降级返回 None**；manager 层亦吞异常返回 None。故监控器收不到 429 异常，统一按"返回 None → 跳过该股（skipped）"处理。轮内**串行**取数使标的数远低于 60/min，正常不触发限流。**严禁**在监控器内自行重试或去 patch 数据源层（违反铁律）。|
| 飞书推送失败 | `send()` 返回 False → 记日志告警；**已加入 already_alerted 的项不回滚**（避免下一轮重推刷屏），当日不再补推该股该类型 |
| 监控器内部异常 | `run_once()` 整体 try/except 包裹，异常只记日志、返回空统计，绝不让后台任务崩溃影响 analyzer 主调度（与 alert_worker 同纪律）|
| 标的解析失败（portfolio 异常） | 退化为仅 STOCK_LIST；portfolio 读取异常不阻断监控 |

## 6. 配置项（`data/.env`，均带默认值，留空走默认）

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `INTRADAY_VOLUME_MONITOR_ENABLED` | `false` | 总开关 |
| `INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES` | `5` | 扫描间隔（分钟） |
| `INTRADAY_VOLUME_SURGE_RATIO` | `2.0` | 放量量比阈值（≥ 触发） |
| `INTRADAY_VOLUME_SHRINK_RATIO` | `0.5` | 缩量量比阈值（≤ 触发） |
| `INTRADAY_VOLUME_BASELINE_DAYS` | `20` | 同时段基线回看交易日数 |
| `INTRADAY_VOLUME_BASELINE_MIN_SAMPLES` | `5` | slot 最少样本数，不足则跳过 |
| `INTRADAY_VOLUME_INCLUDE_HOLDINGS` | `true` | 是否把持仓股并入监控范围 |

> 改配置后需重建容器：`docker compose -f docker/docker-compose.yml up -d`（与项目现有流程一致）。

## 7. 测试策略

- **detector.py（纯函数）单测**：放量边界（2.0 命中、1.99 不命中）、缩量边界（0.5 命中、0.51 不命中）、正常区间、baseline=0 防护。
- **baseline.py 单测**：用构造的 5m DataFrame 验证 slot 分组均值、样本不足返回 None、跨日缓存失效。
- **universe.py 单测**：STOCK_LIST ∪ 持仓去重、持仓为空、portfolio 抛异常时退化。
- **monitor 编排单测**：mock manager / notifier / trading_calendar，验证：非交易时段不取数、当日去重（同股同类型只推一次）、本轮无 hits 不推送、429 退避路径、单股失败不影响整轮。
- **CI 守绿**：跑 `./scripts/ci_gate.sh`，确认未触碰数据源铁律相关测试。
- **生产烟雾**：开启开关后在盘中观察一轮日志（标的数、扫描数、命中数），人工核对一条飞书消息；确认非交易时段空转、无 429。

## 8. 部署与上线

1. 代码合入后，在 `data/.env` 加入开关与参数（至少 `INTRADAY_VOLUME_MONITOR_ENABLED=true`）。
2. `docker compose -f docker/docker-compose.yml up -d` 重建 analyzer 容器。
3. 盘中验证：`docker compose logs -f analyzer` 看 `intraday_volume_monitor` 轮次日志；命中后核对飞书。
4. 回归：`./scripts/ci_gate.sh` 绿 + 确认实时/日 K 来源仍 TickFlow、akshare 仍末位（铁律烟雾）。

## 9. 风险与取舍

- **5 分钟颗粒度**：抓不到分钟内瞬时异动，用户已接受。
- **未走完 bar**：用倒数第二根规避误判，代价是每根延迟一轮（最多 5 分钟）确认，可接受。
- **TickFlow 月总量配额**：本次只实测出 60/min 速率限制，未测月总量；按 ~标的数×48/日 量级估算很小，若套餐有月总量上限需另行确认（不阻塞本设计）。
- **持仓股数量动态**：账户持仓多时标的数上升，但仍远低于 60/min；若未来标的数 > 60 需加分批限速（当前 YAGNI，仅在文档备注）。
