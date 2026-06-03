# 设计：盘中实时量能 Agent 工具 + 飞书推送加股票名

日期：2026-06-03
分支：`feat/intraday-volume-agent-tool`
状态：已确认，进入实现

## 背景

WebUI「问股」Chat 走多智能体编排（`AGENT_ARCH=multi`），其中技术面 `TechnicalAgent`
只能用 `tool_names` 白名单里的工具，目前没有任何工具能感知**盘中实时量能**——
agent 现有的 `get_volume_analysis` / `analyze_pattern` 全部基于**日线**历史数据。

项目已有一套盘中量能监控（`src/services/intraday_volume/`）按 5 分钟时段把当日量能与
历史同时段均量比对，判定放量/缩量并推飞书。本设计在**不改动**这套已上线监控数据流的前提下，
把同一套判定口径暴露成一个 agent 工具，让问股 Chat 的技术面分析能用上盘中量能。

顺带优化一处：盘中量能飞书推送当前只显示股票代码，本次在代码后补上股票名称。

## 范围

- 改动①（主）：新增 agent 工具 `get_intraday_volume`。
- 改动②（顺手）：盘中量能飞书推送 `_render` 在股票代码后加股票名称。

不在范围：不新增配置项；不改 `data_provider` 路由/优先级/fetcher 链（数据源铁律）；
不改每日 18:00 legacy 分析链路；不改已上线监控的扫描/去重/推送语义。

## 改动① get_intraday_volume 工具

### 选型

复用现成纯函数零件：`detector.classify`、`baseline.BaselineProvider` /
`compute_slot_baselines` / `_slot_of` / `_date_of`、`trading_calendar` 的
`get_market_now` / `infer_market_phase` / `MarketPhase`，以及数据层
`DataFetcherManager.get_intraday_kline(code, "5m", count)`。不复用 `IntradayVolumeMonitor`
（它面向多股扫描+去重+推送，耦合 notifier，不适合单股即时查询）。

### 工具签名（暴露给 LLM）

- `name = "get_intraday_volume"`，`category = "analysis"`
- 参数仅 `stock_code`（string，必填）。周期固定 `5m`；基线天数/放缩量阈值取**现有配置**
  （`intraday_volume_baseline_days` / `intraday_volume_surge_ratio` /
  `intraday_volume_shrink_ratio` / `intraday_volume_baseline_min_samples`），不暴露给 LLM。
- `description`（英文，与其它工具一致）：说明返回盘中 5 分钟量能（量比、放量/缩量判定、
  今日累计量、最近几根 bar），口径与盘中飞书告警一致，适合判断盘中异动。

### handler 行为 `_handle_get_intraday_volume(stock_code) -> dict`

1. 取数：`manager.get_intraday_kline(code, period="5m", count=(baseline_days+5)*48)`。
   - 返回 `None`/空/非 A 股 → 返回 `{"stock_code", "error": "无盘中量能数据（非A股或TickFlow无数据）"}`。
2. 市场状态：`now = get_market_now("cn")`；`phase = infer_market_phase("cn", now)`
   （**市场码必须小写 `cn`**）。`market_phase = phase.value`。
3. 选定「参考 bar」：
   - 盘中（`INTRADAY`/`CLOSING_AUCTION`）：取**倒数第二根**（最后一根可能未收满），
     与监控一致；不足 2 根则取最后一根。
   - 其它时段：取**最后一根** bar。
   - `as_of_note` 据 `_date_of(参考bar.datetime)` 与 `now` 日期/`phase` 推导：
     `"盘中实时"` / `"已收盘，为今日最后数据"` / `"非交易时段/非交易日，为最近交易日(YYYY-MM-DD)数据"`。
4. 基线与判定：构造 `BaselineProvider(manager, baseline_days=, min_samples=)`，
   `slot = _slot_of(参考bar.datetime)`，`ref_date = _date_of(参考bar.datetime)`，
   `baseline = provider.get_slot_baseline(code, slot, today_str=ref_date)`。
   - 说明：`compute_slot_baselines` 取 `__date < today_str` 的历史样本。`today_str` 直接传
     **参考 bar 的日期 `ref_date`**：`__date < ref_date` 会排除参考 bar 当天、只用其之前的同 slot
     历史均量。盘中场景 `ref_date == 今天`，非交易日场景 `ref_date == 最近交易日`，两种情况都正确，
     与监控同口径。
   - `signal = classify(current_volume=参考bar.volume, baseline, surge_ratio=, shrink_ratio=)`。
   - baseline 缺失（新股/样本不足）→ `classify` 返回 `normal, ratio=None`；handler 在结果里加
     `"note": "无足够历史基线，量比判定不可用"`，不报错。
5. 今日累计量：对参考 bar 同日（`_date_of == ref_date`）的所有 bar 的 volume 求和。
6. 价/涨跌幅：`price = float(参考bar.close)`（零额外调用）；`change_pct` 尽力而为——
   `manager.get_realtime_quote(code)` 取 `.change_pct`，任何异常/None → `null`。
7. 最近 bar 明细：参考 bar 往前最多 6 根同日 bar，每根 `{time(slot), volume, ratio}`，
   `ratio = round(volume/同slot基线, 2)`，基线缺失则 `ratio=None`。
8. 全程 try/except，任何子步失败降级为字段缺省/note，**绝不抛异常**（agent 工具必须稳）。

### 返回结构

```jsonc
{
  "stock_code": "600036",
  "stock_name": "招商银行",
  "market_phase": "intraday",
  "data_time": "2026-06-03 10:05",
  "as_of_note": "盘中实时",
  "latest_bar": {
    "slot": "10:05", "volume": 12345.0,
    "baseline_volume": 6000.0, "ratio": 2.06,
    "verdict": "surge", "verdict_cn": "放量"
  },
  "today_cumulative_volume": 456789.0,
  "price": 38.20,
  "change_pct": 1.23,
  "recent_bars": [{"time": "09:45", "volume": 8000.0, "ratio": 1.3}, ...],
  "thresholds": {"surge": 2.0, "shrink": 0.5},
  "note": null
}
```

`verdict` 取值 `surge`/`shrink`/`normal`；`verdict_cn` 为 `放量`/`缩量`/`正常`。

### 集成（两处）

1. `src/agent/tools/analysis_tools.py`：定义 `get_intraday_volume_tool`，追加进 `ALL_ANALYSIS_TOOLS`。
2. `src/agent/agents/technical_agent.py`：`tool_names` 列表加 `"get_intraday_volume"`。

### 生效范围

服务于走 agent 的路径（问股 Chat / multi 架构技术面 agent）。每日 18:00 legacy 报告不受影响。

## 改动② 飞书推送加股票名

- `src/services/intraday_volume_monitor.py`：
  - `_scan_one` 返回的 hit dict 增加 `"name": self._get_manager().get_stock_name(code) or ""`
    （`get_stock_name` 静态映射优先、已缓存，失败返回 None → 用空串兜底）。
  - `_render` 每行由 `· {code} 量比...` 改为 `· {code} {name} 量比...`（name 为空则退化为仅代码，
    前后无多余空格）。
- 不改其它推送逻辑、去重、阶段门控。

## 测试（unittest + MagicMock，`pytest -m "not network"`）

新增 `tests/test_get_intraday_volume_tool.py`：用 MagicMock 的 manager 喂构造好的 5m DataFrame，覆盖
1. 盘中放量（ratio≥surge → verdict surge/放量）；
2. 盘中缩量（ratio≤shrink → shrink/缩量）；
3. normal（中间比值）；
4. baseline 缺失（历史样本不足 → ratio None + note，不报错）；
5. 非 A 股 / 取数 None → error 字段；
6. 非交易日/盘后 → as_of_note 标注最近交易日、取最后一根 bar；
7. 取数抛异常 → 降级不抛错；
8. change_pct：realtime quote 异常 → null。

扩展 `tests/test_intraday_volume_monitor.py`（或新增用例）：验证 hit dict 含 `name`、
`_render` 输出包含股票名；`get_stock_name` 返回 None 时退化为仅代码不报错。

## 验证与上线

1. 本地 `pytest -m "not network"` 全绿（守住基线）+ `./scripts/ci_gate.sh` 绿。
2. commit（英文 message、不加 Co-Authored-By）→ push origin `feat/intraday-volume-agent-tool` → 合入 main。
3. 服务器 `git pull`（保留 docker-compose 的 env_file/ENV_FILE 本地改动）→ 重建容器。
4. 生产烟雾：容器内构建 orchestrator 跑一次技术面分析，确认 `get_intraday_volume` 可被调用、
   返回结构正确、不抛错；确认数据源体系未受影响（实时/日K 仍 TickFlow 主力）。
5. 更新 `docs/CHANGELOG.md`（[Unreleased]）。

## 铁律合规

仅调用既有 `get_intraday_kline`（TickFlow 本就是体系内主力），不触碰 `data_provider`
路由/优先级/fetcher 链；新增 agent 工具独立于数据源铁律。不动已上线监控与选股引擎。
