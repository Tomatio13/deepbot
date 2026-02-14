from __future__ import annotations

import types

import pytest

from deepbot.gateway.discord_bot import AuthConfig, MessageEnvelope, MessageProcessor
from deepbot.memory.session_store import SessionStore


class DummyRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_reply(self, request):
        self.calls += 1
        return f"reply:{request.session_id}"


class FailingRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_reply(self, request):
        self.calls += 1
        raise RuntimeError("boom")


PROCESSING = "お調べしますね。少しお待ちください。"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_ignores_bot_messages() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
    )
    sent: list[str] = []

    message = MessageEnvelope(
        message_id="1",
        content="hello",
        author_id="bot",
        author_is_bot=True,
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
    )

    async def send_reply(text: str):
        sent.append(text)

    await processor.handle_message(message, send_reply=send_reply)

    assert runtime.calls == 0
    assert sent == []


@pytest.mark.asyncio
async def test_auto_reply_and_context_saved() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
    )
    sent: list[str] = []

    message = MessageEnvelope(
        message_id="1",
        content="こんにちは",
        author_id="u1",
        author_is_bot=False,
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
    )

    async def send_reply(text: str):
        sent.append(text)

    await processor.handle_message(message, send_reply=send_reply)

    assert runtime.calls == 1
    assert sent == ["reply:guild:g1:channel:c1"]
    ctx = await store.get_context("guild:g1:channel:c1")
    assert len(ctx) == 2


@pytest.mark.asyncio
async def test_processing_message_sent_for_search_like_input() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
    )
    sent: list[str] = []

    message = MessageEnvelope(
        message_id="1",
        content="最新ニュースを調べて",
        author_id="u1",
        author_is_bot=False,
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
    )

    async def send_reply(text: str):
        sent.append(text)

    await processor.handle_message(message, send_reply=send_reply)

    assert runtime.calls == 1
    assert sent == [PROCESSING, "reply:guild:g1:channel:c1"]


@pytest.mark.asyncio
async def test_reset_command_clears_context() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
    )

    async def send_reply(_: str):
        return None

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content="hello",
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
        ),
        send_reply=send_reply,
    )

    await processor.handle_message(
        MessageEnvelope(
            message_id="2",
            content="/reset",
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
        ),
        send_reply=send_reply,
    )

    assert await store.get_context("guild:g1:channel:c1") == []


@pytest.mark.asyncio
async def test_agent_memory_command_falls_back_when_runtime_fails() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = FailingRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
    )
    sent: list[str] = []

    async def fake_handle(_: str) -> str:
        return "memory-ok"

    processor._handle_agent_memory = types.MethodType(  # type: ignore[method-assign]
        lambda self, query: fake_handle(query),
        processor,
    )

    async def send_reply(text: str):
        sent.append(text)

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content="/agent-memory テスト記録",
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
        ),
        send_reply=send_reply,
    )

    assert runtime.calls == 1
    assert sent == [PROCESSING, "memory-ok"]


@pytest.mark.asyncio
async def test_auth_gate_requires_reauth_after_idle_timeout() -> None:
    clock = FakeClock()
    store = SessionStore(max_messages=10, ttl_seconds=300, time_fn=clock)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
        auth_config=AuthConfig(
            passphrase="secret",
            idle_timeout_seconds=60,
            auth_window_seconds=600,
            max_retries=3,
            lock_seconds=1800,
        ),
        time_fn=clock,
    )
    sent: list[str] = []

    async def send_reply(text: str):
        sent.append(text)

    session_message = lambda mid, content: MessageEnvelope(
        message_id=mid,
        content=content,
        author_id="u1",
        author_is_bot=False,
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
    )

    await processor.handle_message(session_message("1", "こんにちは"), send_reply=send_reply)
    assert runtime.calls == 0
    assert sent[-1].startswith("続行するには")

    await processor.handle_message(session_message("2", "/auth secret"), send_reply=send_reply)
    assert runtime.calls == 0
    assert "認証に成功しました" in sent[-1]

    await processor.handle_message(session_message("3", "こんにちは"), send_reply=send_reply)
    assert runtime.calls == 1
    assert sent[-1] == "reply:guild:g1:channel:c1"

    clock.now = 61.0
    await processor.handle_message(session_message("4", "続けて"), send_reply=send_reply)
    assert runtime.calls == 1
    assert sent[-1].startswith("続行するには")


@pytest.mark.asyncio
async def test_auth_gate_locks_after_max_failures() -> None:
    clock = FakeClock()
    store = SessionStore(max_messages=10, ttl_seconds=300, time_fn=clock)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
        auth_config=AuthConfig(
            passphrase="secret",
            idle_timeout_seconds=60,
            auth_window_seconds=600,
            max_retries=3,
            lock_seconds=1800,
        ),
        time_fn=clock,
    )
    sent: list[str] = []

    async def send_reply(text: str):
        sent.append(text)

    session_message = lambda mid, content: MessageEnvelope(
        message_id=mid,
        content=content,
        author_id="u1",
        author_is_bot=False,
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
    )

    await processor.handle_message(session_message("1", "/auth bad1"), send_reply=send_reply)
    await processor.handle_message(session_message("2", "/auth bad2"), send_reply=send_reply)
    await processor.handle_message(session_message("3", "/auth bad3"), send_reply=send_reply)
    assert "ロックします" in sent[-1]

    await processor.handle_message(session_message("4", "/auth secret"), send_reply=send_reply)
    assert "再試行してください" in sent[-1]
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_auth_gate_accepts_non_ascii_passphrase() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(
        store=store,
        runtime=runtime,
        fallback_message="fallback",
        processing_message=PROCESSING,
        auth_config=AuthConfig(
            passphrase="やまかわ",
            idle_timeout_seconds=60,
            auth_window_seconds=600,
            max_retries=3,
            lock_seconds=1800,
        ),
    )
    sent: list[str] = []

    async def send_reply(text: str):
        sent.append(text)

    await processor.handle_message(
        MessageEnvelope(
            message_id="1",
            content="/auth やまかわ",
            author_id="u1",
            author_is_bot=False,
            guild_id="g1",
            channel_id="c1",
            thread_id=None,
        ),
        send_reply=send_reply,
    )

    assert "認証に成功しました" in sent[-1]
