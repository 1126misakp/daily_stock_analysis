# -*- coding: utf-8 -*-
import asyncio
import time

import pytest

from api.v1.endpoints import screen as ep
from src.services.screen_jobs import ScreenJobStore


@pytest.fixture(autouse=True)
def _reset_store_and_stub(monkeypatch):
    # 单例跨用例串扰 + 幂等复用进行中 job → 每个用例必须重置
    ScreenJobStore._instance = None
    # 端点用 `from src.services.stock_screener import run_screen`，故 patch 端点模块属性
    monkeypatch.setattr(ep, "run_screen",
                        lambda **k: {"enabled": True, "candidates": [], "candidateCount": 0})
    yield
    ScreenJobStore._instance = None


def _wait(job_id):
    store = ScreenJobStore.get_instance()
    for _ in range(100):
        j = store.get(job_id)
        if j and j.status in ("completed", "failed"):
            return j
        time.sleep(0.02)
    return store.get(job_id)


def test_strategies_lists_eight():
    res = asyncio.run(ep.list_strategies())
    ids = {s["id"] for s in res["strategies"]}
    assert {"ma_golden_cross", "volume_breakout", "bottom_volume", "shrink_pullback",
            "one_yang_three_yin", "growth_quality", "box_oscillation", "bull_trend"} <= ids
    assert all("tradingStyle" in s or "trading_style" in s for s in res["strategies"])


def test_submit_requires_strategy_or_preference():
    with pytest.raises(Exception):
        asyncio.run(ep.submit_screen_job(ep.ScreenJobRequest(strategy="", preference="", max_results=20)))


def test_submit_returns_job_id():
    req = ep.ScreenJobRequest(strategy="ma_golden_cross", preference="", max_results=5)
    res = asyncio.run(ep.submit_screen_job(req))
    jid = res.get("jobId") or res.get("job_id")
    assert jid
    done = _wait(jid)               # 等后台 job 落定（已 stub run_screen，不联网）
    assert done.status == "completed"


def test_get_job_pure_memory():
    with pytest.raises(Exception):  # 不存在的 job 返回 404
        asyncio.run(ep.get_screen_job("nonexistent"))
