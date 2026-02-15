from __future__ import annotations

import pytest

from deepbot.memory.session_store import SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_session_store_keeps_recent_messages_only() -> None:
    clock = FakeClock()
    store = SessionStore(max_messages=2, ttl_seconds=300, time_fn=clock)

    await store.append("s1", role="user", content="a", author_id="u1")
    await store.append("s1", role="assistant", content="b", author_id="bot")
    await store.append("s1", role="user", content="c", author_id="u1")

    ctx = await store.get_context("s1")
    assert [m["content"] for m in ctx] == ["b", "c"]


@pytest.mark.asyncio
async def test_session_store_evicts_by_ttl() -> None:
    clock = FakeClock()
    store = SessionStore(max_messages=10, ttl_seconds=60, time_fn=clock)

    await store.append("s1", role="user", content="hello", author_id="u1")
    clock.now = 61.0

    ctx = await store.get_context("s1")
    assert ctx == []


@pytest.mark.asyncio
async def test_session_store_clear() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=60)
    await store.append("s1", role="user", content="hello", author_id="u1")

    await store.clear("s1")
    assert await store.get_context("s1") == []


@pytest.mark.asyncio
async def test_session_store_ignores_empty_content() -> None:
    store = SessionStore(max_messages=10, ttl_seconds=60)
    await store.append("s1", role="user", content="   ", author_id="u1")

    assert await store.get_context("s1") == []
