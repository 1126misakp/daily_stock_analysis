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


def test_rerank_parses_markdown_fenced_json(monkeypatch):
    # MiMo 常以 ```json 代码围栏包裹，且前后带说明文字
    fake = (
        "好的，以下是排序结果：\n```json\n"
        '{"selection_logic":"偏好科技","portfolio_risk":"集中","ranking":'
        '[{"code":"000002","reason":"更稳","thesis":"t","risks":["r"],"style_fit":"贴合"}]}\n'
        "```\n以上。"
    )
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: fake)
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="喜欢科技", max_results=5)
    assert res["llm_ranked"] is True
    assert res["candidates"][0]["code"] == "000002"
    assert res["llm_selection_logic"] == "偏好科技"


def test_rerank_limits_llm_input_to_40(monkeypatch):
    # 候选 50 只时，喂给 LLM 的 prompt 中候选行数须 ≤40（按 signal_score 降序取前 40）
    cands = [
        {"code": f"{600000 + i:06d}", "name": f"S{i}", "signal_score": i,
         "signal_detail": "金叉", "close": 10, "change_pct": 0.01, "industry": "科技"}
        for i in range(50)
    ]
    captured = {}

    def fake_call(prompt):
        captured["prompt"] = prompt
        # 返回一个能命中候选的最高分股票，使其走 LLM 成功路径
        return ('{"selection_logic":"x","portfolio_risk":"y","ranking":'
                '[{"code":"600049","reason":"r","thesis":"t","risks":["r"],"style_fit":"f"}]}')

    monkeypatch.setattr(ranker, "_call_llm", fake_call)
    res = ranker.rerank(cands, strategy_desc="金叉", preference="", max_results=10)
    # 候选区块在 prompt 末尾，统计 ` | 信号:` 出现次数即候选行数
    candidate_rows = captured["prompt"].count(" | 信号:")
    assert candidate_rows == 40
    assert res["llm_ranked"] is True


def test_rerank_parses_json_with_trailing_comma(monkeypatch):
    # 带 trailing comma 的非严格 JSON 须容错解析成功
    fake = (
        '{"selection_logic":"偏好医药","portfolio_risk":"集中","ranking":'
        '[{"code":"000001","reason":"龙头","thesis":"t","risks":["r",],"style_fit":"贴合"},]}'
    )
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: fake)
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="喜欢医药", max_results=5)
    assert res["llm_ranked"] is True
    assert res["candidates"][0]["code"] == "000001"
    assert res["llm_selection_logic"] == "偏好医药"
