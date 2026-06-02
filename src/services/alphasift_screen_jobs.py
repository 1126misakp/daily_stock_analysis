# -*- coding: utf-8 -*-
"""AlphaSift 选股异步 job 存储（内存、单 worker、幂等复用）。

选股是耗时 ~7 分钟的全市场扫描，必须异步化以绕开前置 CDN 的 100s 超时上限。
结果仅存内存、不持久化（探索性操作，刷新/重启即弃）。
"""

from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ScreenRunFn = Callable[..., Dict[str, Any]]

_ACTIVE_STATUSES = ("pending", "running")
_FINISHED_STATUSES = ("completed", "failed")


@dataclass
class ScreenJob:
    job_id: str
    market: str
    strategy: str
    max_results: int
    status: str = "pending"  # pending | running | completed | failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class AlphaSiftScreenJobStore:
    """内存 job 存储。单例通过 get_instance() 获取；测试可直接实例化。"""

    MAX_JOBS = 20
    TTL_SECONDS = 3600

    _instance: Optional["AlphaSiftScreenJobStore"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: Dict[str, ScreenJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="alphasift_screen_"
        )

    @classmethod
    def get_instance(cls) -> "AlphaSiftScreenJobStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def submit(
        self,
        market: str,
        strategy: str,
        max_results: int,
        run_fn: ScreenRunFn,
    ) -> ScreenJob:
        with self._lock:
            active = self._active_job_locked()
            if active is not None:
                logger.info(
                    "AlphaSift 选股已有进行中任务 %s，复用并忽略本次参数 strategy=%s max_results=%s",
                    active.job_id, strategy, max_results,
                )
                return copy.copy(active)  # 幂等复用进行中的任务，绝不排队
            job = ScreenJob(
                job_id=uuid.uuid4().hex[:12],
                market=market,
                strategy=strategy,
                max_results=max_results,
            )
            self._jobs[job.job_id] = job
            self._cleanup_locked()  # 插入后清理：TTL 过期 + 容量封顶（只淘汰已完成 job）
            logger.info(
                "AlphaSift 选股任务已提交 job_id=%s strategy=%s market=%s",
                job.job_id, strategy, market,
            )
            # 提交时刻的快照：保证调用方拿到的对象反映「提交时」状态（pending），
            # 不会被 worker 线程异步改写；存储里仍保留 live 对象供 get() 反映最新进度。
            snapshot = copy.copy(job)
        self._executor.submit(self._run, job, run_fn)
        return snapshot

    def get(self, job_id: str) -> Optional[ScreenJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: ScreenJob, run_fn: ScreenRunFn) -> None:
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = run_fn(
                market=job.market,
                strategy=job.strategy,
                max_results=job.max_results,
            )
            job.status = "completed"
            logger.info("AlphaSift 选股任务完成 job_id=%s", job.job_id)
        except Exception as exc:  # noqa: BLE001 - 任何失败都落到 job.error
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict) and detail.get("message"):
                job.error = str(detail["message"])
            else:
                job.error = str(exc) or exc.__class__.__name__
            job.status = "failed"
            logger.exception("AlphaSift 选股任务失败 job_id=%s", job.job_id)
        finally:
            job.finished_at = time.time()

    def _active_job_locked(self) -> Optional[ScreenJob]:
        for job in self._jobs.values():
            if job.status in _ACTIVE_STATUSES:
                return job
        return None

    def _cleanup_locked(self) -> None:
        now = time.time()
        # 1) 丢弃超过 TTL 的已完成 job
        for job_id in list(self._jobs):
            job = self._jobs[job_id]
            if (
                job.status in _FINISHED_STATUSES
                and job.finished_at is not None
                and now - job.finished_at > self.TTL_SECONDS
            ):
                del self._jobs[job_id]
        # 2) 容量上限：超出则丢最旧的已完成 job（绝不丢活跃 job）
        if len(self._jobs) > self.MAX_JOBS:
            finished = sorted(
                (j for j in self._jobs.values() if j.status in _FINISHED_STATUSES),
                key=lambda j: j.created_at,
            )
            overflow = len(self._jobs) - self.MAX_JOBS
            for job in finished[:overflow]:
                self._jobs.pop(job.job_id, None)
