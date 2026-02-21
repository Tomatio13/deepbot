from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deepbot.gateway.discord_bot import MessageEnvelope, MessageProcessor
from deepbot.memory.session_store import SessionStore
from deepbot.scheduler.models import JobDefinition


class EchoRuntime:
    async def generate_reply(self, request):
        return f"reply:{request.session_id}"


class BlockingCronRuntime:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def generate_reply(self, request):
        if str(request.session_id).startswith("cron:"):
            await self.release.wait()
            return "cron-done"
        return "user-done"


@pytest.mark.asyncio
async def test_cron_register_command_creates_job_file(tmp_path: Path) -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = EchoRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message="processing",
        cron_jobs_dir=tmp_path / "jobs",
        cron_default_timezone="Asia/Tokyo",
    )

    sent: list[str] = []

    async def send_reply(text: str, **_: object):
        sent.append(text)

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content='/定期登録 プロンプト="今日の天気" 頻度="平日 7:00"',
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
            attachments=(),
        ),
        send_reply=send_reply,
    )

    files = sorted((tmp_path / "jobs").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "schedule: 平日 7:00" in text
    assert "# Prompt" in text
    assert any("定期ジョブを登録しました" in item for item in sent)


@pytest.mark.asyncio
async def test_cron_register_command_accepts_english_alias(tmp_path: Path) -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = EchoRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message="processing",
        cron_jobs_dir=tmp_path / "jobs",
        cron_default_timezone="Asia/Tokyo",
    )

    sent: list[str] = []

    async def send_reply(text: str, **_: object):
        sent.append(text)

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content='/schedule prompt="today weather" schedule="毎時"',
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
            attachments=(),
        ),
        send_reply=send_reply,
    )

    files = sorted((tmp_path / "jobs").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "schedule: 毎時" in text
    assert any("定期ジョブを登録しました" in item for item in sent)


@pytest.mark.asyncio
async def test_handle_message_sends_busy_notice_while_scheduled_job_running() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = BlockingCronRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message="processing",
        cron_busy_message="ジョブ実行中です",
    )

    job = JobDefinition(
        path=Path("/tmp/dummy.md"),
        name="job-1",
        description="d",
        schedule="毎時",
        timezone="Asia/Tokyo",
        delivery="none",
        prompt="cron prompt",
        next_run_at=datetime.now(timezone.utc),
    )

    cron_task = asyncio.create_task(processor.run_scheduled_job(job))
    await asyncio.sleep(0.01)

    sent: list[str] = []

    async def send_reply(text: str, **_: object):
        sent.append(text)

    user_task = asyncio.create_task(
        processor.handle_message(
            MessageEnvelope(
                message_id="m1",
                content="こんにちは",
                author_id="u1",
                author_is_bot=False,
                guild_id="g1",
                channel_id="c1",
                thread_id=None,
                attachments=(),
            ),
            send_reply=send_reply,
        )
    )

    await asyncio.sleep(0.01)
    runtime.release.set()
    await cron_task
    await user_task

    assert sent[0] == "ジョブ実行中です"
    assert sent[-1] == "user-done"


@pytest.mark.asyncio
async def test_cron_delete_command_removes_job_file(tmp_path: Path) -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = EchoRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message="processing",
        cron_jobs_dir=tmp_path / "jobs",
        cron_default_timezone="Asia/Tokyo",
    )

    sent: list[str] = []

    async def send_reply(text: str, **_: object):
        sent.append(text)

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content='/定期登録 プロンプト="今日の天気" 頻度="毎時"',
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
            attachments=(),
        ),
        send_reply=send_reply,
    )
    files = sorted((tmp_path / "jobs").glob("*.md"))
    assert len(files) == 1
    job_id = files[0].stem

    await processor.handle_message(
        MessageEnvelope(
            message_id="2",
            content=f"/schedule-delete {job_id}",
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
            attachments=(),
        ),
        send_reply=send_reply,
    )

    remaining = sorted((tmp_path / "jobs").glob("*.md"))
    assert remaining == []
    assert any(f"ジョブを削除しました: `{job_id}`" in item for item in sent)
