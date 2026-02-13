from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SessionMessage:
    role: str
    content: str
    author_id: str
    timestamp: float


class SessionStore:
    def __init__(
        self,
        *,
        max_messages: int,
        ttl_seconds: int,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        if max_messages <= 0:
            raise ValueError("max_messages must be > 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        self._max_messages = max_messages
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn or time.time
        self._sessions: dict[str, deque[SessionMessage]] = {}
        self._last_updated: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def append(self, session_id: str, *, role: str, content: str, author_id: str) -> None:
        now = self._time_fn()
        async with self._lock:
            self._evict_expired_locked(now)
            queue = self._sessions.setdefault(session_id, deque())
            queue.append(SessionMessage(role=role, content=content, author_id=author_id, timestamp=now))
            while len(queue) > self._max_messages:
                queue.popleft()
            self._last_updated[session_id] = now

    async def get_context(self, session_id: str) -> list[dict[str, str]]:
        now = self._time_fn()
        async with self._lock:
            self._evict_expired_locked(now)
            queue = self._sessions.get(session_id)
            if not queue:
                return []
            return [
                {
                    "role": msg.role,
                    "content": msg.content,
                }
                for msg in queue
            ]

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)
            self._last_updated.pop(session_id, None)

    async def evict_expired(self) -> None:
        async with self._lock:
            self._evict_expired_locked(self._time_fn())

    def _evict_expired_locked(self, now: float) -> None:
        stale_ids = [
            session_id
            for session_id, last_updated in self._last_updated.items()
            if (now - last_updated) > self._ttl_seconds
        ]
        for session_id in stale_ids:
            self._sessions.pop(session_id, None)
            self._last_updated.pop(session_id, None)
