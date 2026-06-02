# AlphaSift 选股异步化 + LLM 接入 MiMo 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把耗时 ~7 分钟的 AlphaSift 选股从同步 HTTP 改为异步 job（提交→轮询），绕开 Cloudflare 100s 超时；并把 AlphaSift 的 LLM 重排接上本项目 MiMo。

**Architecture:** 后端抽出共享函数 `run_alphasift_screen`（含 LLM 临时环境注入），新增内存 job store（单 worker 串行）+ 两个追加式端点（提交/轮询）；前端改为提交后轮询。原同步 `/screen` 保留并复用同一函数（LLM 一并接上 MiMo）。

**Tech Stack:** FastAPI / Python（unittest）后端；React + TypeScript / Vitest 前端。

**配套设计文档：** `docs/superpowers/specs/2026-06-01-alphasift-async-screen-design.md`

---

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `api/v1/endpoints/alphasift.py` | 修改 | 抽出 `run_alphasift_screen` + `_alphasift_llm_env`；新增 `/screen/jobs`(POST) 与 `/screen/jobs/{job_id}`(GET) |
| `src/services/alphasift_screen_jobs.py` | 新建 | 内存 job store（单例、单 worker、幂等复用、TTL 清理） |
| `tests/test_alphasift_api.py` | 修改 | 扩展后端用例 |
| `tests/test_alphasift_screen_jobs.py` | 新建 | job store 单元测试 |
| `apps/dsa-web/src/api/alphasift.ts` | 修改 | 新增 `submitScreenJob` / `getScreenJob` |
| `apps/dsa-web/src/pages/StockScreeningPage.tsx` | 修改 | `handleSubmit` 改为提交+轮询 |
| `apps/dsa-web/src/api/__tests__/alphasift.test.ts` | 修改 | 新增 client 用例 |
| `apps/dsa-web/src/pages/__tests__/StockScreeningPage.test.tsx` | 修改 | 新增轮询/404 用例 |
| `docs/CHANGELOG.md` | 修改 | 记录本次改动 |

> 路由无需改 `api/v1/router.py`：新端点用同一 `router` 对象的装饰器即自动注册到 `/api/v1/alphasift` 前缀下。

---

## Task 1: 后端抽出 `run_alphasift_screen` + LLM 临时环境注入

**Files:**
- Modify: `api/v1/endpoints/alphasift.py`（`alphasift_screen` 函数，约 187-242 行；顶部 import 区）
- Test: `tests/test_alphasift_api.py`

- [ ] **Step 1: 写失败测试（LLM 注入与还原）**

在 `tests/test_alphasift_api.py` 顶部 import 区补 `import os`（若已存在则跳过），并在 `AlphaSiftOpportunitiesApiTestCase` 类内新增：

```python
    def test_run_screen_injects_and_restores_llm_env(self) -> None:
        config = Config(
            alphasift_enabled=True,
            llm_channels=[{"name": "mimo", "api_keys": ["KEY123"], "base_url": "https://mimo.example/v1"}],
        )
        seen = {}

        def fake_screen(strategy, **kwargs):
            seen["key"] = os.environ.get("LLM_API_KEY")
            seen["base"] = os.environ.get("LLM_BASE_URL")
            return {"candidates": []}

        fake_module = _make_adapter_module(screen=MagicMock(side_effect=fake_screen))
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("LLM_BASE_URL", None)

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            alphasift_endpoint.run_alphasift_screen(
                config, market="cn", strategy="dual_low", max_results=5,
            )

        self.assertEqual(seen["key"], "KEY123")
        self.assertEqual(seen["base"], "https://mimo.example/v1")
        self.assertNotIn("LLM_API_KEY", os.environ)
        self.assertNotIn("LLM_BASE_URL", os.environ)

    def test_run_screen_skips_injection_without_channel_keys(self) -> None:
        config = Config(alphasift_enabled=True, llm_channels=[{"name": "mimo", "api_keys": []}])
        seen = {}

        def fake_screen(strategy, **kwargs):
            seen["key"] = os.environ.get("LLM_API_KEY")
            return {"candidates": []}

        fake_module = _make_adapter_module(screen=MagicMock(side_effect=fake_screen))
        os.environ.pop("LLM_API_KEY", None)

        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            alphasift_endpoint.run_alphasift_screen(
                config, market="cn", strategy="dual_low", max_results=5,
            )

        self.assertIsNone(seen["key"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_alphasift_api.py::AlphaSiftOpportunitiesApiTestCase::test_run_screen_injects_and_restores_llm_env -v`
Expected: FAIL（`module 'api.v1.endpoints.alphasift' has no attribute 'run_alphasift_screen'`）

- [ ] **Step 3: 实现 `_alphasift_llm_env` 与 `run_alphasift_screen`，并把 `alphasift_screen` 改成薄包装**

在 `api/v1/endpoints/alphasift.py` 顶部 import 区补：

```python
import contextlib
```

新增上下文管理器（放在 `_prepare_alphasift_runtime_env` 附近）：

```python
@contextlib.contextmanager
def _alphasift_llm_env(config: Config):
    """临时把 DSA 主 LLM 渠道的 key/base_url 注入 AlphaSift 读取的 LLM_API_KEY/LLM_BASE_URL，
    调用结束后还原。AlphaSift 0.2.0 的 _resolve_llm_api_key/base_url 把这两个变量当最高优先级覆盖。"""
    channel = next(
        (c for c in (config.llm_channels or []) if c.get("api_keys")),
        None,
    )
    if not channel:
        yield
        return

    api_key = str(channel["api_keys"][0])
    base_url = str(channel.get("base_url") or "")
    previous = {
        "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
        "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
    }
    os.environ["LLM_API_KEY"] = api_key
    if base_url:
        os.environ["LLM_BASE_URL"] = base_url
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
```

把现有 `alphasift_screen` 的函数体迁移到新函数 `run_alphasift_screen`（注意：原 `request.xxx` 改为参数 `market`/`strategy`/`max_results`；把 `_call_alphasift_screen(...)` 调用包进 `with _alphasift_llm_env(config):`）：

```python
def run_alphasift_screen(
    config: Config,
    *,
    market: str,
    strategy: str,
    max_results: int,
) -> Dict[str, Any]:
    _ensure_alphasift_enabled(config)
    _ensure_supported_market(market)
    _ensure_supported_strategy(strategy)

    adapter = _get_dsa_adapter()
    screen = _get_adapter_callable(adapter, "screen", "screen() 不可调用。")
    with _alphasift_llm_env(config):
        try:
            raw = _call_alphasift_screen(screen, strategy, market, max_results)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "alphasift_screen_rejected", "message": str(exc)},
            ) from exc
        except (TypeError, KeyError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "alphasift_invalid_input", "message": f"AlphaSift 参数非法：{exc}"},
            ) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=424,
                detail={"error": "alphasift_screen_failed", "message": f"AlphaSift 选股运行失败：{exc}"},
            ) from exc

    raw_data = _to_plain(raw)
    if not isinstance(raw_data, dict):
        raw_data = {"candidates": raw_data}
    raw_data = _remove_non_finite_json_values(raw_data)

    candidates = _normalize_candidates(raw_data)
    selected = candidates[:max_results]
    return {
        "enabled": True,
        "candidates": selected,
        "candidate_count": len(selected),
        "run_id": raw_data.get("run_id"),
        "strategy": raw_data.get("strategy") or strategy,
        "market": raw_data.get("market") or market,
        "snapshot_count": raw_data.get("snapshot_count"),
        "after_filter_count": raw_data.get("after_filter_count"),
        "llm_ranked": raw_data.get("llm_ranked"),
        "llm_market_view": raw_data.get("llm_market_view") or "",
        "llm_selection_logic": raw_data.get("llm_selection_logic") or "",
        "llm_portfolio_risk": raw_data.get("llm_portfolio_risk") or "",
        "llm_coverage": raw_data.get("llm_coverage"),
        "llm_parse_errors": raw_data.get("llm_parse_errors") or [],
        "warnings": raw_data.get("warnings") or [],
        "source_errors": raw_data.get("source_errors") or [],
    }
```

把原 `@router.post("/screen")` 端点改成薄包装：

```python
@router.post("/screen")
def alphasift_screen(
    request: AlphaSiftScreenRequest,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    return run_alphasift_screen(
        config,
        market=request.market,
        strategy=request.strategy,
        max_results=request.max_results,
    )
```

- [ ] **Step 4: 跑测试确认通过（含原有用例不回归）**

Run: `python -m pytest tests/test_alphasift_api.py -v`
Expected: PASS（新增 2 条 + 原有全部通过；注意 `test_screen_calls_dsa_adapter_and_normalizes_llm_fields` 仍应通过，证明响应体结构不变）

- [ ] **Step 5: 提交**

```bash
git add api/v1/endpoints/alphasift.py tests/test_alphasift_api.py
git commit -m "refactor(alphasift): 抽出 run_alphasift_screen 并注入 MiMo LLM 环境

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 后端内存 job store

**Files:**
- Create: `src/services/alphasift_screen_jobs.py`
- Test: `tests/test_alphasift_screen_jobs.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_alphasift_screen_jobs.py`：

```python
# -*- coding: utf-8 -*-
"""Tests for the in-memory AlphaSift screen job store."""

from __future__ import annotations

import time
import unittest

from src.services.alphasift_screen_jobs import AlphaSiftScreenJobStore


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class AlphaSiftScreenJobStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AlphaSiftScreenJobStore()

    def test_submit_runs_and_completes(self) -> None:
        def run_fn(*, market, strategy, max_results):
            return {"candidates": [{"code": market}], "max_results": max_results}

        job = self.store.submit("cn", "dual_low", 3, run_fn)
        self.assertEqual(job.status, "pending")

        _wait_until(lambda: self.store.get(job.job_id).status == "completed")
        done = self.store.get(job.job_id)
        self.assertEqual(done.result["candidates"][0]["code"], "cn")
        self.assertIsNone(done.error)

    def test_submit_reuses_active_job(self) -> None:
        started = []

        def run_fn(*, market, strategy, max_results):
            started.append(1)
            time.sleep(0.3)
            return {"candidates": []}

        first = self.store.submit("cn", "dual_low", 3, run_fn)
        second = self.store.submit("cn", "quality_value", 5, run_fn)
        self.assertEqual(first.job_id, second.job_id)

        _wait_until(lambda: self.store.get(first.job_id).status == "completed")
        self.assertEqual(len(started), 1)

    def test_failed_job_records_error(self) -> None:
        def run_fn(*, market, strategy, max_results):
            raise RuntimeError("boom")

        job = self.store.submit("cn", "dual_low", 3, run_fn)
        _wait_until(lambda: self.store.get(job.job_id).status == "failed")
        self.assertIn("boom", self.store.get(job.job_id).error)

    def test_get_unknown_returns_none(self) -> None:
        self.assertIsNone(self.store.get("nope"))

    def test_cleanup_drops_old_finished_jobs(self) -> None:
        def run_fn(*, market, strategy, max_results):
            return {"candidates": []}

        job = self.store.submit("cn", "dual_low", 3, run_fn)
        _wait_until(lambda: self.store.get(job.job_id).status == "completed")
        # 人为把完成时间提前到 TTL 之外
        self.store.get(job.job_id).finished_at = time.time() - (self.store.TTL_SECONDS + 10)

        # 再提交一次触发清理
        new_job = self.store.submit("cn", "dual_low", 3, run_fn)
        _wait_until(lambda: self.store.get(new_job.job_id).status == "completed")
        self.assertIsNone(self.store.get(job.job_id))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_alphasift_screen_jobs.py -v`
Expected: FAIL（`No module named 'src.services.alphasift_screen_jobs'`）

- [ ] **Step 3: 实现 job store**

新建 `src/services/alphasift_screen_jobs.py`：

```python
# -*- coding: utf-8 -*-
"""AlphaSift 选股异步 job 存储（内存、单 worker、幂等复用）。

选股是耗时 ~7 分钟的全市场扫描，必须异步化以绕开前置 CDN 的 100s 超时上限。
结果仅存内存、不持久化（探索性操作，刷新/重启即弃）。
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

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
            self._cleanup_locked()
            active = self._active_job_locked()
            if active is not None:
                return active  # 幂等复用进行中的任务，绝不排队
            job = ScreenJob(
                job_id=uuid.uuid4().hex[:12],
                market=market,
                strategy=strategy,
                max_results=max_results,
            )
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job, run_fn)
        return job

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
        except Exception as exc:  # noqa: BLE001 - 任何失败都落到 job.error
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict) and detail.get("message"):
                job.error = str(detail["message"])
            else:
                job.error = str(exc) or exc.__class__.__name__
            job.status = "failed"
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_alphasift_screen_jobs.py -v`
Expected: PASS（5 条全过）

- [ ] **Step 5: 提交**

```bash
git add src/services/alphasift_screen_jobs.py tests/test_alphasift_screen_jobs.py
git commit -m "feat(alphasift): 新增内存选股 job store（单worker/幂等复用/TTL）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 后端异步端点（提交 + 轮询）

**Files:**
- Modify: `api/v1/endpoints/alphasift.py`（新增两个端点 + import job store）
- Test: `tests/test_alphasift_api.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_alphasift_api.py` 的 `AlphaSiftOpportunitiesApiTestCase` 类内新增。注意：提交端点会异步执行，测试用轮询等待完成。

```python
    def _reset_job_store(self):
        from src.services.alphasift_screen_jobs import AlphaSiftScreenJobStore
        AlphaSiftScreenJobStore._instance = None

    def _wait_job(self, job_id, target=("completed", "failed"), timeout=5.0):
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            payload = alphasift_endpoint.alphasift_get_screen_job(job_id=job_id)
            if payload["status"] in target:
                return payload
            _t.sleep(0.01)
        raise AssertionError("job did not finish in time")

    def test_submit_job_then_poll_completed(self) -> None:
        self._reset_job_store()
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            screen=MagicMock(return_value={"candidates": [{"code": "600519", "name": "MT"}]}),
        )
        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            submitted = alphasift_endpoint.alphasift_submit_screen_job(
                alphasift_endpoint.AlphaSiftScreenRequest(market="cn", strategy="dual_low", max_results=3),
                config=config,
            )
            self.assertEqual(submitted["status"], "pending")
            self.assertIn("job_id", submitted)
            done = self._wait_job(submitted["job_id"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["candidate_count"], 1)
        self.assertEqual(done["candidates"][0]["code"], "600519")

    def test_submit_job_reuses_active(self) -> None:
        self._reset_job_store()
        config = self._config(enabled=True)
        import time as _t

        def slow_screen(strategy, **kwargs):
            _t.sleep(0.3)
            return {"candidates": []}

        fake_module = _make_adapter_module(screen=MagicMock(side_effect=slow_screen))
        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            first = alphasift_endpoint.alphasift_submit_screen_job(
                alphasift_endpoint.AlphaSiftScreenRequest(market="cn", strategy="dual_low", max_results=3),
                config=config,
            )
            second = alphasift_endpoint.alphasift_submit_screen_job(
                alphasift_endpoint.AlphaSiftScreenRequest(market="cn", strategy="quality_value", max_results=5),
                config=config,
            )
            self.assertEqual(first["job_id"], second["job_id"])
            self._wait_job(first["job_id"])

    def test_submit_job_rejects_when_disabled(self) -> None:
        self._reset_job_store()
        config = self._config(enabled=False)
        with self.assertRaises(HTTPException) as caught:
            alphasift_endpoint.alphasift_submit_screen_job(
                alphasift_endpoint.AlphaSiftScreenRequest(market="cn", strategy="dual_low", max_results=3),
                config=config,
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_get_unknown_job_returns_404(self) -> None:
        self._reset_job_store()
        with self.assertRaises(HTTPException) as caught:
            alphasift_endpoint.alphasift_get_screen_job(job_id="missing")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail["error"], "alphasift_screen_job_not_found")

    def test_failed_job_poll_reports_error(self) -> None:
        self._reset_job_store()
        config = self._config(enabled=True)
        fake_module = _make_adapter_module(
            screen=MagicMock(side_effect=ValueError("bad strategy")),
        )
        with patch("api.v1.endpoints.alphasift._import_alphasift", return_value=fake_module):
            submitted = alphasift_endpoint.alphasift_submit_screen_job(
                alphasift_endpoint.AlphaSiftScreenRequest(market="cn", strategy="dual_low", max_results=3),
                config=config,
            )
            done = self._wait_job(submitted["job_id"])
        self.assertEqual(done["status"], "failed")
        self.assertIn("bad strategy", done["error"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_alphasift_api.py::AlphaSiftOpportunitiesApiTestCase::test_submit_job_then_poll_completed -v`
Expected: FAIL（`has no attribute 'alphasift_submit_screen_job'`）

- [ ] **Step 3: 实现两个端点**

在 `api/v1/endpoints/alphasift.py` import 区补：

```python
from src.services.alphasift_screen_jobs import AlphaSiftScreenJobStore
```

在 `run_alphasift_screen` 之后新增两个端点：

```python
@router.post("/screen/jobs")
def alphasift_submit_screen_job(
    request: AlphaSiftScreenRequest,
    config: Config = Depends(get_config_dep),
) -> Dict[str, Any]:
    # 同步快速校验（秒级），失败直接返回错误码，不建 job
    _ensure_alphasift_enabled(config)
    _ensure_supported_market(request.market)
    _ensure_supported_strategy(request.strategy)

    store = AlphaSiftScreenJobStore.get_instance()

    def _run(*, market: str, strategy: str, max_results: int) -> Dict[str, Any]:
        return run_alphasift_screen(
            config, market=market, strategy=strategy, max_results=max_results
        )

    job = store.submit(request.market, request.strategy, request.max_results, _run)
    return {"job_id": job.job_id, "status": job.status}


@router.get("/screen/jobs/{job_id}")
def alphasift_get_screen_job(job_id: str) -> Dict[str, Any]:
    # 纯内存查询：不调用任何 AlphaSift 适配层
    store = AlphaSiftScreenJobStore.get_instance()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "alphasift_screen_job_not_found",
                "message": "选股任务不存在或已过期，请重新运行。",
            },
        )
    payload: Dict[str, Any] = {"job_id": job.job_id, "status": job.status}
    if job.status == "completed" and isinstance(job.result, dict):
        payload.update(job.result)
    elif job.status == "failed":
        payload["error"] = job.error or "选股失败"
    return payload
```

> 说明：测试里多个用例共享单例 job store 且 `max_workers=1`，故每个用例用 `_reset_job_store()` 重置单例，避免前一个用例的活跃 job 干扰"幂等复用"判断。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_alphasift_api.py -v`
Expected: PASS（新增 5 条 + 原有全部）

- [ ] **Step 5: py_compile 自检 + 提交**

Run: `python -m py_compile api/v1/endpoints/alphasift.py src/services/alphasift_screen_jobs.py`
Expected: 无输出（成功）

```bash
git add api/v1/endpoints/alphasift.py tests/test_alphasift_api.py
git commit -m "feat(alphasift): 新增异步选股端点 /screen/jobs（提交+轮询）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 前端 API 客户端 `submitScreenJob` / `getScreenJob`

**Files:**
- Modify: `apps/dsa-web/src/api/alphasift.ts`
- Test: `apps/dsa-web/src/api/__tests__/alphasift.test.ts`

> 全部前端命令在 `apps/dsa-web` 目录下执行。

- [ ] **Step 1: 写失败测试**

在 `apps/dsa-web/src/api/__tests__/alphasift.test.ts` 的 `describe('alphasiftApi', ...)` 内新增：

```typescript
  it('submits a screen job and returns camelCased job info', async () => {
    post.mockResolvedValueOnce({ data: { job_id: 'abc123', status: 'pending' } });

    const result = await alphasiftApi.submitScreenJob({ market: 'cn', strategy: 'dual_low', maxResults: 3 });

    expect(post).toHaveBeenCalledWith(
      '/api/v1/alphasift/screen/jobs',
      { market: 'cn', strategy: 'dual_low', max_results: 3 },
      expect.objectContaining({ timeout: expect.any(Number) }),
    );
    expect(result.jobId).toBe('abc123');
    expect(result.status).toBe('pending');
  });

  it('gets a screen job and camelCases the completed payload', async () => {
    get.mockResolvedValueOnce({
      data: {
        job_id: 'abc123',
        status: 'completed',
        candidate_count: 1,
        candidates: [{ code: '600519', name: 'MT', rank: 1, reason: '' }],
        llm_ranked: true,
      },
    });

    const result = await alphasiftApi.getScreenJob('abc123');

    expect(get).toHaveBeenCalledWith('/api/v1/alphasift/screen/jobs/abc123', expect.any(Object));
    expect(result.status).toBe('completed');
    expect(result.candidateCount).toBe(1);
    expect(result.candidates[0].code).toBe('600519');
    expect(result.llmRanked).toBe(true);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -- alphasift.test.ts --run`
Expected: FAIL（`alphasiftApi.submitScreenJob is not a function`）

- [ ] **Step 3: 实现 client 方法与类型**

在 `apps/dsa-web/src/api/alphasift.ts` 顶部常量区补一个轮询端点短超时常量：

```typescript
const ALPHASIFT_JOB_API_TIMEOUT_MS = 30000;
```

新增类型（放在 `AlphaSiftScreenResponse` 之后）：

```typescript
export type AlphaSiftScreenJobSubmit = {
  jobId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
};

export type AlphaSiftScreenJobResult = AlphaSiftScreenResponse & {
  jobId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  error?: string;
};
```

在 `alphasiftApi` 对象内（紧挨现有 `screen` 方法后）新增：

```typescript
  async submitScreenJob(payload: { market: string; strategy: string; maxResults: number }): Promise<AlphaSiftScreenJobSubmit> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/alphasift/screen/jobs', {
      market: payload.market,
      strategy: payload.strategy,
      max_results: payload.maxResults,
    }, { timeout: ALPHASIFT_JOB_API_TIMEOUT_MS });
    return toCamelCase<AlphaSiftScreenJobSubmit>(response.data);
  },

  async getScreenJob(jobId: string): Promise<AlphaSiftScreenJobResult> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/alphasift/screen/jobs/${jobId}`,
      { timeout: ALPHASIFT_JOB_API_TIMEOUT_MS },
    );
    return toCamelCase<AlphaSiftScreenJobResult>(response.data);
  },
```

- [ ] **Step 4: 跑测试确认通过**

Run: `npm run test -- alphasift.test.ts --run`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/api/alphasift.ts apps/dsa-web/src/api/__tests__/alphasift.test.ts
git commit -m "feat(web): alphasift API 新增 submitScreenJob/getScreenJob

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 前端选股页改为提交 + 轮询

**Files:**
- Modify: `apps/dsa-web/src/pages/StockScreeningPage.tsx`（`handleSubmit` 约 183-197 行；import 区；组件状态/ref）
- Test: `apps/dsa-web/src/pages/__tests__/StockScreeningPage.test.tsx`

- [ ] **Step 1: 写失败测试（轮询完成 + 404）**

在 `apps/dsa-web/src/pages/__tests__/StockScreeningPage.test.tsx` 新增用例。需先确认该文件顶部如何 mock `alphasiftApi`（沿用其既有 mock 风格补 `submitScreenJob`/`getScreenJob`）。新增：

```typescript
  it('submits a job and renders candidates after polling completes', async () => {
    vi.useFakeTimers();
    alphasiftApi.submitScreenJob = vi.fn().mockResolvedValue({ jobId: 'job1', status: 'pending' });
    alphasiftApi.getScreenJob = vi.fn()
      .mockResolvedValueOnce({ jobId: 'job1', status: 'running', candidates: [] })
      .mockResolvedValueOnce({
        jobId: 'job1', status: 'completed', candidateCount: 1,
        candidates: [{ code: '600519', name: '贵州茅台', rank: 1, reason: '' }],
        llmRanked: true,
      });

    renderScreeningPage(); // 沿用文件内既有渲染辅助；若无则用 render(<StockScreeningPage />)
    // 触发运行（沿用文件内点击"运行选股"按钮的既有写法）
    await clickRunButton();

    await vi.advanceTimersByTimeAsync(4000); // 第一次轮询 -> running
    await vi.advanceTimersByTimeAsync(4000); // 第二次轮询 -> completed

    expect(await screen.findByText('600519')).toBeInTheDocument();
    vi.useRealTimers();
  });

  it('shows a re-run hint when polling returns 404', async () => {
    vi.useFakeTimers();
    alphasiftApi.submitScreenJob = vi.fn().mockResolvedValue({ jobId: 'job1', status: 'pending' });
    const notFound = Object.assign(new Error('not found'), { response: { status: 404 } });
    alphasiftApi.getScreenJob = vi.fn().mockRejectedValue(notFound);

    renderScreeningPage();
    await clickRunButton();
    await vi.advanceTimersByTimeAsync(4000);

    expect(await screen.findByText(/结果未保留|重新运行/)).toBeInTheDocument();
    vi.useRealTimers();
  });
```

> 实施者注：`renderScreeningPage()`/`clickRunButton()` 是占位，需替换为该测试文件中**已有**的渲染与交互写法（读文件顶部与现有用例即可知）。断言文案需与 Step 3 实现一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `npm run test -- StockScreeningPage.test.tsx --run`
Expected: FAIL（轮询未实现，找不到候选/提示文案）

- [ ] **Step 3: 实现提交+轮询**

在 `StockScreeningPage.tsx` import 区补：

```typescript
import { getParsedApiError } from '../api/error';
```

并把 `useRef` 加入 react import（首行 `import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';`）。

在组件内常量区（`export default function ...` 内、状态声明附近）补：

```typescript
  const POLL_INTERVAL_MS = 4000;
  const MAX_POLL_MS = 15 * 60 * 1000;
  const MAX_TRANSIENT_ERRORS = 3;
  const pollTimerRef = useRef<number | null>(null);
  const pollStartRef = useRef<number>(0);
  const transientErrorsRef = useRef<number>(0);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current != null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);
```

把现有 `handleSubmit`（约 183-197 行）整体替换为：

```typescript
  const pollJob = useCallback(async (jobId: string) => {
    if (Date.now() - pollStartRef.current > MAX_POLL_MS) {
      setError('选股超时，请稍后重试');
      setLoading(false);
      return;
    }
    try {
      const job = await alphasiftApi.getScreenJob(jobId);
      transientErrorsRef.current = 0;
      if (job.status === 'completed') {
        setScreenMeta(job);
        setCandidates(job.candidates ?? []);
        setExpandedCode(job.candidates?.[0]?.code ?? null);
        setLoading(false);
        return;
      }
      if (job.status === 'failed') {
        setCandidates([]);
        setError(job.error || '选股失败');
        setLoading(false);
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void pollJob(jobId), POLL_INTERVAL_MS);
    } catch (err) {
      const parsed = getParsedApiError(err);
      if (parsed.status === 404) {
        setCandidates([]);
        setError('任务已结束或服务重启，结果未保留，请重新运行');
        setLoading(false);
        return;
      }
      transientErrorsRef.current += 1;
      if (transientErrorsRef.current >= MAX_TRANSIENT_ERRORS) {
        setError(err instanceof Error ? err.message : '选股失败');
        setLoading(false);
        return;
      }
      pollTimerRef.current = window.setTimeout(() => void pollJob(jobId), POLL_INTERVAL_MS);
    }
  }, []);

  const handleSubmit = async () => {
    stopPolling();
    setLoading(true);
    setError('');
    setScreenMeta(null);
    transientErrorsRef.current = 0;
    pollStartRef.current = Date.now();
    try {
      const submitted = await alphasiftApi.submitScreenJob({ market, strategy, maxResults });
      pollTimerRef.current = window.setTimeout(() => void pollJob(submitted.jobId), POLL_INTERVAL_MS);
    } catch (err) {
      setCandidates([]);
      setError(err instanceof Error ? err.message : '选股失败');
      setLoading(false);
    }
  };
```

在运行按钮附近（约 330 行 `loadingText="筛选中..."`）把加载文案改为体现长耗时，例如把按钮下方或 `loadingText` 调整为：

```tsx
            loadingText="选股中(约几分钟)..."
```

并在结果区上方（`loading` 为 true 时）补一行提示（放在候选列表渲染条件附近）：

```tsx
          {loading && (
            <p className="text-sm text-muted-foreground">
              选股需扫描全市场，预计需几分钟，请勿关闭页面。
            </p>
          )}
```

> 实施者注：上面 JSX 的插入位置以"加载态可见、不破坏现有布局"为准，具体挂载点参考文件现有结构；`text-muted-foreground` 等类名沿用页面既有风格（若不同则取页面同类提示的类名）。

- [ ] **Step 4: 跑测试 + lint 确认通过**

Run: `npm run test -- StockScreeningPage.test.tsx alphasift.test.ts --run`
Expected: PASS

Run: `npm run lint`
Expected: 无 error

- [ ] **Step 5: 提交**

```bash
git add apps/dsa-web/src/pages/StockScreeningPage.tsx apps/dsa-web/src/pages/__tests__/StockScreeningPage.test.tsx
git commit -m "feat(web): 选股页改为提交+轮询，绕开CDN超时

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: 文档与最终校验

**Files:**
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 更新 CHANGELOG**

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 区新增条目（沿用文件既有格式）：

```markdown
- AlphaSift 选股改为异步任务（提交→轮询），绕开前置 CDN 的 100s 超时；选股的 LLM 重排接入项目 MiMo 渠道（注入 LLM_API_KEY/LLM_BASE_URL）。
```

- [ ] **Step 2: 全量测试 + 构建校验**

Run（仓库根）：`python -m pytest tests/test_alphasift_api.py tests/test_alphasift_screen_jobs.py -v`
Expected: 全 PASS

Run（`apps/dsa-web`）：`npm run test -- alphasift.test.ts StockScreeningPage.test.tsx --run && npm run lint && npm run build`
Expected: 测试 PASS、lint 无 error、build 成功

- [ ] **Step 3: 提交**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: CHANGELOG 记录 AlphaSift 选股异步化+MiMo 接入

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 部署与验收（实现完成、本地全绿后执行；非编码任务）

> 详见设计文档 §5。要点：
> 1. 服务器备份；2. 重建前确认 `docker/docker-compose.yml` 的 `env_file: ../data/.env` 与 `ENV_FILE=/app/data/.env` 两处本地改动仍在；
> 3. `git pull` → `docker compose -f docker/docker-compose.yml up -d`（多阶段构建编前端）重建两容器；
> 4. 真机实跑一次选股：提交秒回、轮询推进、出候选、`llm_ranked=true`（验证 MiMo 接上）、`docker stats` 看 stock-server RSS 峰值 < 400MB；
> 5. 把"3 个上游文件分叉"登记进维护手册 CLAUDE.md 与自动记忆（与数据源铁律同列，提醒同步上游时保留）。

---

## 自检记录（plan self-review）

- **Spec 覆盖**：§3.1 异步端点→Task 3；§3.1 job store/并发复用/TTL→Task 2；§3.2 LLM 注入→Task 1；§3.3 前端轮询/404/15min→Task 5；§4 测试→分散各 Task；§5 部署→末节；§6 上游同步登记→Task 6 部署要点。无遗漏。
- **占位扫描**：前端测试中 `renderScreeningPage()`/`clickRunButton()` 与 JSX 插入点已显式标注为"沿用文件既有写法"，因依赖现有测试文件结构，实施时读文件即可确定，非逻辑占位。
- **类型一致**：`run_alphasift_screen(config, *, market, strategy, max_results)`、`ScreenJob`、`alphasift_submit_screen_job`/`alphasift_get_screen_job`、`submitScreenJob`/`getScreenJob`、`AlphaSiftScreenJobSubmit`/`AlphaSiftScreenJobResult` 跨任务命名一致。
