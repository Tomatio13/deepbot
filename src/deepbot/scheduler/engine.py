from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from deepbot.scheduler.loader import (
    compute_next_run_at,
    compute_retry_next_run,
    find_job,
    load_jobs,
    save_job,
)
from deepbot.scheduler.models import JobDefinition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerSettings:
    enabled: bool
    jobs_dir: Path
    default_timezone: str
    poll_seconds: int


class SchedulerEngine:
    def __init__(
        self,
        *,
        settings: SchedulerSettings,
        run_job: Callable[[JobDefinition], Awaitable[str]],
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._run_job = run_job
        self._time_fn = time_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._loop_lock = asyncio.Lock()

    def start(self) -> None:
        if not self._settings.enabled:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="deepbot-scheduler")
        logger.info("Scheduler started. jobs_dir=%s", self._settings.jobs_dir)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_job_now(self, name: str) -> tuple[bool, str]:
        async with self._loop_lock:
            jobs, errors = load_jobs(self._settings.jobs_dir, default_timezone=self._settings.default_timezone)
            for error in errors:
                logger.warning("Scheduler load error: %s", error)
            job = find_job(jobs, name)
            if job is None:
                return False, f"ジョブが見つかりません: {name}"
            if job.invalid_reason:
                return False, f"ジョブ定義が不正です: {job.invalid_reason}"
            await self._execute_job(job)
            return True, f"ジョブを実行しました: {name}"

    async def _run_loop(self) -> None:
        poll_seconds = max(1, self._settings.poll_seconds)
        while not self._stop_event.is_set():
            try:
                async with self._loop_lock:
                    await self._run_due_jobs_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduler loop error: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    async def _run_due_jobs_once(self) -> None:
        jobs, errors = load_jobs(self._settings.jobs_dir, default_timezone=self._settings.default_timezone)
        for error in errors:
            logger.warning("Scheduler load error: %s", error)

        now = self._now_utc()
        due_jobs = [
            job
            for job in jobs
            if job.enabled and job.invalid_reason is None and job.next_run_at is not None and job.next_run_at <= now
        ]
        due_jobs.sort(key=lambda item: (item.next_run_at or now, item.name))
        for job in due_jobs:
            await self._execute_job(job)

    async def _execute_job(self, job: JobDefinition) -> None:
        started_at = self._now_utc()
        succeeded = False
        error_message = ""
        try:
            await self._run_job(job)
            succeeded = True
        except Exception as exc:
            error_message = str(exc)
            logger.exception("Scheduled job failed. name=%s error=%s", job.name, exc)

        job.last_run_at = started_at

        if succeeded:
            job.retry_count = 0
            job.next_run_at = compute_next_run_at(
                schedule=job.schedule,
                timezone_name=job.timezone,
                now_utc=started_at,
            )
            save_job(job)
            return

        if job.retry_backoff == "exponential" and job.retry_count < job.max_retries:
            job.retry_count += 1
            job.next_run_at = compute_retry_next_run(retry_count=job.retry_count, now_utc=started_at)
            save_job(job)
            logger.warning(
                "Scheduled job retry queued. name=%s retry_count=%d next_run_at=%s",
                job.name,
                job.retry_count,
                job.next_run_at,
            )
            return

        job.retry_count = 0
        job.next_run_at = compute_next_run_at(
            schedule=job.schedule,
            timezone_name=job.timezone,
            now_utc=started_at,
        )
        save_job(job)
        logger.warning("Scheduled job failed without retry. name=%s error=%s", job.name, error_message)
