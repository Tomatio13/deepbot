from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from deepbot.scheduler.loader import (
    compute_next_run_at,
    parse_job_file,
    save_job,
)
from deepbot.scheduler.models import JobDefinition


def test_parse_job_file_with_sections(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config" / "skills").mkdir(parents=True)

    job_file = tmp_path / "jobs" / "morning-weather.md"
    job_file.parent.mkdir(parents=True)
    job_file.write_text(
        """---
name: morning-weather
description: 朝の天気通知
schedule: 平日 7:00
timezone: Asia/Tokyo
enabled: true
delivery: announce
channel: auto
mode: isolated
---

# Prompt
今日の天気を要約して

# Steps
- 最高/最低気温を含める
- 降水確率を含める

# Output
- 箇条書き3項目
""",
        encoding="utf-8",
    )

    job = parse_job_file(job_file, default_timezone="Asia/Tokyo")
    assert job.name == "morning-weather"
    assert job.prompt == "今日の天気を要約して"
    assert job.steps == ("最高/最低気温を含める", "降水確率を含める")
    assert job.output_constraints == ("箇条書き3項目",)
    assert job.invalid_reason is None


def test_compute_next_run_at_weekday() -> None:
    now = datetime(2026, 2, 21, 22, 0, 0, tzinfo=timezone.utc)  # Sat
    next_run = compute_next_run_at(
        schedule="平日 7:00",
        timezone_name="Asia/Tokyo",
        now_utc=now,
    )
    # Monday 2026-02-23 07:00 JST => 2026-02-22 22:00 UTC
    assert next_run == datetime(2026, 2, 22, 22, 0, 0, tzinfo=timezone.utc)


def test_save_job_writes_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "jobs" / "job-20260221-120000.md"
    job = JobDefinition(
        path=path,
        name="job-20260221-120000",
        description="desc",
        schedule="毎時",
        timezone="Asia/Tokyo",
        prompt="hello",
        next_run_at=datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    save_job(job)
    written = path.read_text(encoding="utf-8")
    assert "name: job-20260221-120000" in written
    assert "# Prompt" in written
    assert "hello" in written
