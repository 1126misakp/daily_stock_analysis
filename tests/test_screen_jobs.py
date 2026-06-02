# -*- coding: utf-8 -*-
from src.services.screen_jobs import ScreenJobStore


def test_submit_and_get_completes():
    store = ScreenJobStore()

    def run_fn(strategy, preference, max_results):
        return {"candidates": [], "strategy": strategy, "preference": preference}

    job = store.submit("ma_golden_cross", "科技", 20, run_fn)
    assert job.status == "pending"
    import time
    for _ in range(50):
        cur = store.get(job.job_id)
        if cur.status in ("completed", "failed"):
            break
        time.sleep(0.05)
    done = store.get(job.job_id)
    assert done.status == "completed"
    assert done.result["preference"] == "科技"


def test_idempotent_reuse_active_job():
    store = ScreenJobStore()
    import threading
    gate = threading.Event()

    def run_fn(strategy, preference, max_results):
        gate.wait(2)
        return {"candidates": []}

    j1 = store.submit("a", None, 5, run_fn)
    j2 = store.submit("b", "x", 5, run_fn)   # 进行中 → 复用 j1
    assert j2.job_id == j1.job_id
    gate.set()
