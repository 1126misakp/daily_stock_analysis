# -*- coding: utf-8 -*-
import pandas as pd
import pytest

from src.services.stock_screener import engine
from src.services.stock_screener.market_data import MarketPanel


def _panel():
    latest = pd.DataFrame([
        {"code": "000001", "name": "科技A", "industry": "半导体", "close": 10, "amount": 1e8,
         "ma5": 10.1, "ma10": 10.0, "ma5_prev": 9.9, "ma10_prev": 10.0, "vol_ratio": 1.5, "bias_ma5": 0.01,
         "change_pct": 0.01, "pe": 30, "pb": 3, "total_mv": 8e6, "high_20": 9.9, "ma20": 9.8, "ret_from_high20": 0.01, "low_30": 9},
        {"code": "000002", "name": "银行B", "industry": "银行", "close": 20, "amount": 2e8,
         "ma5": 19, "ma10": 20, "ma5_prev": 18.9, "ma10_prev": 20, "vol_ratio": 1.5, "bias_ma5": -0.05,
         "change_pct": -0.01, "pe": 6, "pb": 0.8, "total_mv": 5e7, "high_20": 21, "ma20": 20.5, "ret_from_high20": -0.05, "low_30": 19},
    ]).set_index("code")
    return MarketPanel(trade_date="20260602", latest=latest, history={}, basic=pd.DataFrame(),
                       names={"000001": "科技A", "000002": "银行B"},
                       industry={"000001": "半导体", "000002": "银行"})


def test_empty_inputs_rejected(monkeypatch):
    with pytest.raises(engine.ScreenInputError):
        engine.run_screen(strategy=None, preference="", max_results=20)


def test_preference_without_board_rejected(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "_extract_boards", lambda pref, industries: [])
    with pytest.raises(engine.ScreenInputError):
        engine.run_screen(strategy=None, preference="激进", max_results=20)


def test_strategy_only_runs(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "rerank",
        lambda cands, **k: {"candidates": cands, "llm_ranked": False,
                            "llm_selection_logic": "", "llm_portfolio_risk": "", "warnings": []})
    res = engine.run_screen(strategy="ma_golden_cross", preference="", max_results=20)
    assert res["after_filter_count"] == 1
    assert res["candidates"][0]["code"] == "000001"


def test_strategy_plus_preference_does_not_hard_filter(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "_extract_boards", lambda pref, industries: ["银行"])
    monkeypatch.setattr(engine, "rerank", lambda cands, **k: {"candidates": cands, "llm_ranked": True,
        "llm_selection_logic": "", "llm_portfolio_risk": "", "warnings": []})
    # 金叉命中 000001(半导体)，偏好限定银行：第1段不硬过滤，候选保留交 LLM 优先满足偏好
    res = engine.run_screen(strategy="ma_golden_cross", preference="只看银行", max_results=20)
    assert res["after_filter_count"] == 1
    assert any("偏好" in w for w in res["warnings"])
