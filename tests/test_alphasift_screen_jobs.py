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
        self.store.get(job.job_id).finished_at = time.time() - (self.store.TTL_SECONDS + 10)

        new_job = self.store.submit("cn", "dual_low", 3, run_fn)
        _wait_until(lambda: self.store.get(new_job.job_id).status == "completed")
        self.assertIsNone(self.store.get(job.job_id))

    def test_cleanup_caps_to_max_jobs(self) -> None:
        def run_fn(*, market, strategy, max_results):
            return {"candidates": []}

        created = []
        for _ in range(self.store.MAX_JOBS + 5):
            job = self.store.submit("cn", "dual_low", 3, run_fn)
            _wait_until(lambda jid=job.job_id: self.store.get(jid) is not None
                        and self.store.get(jid).status == "completed")
            created.append(job.job_id)

        # 总数不超过上限
        with self.store._lock:
            remaining = len(self.store._jobs)
        self.assertLessEqual(remaining, self.store.MAX_JOBS)
        # 最早创建的若干个应已被淘汰，最新的应保留
        self.assertIsNone(self.store.get(created[0]))
        self.assertIsNotNone(self.store.get(created[-1]))


if __name__ == "__main__":
    unittest.main()
