# -*- coding: utf-8 -*-
"""自研选股 API：策略列表、异步选股 job 提交与查询。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agent.skills.base import load_skills_from_directory, _BUILTIN_SKILLS_DIR
from src.services.screen_jobs import ScreenJobStore
from src.services.stock_screener import run_screen, ScreenInputError

logger = logging.getLogger(__name__)
# prefix 不在此写：与现有所有端点一致，由 api/v1/router.py 的 include_router(prefix="/screen") 提供
router = APIRouter(tags=["screen"])

_ONLINE_STRATEGIES = [
    "ma_golden_cross", "volume_breakout", "bottom_volume", "shrink_pullback",
    "one_yang_three_yin", "growth_quality", "box_oscillation", "bull_trend",
]


class ScreenJobRequest(BaseModel):
    strategy: Optional[str] = Field(default=None, max_length=64)
    preference: Optional[str] = Field(default=None, max_length=500)
    max_results: int = Field(default=20, ge=1, le=100)


@router.get("/strategies")
async def list_strategies():
    by_name = {s.name: s for s in load_skills_from_directory(_BUILTIN_SKILLS_DIR)}
    strategies = []
    for sid in _ONLINE_STRATEGIES:
        sk = by_name.get(sid)
        if not sk:
            continue
        strategies.append({"id": sk.name, "name": sk.display_name, "category": sk.category,
                           "description": sk.description,
                           "trading_style": getattr(sk, "trading_style", "")})
    return {"enabled": True, "strategies": strategies, "strategyCount": len(strategies)}


@router.post("/jobs")
async def submit_screen_job(request: ScreenJobRequest):
    strategy = (request.strategy or "").strip() or None
    preference = (request.preference or "").strip() or None
    if not strategy and not preference:
        raise HTTPException(status_code=400, detail="策略和用户偏好至少填写一个")

    def _run(strategy, preference, max_results):
        try:
            return run_screen(strategy=strategy, preference=preference, max_results=max_results)
        except ScreenInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    store = ScreenJobStore.get_instance()
    job = store.submit(strategy, preference, request.max_results, _run)
    return {"jobId": job.job_id, "status": job.status}


@router.get("/jobs/{job_id}")
async def get_screen_job(job_id: str):
    store = ScreenJobStore.get_instance()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="选股任务不存在或已过期")
    payload = {"jobId": job.job_id, "status": job.status, "error": job.error}
    if job.result:
        payload.update(job.result)
    return payload
