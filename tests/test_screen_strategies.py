# -*- coding: utf-8 -*-
import pandas as pd

from src.services.stock_screener.market_data import MarketPanel
from src.services.stock_screener import strategies as st


def _panel(latest_rows, history=None):
    latest = pd.DataFrame(latest_rows).set_index("code")
    return MarketPanel(trade_date="20260602", latest=latest,
                       history=history or {}, basic=pd.DataFrame(),
                       names={r["code"]: r["name"] for r in latest_rows},
                       industry={r["code"]: r.get("industry", "") for r in latest_rows})


def test_ma_golden_cross_hits():
    # 命中：今日 ma5>ma10 且昨日 ma5<=ma10（金叉），量比>1.2，乖离<5%
    rows = [{"code": "000001", "name": "A", "close": 10.2, "ma5": 10.1, "ma10": 10.0,
             "ma5_prev": 9.9, "ma10_prev": 10.0, "vol_ratio": 1.5, "bias_ma5": 0.01},
            {"code": "000002", "name": "B", "close": 10.2, "ma5": 9.0, "ma10": 10.0,   # 无金叉
             "ma5_prev": 8.9, "ma10_prev": 10.0, "vol_ratio": 1.5, "bias_ma5": 0.01}]
    out = st.STRATEGY_SCORERS["ma_golden_cross"](_panel(rows))
    assert list(out["code"]) == ["000001"]
    assert out.iloc[0]["signal_score"] > 0


def test_volume_breakout_hits():
    rows = [{"code": "000001", "name": "A", "close": 11.0, "high_20": 10.9, "vol_ratio": 2.5,
             "bias_ma5": 0.02, "ma5": 10.8},
            {"code": "000002", "name": "B", "close": 10.0, "high_20": 10.9, "vol_ratio": 2.5,  # 未破高
             "bias_ma5": 0.02, "ma5": 10.8}]
    out = st.STRATEGY_SCORERS["volume_breakout"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_bottom_volume_hits():
    rows = [{"code": "000001", "name": "A", "close": 8.5, "open_": 8.3, "high_20": 10.0,  # 跌幅>15%
             "low_30": 8.4, "vol_ratio": 3.5, "ret_from_high20": -0.16},
            {"code": "000002", "name": "B", "close": 9.9, "open_": 9.8, "high_20": 10.0,   # 跌幅不足
             "low_30": 9.7, "vol_ratio": 3.5, "ret_from_high20": -0.01}]
    out = st.STRATEGY_SCORERS["bottom_volume"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_shrink_pullback_hits():
    rows = [{"code": "000001", "name": "A", "close": 10.05, "ma5": 10.0, "ma10": 9.8, "ma20": 9.6,
             "vol_ratio": 0.6, "bias_ma5": 0.005},
            {"code": "000002", "name": "B", "close": 10.05, "ma5": 10.0, "ma10": 9.8, "ma20": 9.6,
             "vol_ratio": 1.5, "bias_ma5": 0.005}]  # 未缩量
    out = st.STRATEGY_SCORERS["shrink_pullback"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_bull_trend_hits():
    rows = [{"code": "000001", "name": "A", "close": 10.5, "ma5": 10.3, "ma10": 10.1, "ma20": 10.0, "bias_ma5": 0.02},
            {"code": "000002", "name": "B", "close": 9.0, "ma5": 9.5, "ma10": 10.1, "ma20": 10.0, "bias_ma5": -0.05}]
    out = st.STRATEGY_SCORERS["bull_trend"](_panel(rows))
    assert list(out["code"]) == ["000001"]
