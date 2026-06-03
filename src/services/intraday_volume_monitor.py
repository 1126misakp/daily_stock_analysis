# -*- coding: utf-8 -*-
"""盘中分钟级量能监控器：编排一轮扫描并合并推送。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from src.core.trading_calendar import MarketPhase, get_market_now, infer_market_phase
from src.services.intraday_volume.baseline import BaselineProvider, _slot_of, _date_of
from src.services.intraday_volume.detector import (
    SIGNAL_SHRINK,
    SIGNAL_SURGE,
    VolumeSignal,
    classify,
)
from src.services.intraday_volume.universe import resolve_universe

logger = logging.getLogger(__name__)

_RUN_PHASES = {MarketPhase.INTRADAY, MarketPhase.CLOSING_AUCTION}
_LIVE_PROBE_COUNT = 50
_SIGNAL_LABEL = {SIGNAL_SURGE: "放量", SIGNAL_SHRINK: "缩量"}
_SIGNAL_EMOJI = {SIGNAL_SURGE: "🔴", SIGNAL_SHRINK: "🔵"}


class IntradayVolumeMonitor:
    def __init__(
        self,
        config_provider: Callable[[], object],
        *,
        manager=None,
        notifier=None,
        phase_fn: Optional[Callable[[datetime], MarketPhase]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self._config_provider = config_provider
        self._manager = manager
        self._notifier = notifier
        self._phase_fn = phase_fn or (lambda now: infer_market_phase("CN", now))
        self._now_fn = now_fn or (lambda: get_market_now("CN"))

        cfg = config_provider()
        self._baseline = BaselineProvider(
            self._get_manager(),
            baseline_days=int(getattr(cfg, "intraday_volume_baseline_days", 20)),
            min_samples=int(getattr(cfg, "intraday_volume_baseline_min_samples", 5)),
        )
        self._alerted: Set[Tuple[str, str]] = set()
        self._cache_date: Optional[str] = None

    # --- 懒构建依赖（默认走真实实现）---
    def _get_manager(self):
        if self._manager is None:
            from data_provider import DataFetcherManager

            self._manager = DataFetcherManager()
        return self._manager

    def _get_notifier(self):
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()
        return self._notifier

    def run_once(self) -> Dict[str, int]:
        stats = {"scanned": 0, "hits": 0, "skipped": 0, "errors": 0, "notified": 0}
        try:
            now = self._now_fn()
            phase = self._phase_fn(now)
            if phase not in _RUN_PHASES:
                return stats

            today_str = now.strftime("%Y-%m-%d")
            self._roll_day(today_str)

            cfg = self._config_provider()
            codes = resolve_universe(
                getattr(cfg, "stock_list", []) or [],
                include_holdings=bool(getattr(cfg, "intraday_volume_include_holdings", True)),
            )
            surge_ratio = float(getattr(cfg, "intraday_volume_surge_ratio", 2.0))
            shrink_ratio = float(getattr(cfg, "intraday_volume_shrink_ratio", 0.5))

            hits: List[Dict[str, object]] = []
            for code in codes:
                stats["scanned"] += 1
                outcome = self._scan_one(code, today_str, surge_ratio, shrink_ratio)
                if outcome is None:
                    stats["skipped"] += 1
                    continue
                if outcome == "error":
                    stats["errors"] += 1
                    continue
                hits.append(outcome)
                stats["hits"] += 1

            if hits:
                content = self._render(now, hits)
                ok = False
                try:
                    # 与 send_daily_report 一致：不指定 route_type，发往全部已配置渠道（飞书）。
                    # 生产未配 NOTIFICATION_*_CHANNELS，指定路由虽会回退全渠道，但此路径最稳、已验证。
                    ok = self._get_notifier().send(content)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[IntradayVolume] 飞书推送异常: %s", exc)
                if ok:
                    stats["notified"] = 1
                else:
                    logger.warning("[IntradayVolume] 本轮 %d 条命中推送失败（当日不补推）", len(hits))
        except Exception as exc:  # noqa: BLE001 - 绝不让后台任务崩溃
            logger.warning("[IntradayVolume] run_once 异常: %s", exc, exc_info=True)
        return stats

    def _roll_day(self, today_str: str) -> None:
        if self._cache_date != today_str:
            self._cache_date = today_str
            self._baseline.reset()
            self._alerted.clear()

    def _scan_one(self, code, today_str, surge_ratio, shrink_ratio):
        """返回 hit dict / None(跳过) / "error"。"""
        try:
            df = self._get_manager().get_intraday_kline(code, period="5m", count=_LIVE_PROBE_COUNT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IntradayVolume] 取数失败 %s: %s", code, exc)
            return "error"
        if df is None or getattr(df, "empty", True) or len(df) < 2:
            return None
        bar = df.iloc[-2]  # 最后一根已收 bar
        if _date_of(bar["datetime"]) != today_str:
            return None  # 本会话尚无已收 bar
        slot = _slot_of(bar["datetime"])
        baseline = self._baseline.get_slot_baseline(code, slot, today_str)
        if baseline is None:
            return None
        current_volume = float(bar["volume"])
        signal: VolumeSignal = classify(
            current_volume, baseline, surge_ratio=surge_ratio, shrink_ratio=shrink_ratio
        )
        if signal.signal_type not in _SIGNAL_LABEL:
            return None
        key = (code, signal.signal_type)
        if key in self._alerted:
            return None
        self._alerted.add(key)
        return {
            "code": code,
            "signal_type": signal.signal_type,
            "ratio": signal.ratio,
            "price": float(bar["close"]),
            "current_volume": current_volume,
            "baseline_volume": baseline,
        }

    def _render(self, now: datetime, hits: List[Dict[str, object]]) -> str:
        lines = [f"📊 盘中量能异动 {now.strftime('%H:%M')}（5分钟）"]
        for stype in (SIGNAL_SURGE, SIGNAL_SHRINK):
            group = [h for h in hits if h["signal_type"] == stype]
            if not group:
                continue
            lines.append(f"{_SIGNAL_EMOJI[stype]} {_SIGNAL_LABEL[stype]}")
            for h in group:
                lines.append(
                    f"  · {h['code']}  量比{h['ratio']:.1f}x  现价{h['price']:.2f}  "
                    f"量{h['current_volume']:.0f}(基线{h['baseline_volume']:.0f})"
                )
        return "\n".join(lines)
