# -*- coding: utf-8 -*-
"""自研选股：全市场行情快照取数与特征预计算。

仅在选股流程内通过 Tushare 全市场批量接口（按交易日）取数，属独立调用路径，
不修改 data_provider 的路由/优先级/fetcher 链（数据源优先铁律不受影响）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _bare_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0]


@dataclass
class MarketPanel:
    trade_date: str
    latest: pd.DataFrame
    history: Dict[str, pd.DataFrame]
    basic: pd.DataFrame
    names: Dict[str, str] = field(default_factory=dict)
    industry: Dict[str, str] = field(default_factory=dict)

    @property
    def universe_size(self) -> int:
        return int(len(self.latest))


def build_panel_from_frames(daily, basic, names, industry, trade_date) -> MarketPanel:
    """从已取好的全市场多日 daily + 最新 daily_basic 构造 MarketPanel。"""
    daily = daily.copy()
    daily["code"] = daily["ts_code"].map(_bare_code)
    daily = daily.sort_values(["code", "trade_date"])

    history: Dict[str, pd.DataFrame] = {}
    rows: List[dict] = []
    for code, grp in daily.groupby("code"):
        g = grp.reset_index(drop=True)
        history[code] = g
        closes = g["close"].astype(float)
        vols = g["vol"].astype(float)
        last = g.iloc[-1]
        ma5 = closes.tail(5).mean()
        ma10 = closes.tail(10).mean()
        ma20 = closes.tail(20).mean()
        ma5_prev = closes.iloc[-6:-1].mean() if len(closes) >= 6 else ma5
        ma10_prev = closes.iloc[-11:-1].mean() if len(closes) >= 11 else ma10
        # 量比口径：当日量能 / 此前 5 日均量（不含当日），与各 scorer 阈值一致
        vol_ma5 = vols.iloc[-6:-1].mean() if len(vols) >= 6 else vols.tail(5).mean()
        vol_ratio = float(last["vol"]) / vol_ma5 if vol_ma5 else 0.0
        high_20 = g["high"].tail(20).astype(float).max()
        low_30 = g["low"].tail(30).astype(float).min()
        # change_pct：优先用 Tushare daily 自带 pct_chg（百分比→小数），否则用收盘价回推
        if "pct_chg" in g.columns and pd.notna(last.get("pct_chg")):
            change_pct = float(last["pct_chg"]) / 100.0
        elif len(closes) >= 2:
            change_pct = float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1.0
        else:
            change_pct = 0.0
        rows.append({
            "code": code, "name": names.get(code, code),
            "close": float(last["close"]), "open_": float(last["open"]),
            "high": float(last["high"]), "low": float(last["low"]),
            "vol": float(last["vol"]), "amount": float(last["amount"]),
            "change_pct": change_pct,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "ma5_prev": ma5_prev, "ma10_prev": ma10_prev,
            "vol_ma5": vol_ma5, "vol_ratio": vol_ratio,
            "high_20": high_20, "low_30": low_30,
            "ret_from_high20": (float(last["close"]) / high_20 - 1) if high_20 else 0.0,
            "bias_ma5": (float(last["close"]) / ma5 - 1) if ma5 else 0.0,
            "industry": industry.get(code, ""),
        })
    latest = pd.DataFrame(rows).set_index("code")

    b = basic.copy()
    if "ts_code" in b.columns:
        b["code"] = b["ts_code"].map(_bare_code)
        b = b.set_index("code")
    for col in ("pe", "pb", "total_mv", "turnover_rate", "volume_ratio"):
        if col in b.columns:
            latest[col] = b[col]
        else:
            latest[col] = pd.NA
    return MarketPanel(trade_date=trade_date, latest=latest, history=history,
                       basic=b, names=names, industry=industry)


def _get_tushare():
    """复用 history_loader 的共享 fetcher manager，拿 TushareFetcher（共享限频）。"""
    from src.services.history_loader import _get_fetcher_manager
    manager = _get_fetcher_manager()
    fetcher = manager._get_fetcher_by_name("TushareFetcher")
    if fetcher is None:
        raise RuntimeError("Tushare 未配置，无法执行全市场选股取数")
    return fetcher


def _recent_trade_dates(tushare, n_days: int, end_date: Optional[str] = None) -> List[str]:
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n_days * 2 + 30)).strftime("%Y%m%d")
    cal = tushare._fundamental_df("trade_cal", exchange="SSE", start_date=start, end_date=end)
    if cal is None or cal.empty:
        raise RuntimeError("无法获取交易日历")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    return opens[-n_days:]


def fetch_market_panel(n_days: int = 60, end_date: Optional[str] = None) -> MarketPanel:
    """全市场近 n_days 日行情快照。约 n_days 次 daily + 1 次 daily_basic + 1 次 stock_basic。"""
    tushare = _get_tushare()
    dates = _recent_trade_dates(tushare, n_days, end_date)
    if not dates:
        raise RuntimeError("无可用交易日")
    frames = []
    EXPECTED_MIN_ROWS = 4000   # A股全市场实测约 5507 行，明显偏少视为截断/限权
    for d in dates:
        df = tushare._fundamental_df("daily", trade_date=d)
        if df is not None and not df.empty:
            if len(df) < EXPECTED_MIN_ROWS:
                logger.warning("[选股取数] %s daily 仅 %d 行，疑似被截断或积分受限", d, len(df))
            frames.append(df)
    if not frames:
        raise RuntimeError("Tushare daily 全市场取数为空")
    daily = pd.concat(frames, ignore_index=True)
    latest_date = dates[-1]
    basic = tushare._fundamental_df("daily_basic", trade_date=latest_date)
    if basic is None:
        basic = pd.DataFrame()
    stock_list = tushare.get_stock_list()
    names, industry = {}, {}
    if stock_list is not None and not stock_list.empty:
        code_col = "code" if "code" in stock_list.columns else "ts_code"
        for _, r in stock_list.iterrows():
            c = _bare_code(r[code_col])
            names[c] = str(r.get("name", c))
            industry[c] = str(r.get("industry", "") or "")
    return build_panel_from_frames(daily, basic, names, industry, trade_date=latest_date)
