# -*- coding: utf-8 -*-
from src.services.stock_screener import ranker

CANDS = [
    {"code": "000001", "name": "A", "signal_score": 12, "signal_detail": "金叉", "close": 10, "change_pct": 0.01, "industry": "科技"},
    {"code": "000002", "name": "B", "signal_score": 8, "signal_detail": "金叉", "close": 20, "change_pct": -0.01, "industry": "医药"},
]


def test_rerank_llm_failure_falls_back_to_signal_score(monkeypatch):
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="", max_results=5)
    assert res["llm_ranked"] is False
    assert [c["code"] for c in res["candidates"]] == ["000001", "000002"]  # 按 signal_score 降序
    assert res["candidates"][0]["rank"] == 1
    assert any("LLM" in w for w in res["warnings"])


def test_rerank_uses_llm_order(monkeypatch):
    fake = '{"selection_logic":"偏好科技","portfolio_risk":"集中科技","ranking":[{"code":"000002","reason":"更稳","thesis":"t","risks":["r"],"style_fit":"贴合"}]}'
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: fake)
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="喜欢医药", max_results=5)
    assert res["llm_ranked"] is True
    assert res["candidates"][0]["code"] == "000002"
    assert res["candidates"][0]["reason"] == "更稳"
    assert res["llm_selection_logic"] == "偏好科技"
