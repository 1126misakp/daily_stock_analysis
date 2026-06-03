# 盘中分钟级量能（放量/缩量）监控

## 是什么
交易时段内每 N 分钟（默认 5）扫描“自选股 ∪ 持仓股(仅 A 股)”的 5 分钟 K 线成交量，
与近 N 交易日（默认 20）同一时刻的 5 分钟均量对比：
- 量比 ≥ `INTRADAY_VOLUME_SURGE_RATIO`（默认 2.0）→ 放量
- 量比 ≤ `INTRADAY_VOLUME_SHRINK_RATIO`（默认 0.5）→ 缩量
同一股票、同一类型当日仅首次提醒；本轮所有异动合并成一条飞书消息推送。

## 与告警中心 `volume_spike` 的区别
- 告警中心 `volume_spike`：**日线级**（今日总量 vs 近 20 日均量），用户在告警页逐条建规则，由 `AGENT_EVENT_MONITOR_ENABLED` 驱动。
- 本功能：**盘中 5 分钟级**，自动对一组股票跑，独立 `.env` 开关，二者互不影响。

## 配置（`data/.env`）
| 配置项 | 默认 | 说明 |
|--------|------|------|
| `INTRADAY_VOLUME_MONITOR_ENABLED` | false | 总开关 |
| `INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES` | 5 | 扫描间隔（分钟） |
| `INTRADAY_VOLUME_SURGE_RATIO` | 2.0 | 放量量比阈值（≥ 触发） |
| `INTRADAY_VOLUME_SHRINK_RATIO` | 0.5 | 缩量量比阈值（≤ 触发） |
| `INTRADAY_VOLUME_BASELINE_DAYS` | 20 | 同时段基线回看交易日数 |
| `INTRADAY_VOLUME_BASELINE_MIN_SAMPLES` | 5 | slot 最少样本，不足跳过 |
| `INTRADAY_VOLUME_INCLUDE_HOLDINGS` | true | 是否并入持仓股(仅 A 股) |

## 运行机制
- 仅在交易日连续竞价 + 尾盘集合竞价（`MarketPhase ∈ {INTRADAY, CLOSING_AUCTION}`）运行；非交易时段空转零成本。
- 取数只经 TickFlow 分钟K（`get_intraday_kline`，限流 60/min，本负载远低于此）。
- 仅在 `stock-analyzer` 容器（`--schedule` 模式）生效。
- 已知取舍：尾盘 14:55–15:00 这根因在收盘后才“已收”、那时已转 POSTMARKET，**必然不被覆盖**。

## 上线
1. `data/.env` 设 `INTRADAY_VOLUME_MONITOR_ENABLED=true`（及需要调整的阈值）。
2. `docker compose -f docker/docker-compose.yml up -d` 重建 analyzer 容器。
3. 盘中 `docker compose logs -f analyzer` 看 `[IntradayVolume]` 轮次日志，并核对一条飞书消息。
