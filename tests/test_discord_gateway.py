from __future__ import annotations

import pytest

from deepbot.gateway.discord_bot import MessageEnvelope, MessageProcessor
from deepbot.memory.session_store import SessionStore


class DummyRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_reply(self, request):
        self.calls += 1
        return f"reply:{request.session_id}"


@pytest.mark.asyncio
async def test_ignores_bot_messages() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(store=store, runtime=runtime, fallback_message="fallback")
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
    processor = MessageProcessor(store=store, runtime=runtime, fallback_message="fallback")
    sent: list[str] = []

    message = MessageEnvelope(
        message_id="1",
        content="hello",
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
async def test_reset_command_clears_context() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=300)
    runtime = DummyRuntime()
    processor = MessageProcessor(store=store, runtime=runtime, fallback_message="fallback")

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
