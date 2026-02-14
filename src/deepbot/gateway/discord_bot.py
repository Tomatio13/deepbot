from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from deepbot.agent.runtime import AgentRequest, AgentRuntime
from deepbot.memory.session_store import SessionStore

logger = logging.getLogger(__name__)


class SendableChannel(Protocol):
    async def send(self, content: str) -> Any: ...


@dataclass(frozen=True)
class MessageEnvelope:
    message_id: str
    content: str
    author_id: str
    author_is_bot: bool
    guild_id: str | None
    channel_id: str
    thread_id: str | None


@dataclass(frozen=True)
class AuthConfig:
    passphrase: str
    idle_timeout_seconds: int
    auth_window_seconds: int
    max_retries: int
    lock_seconds: int
    auth_command: str = "/auth"

    @property
    def enabled(self) -> bool:
        return bool(self.passphrase)


@dataclass
class _AuthSessionState:
    last_activity_at: float | None = None
    authenticated_until: float | None = None
    failed_attempts: int = 0
    locked_until: float | None = None


class MessageProcessor:
    _AGENT_MEMORY_PREFIX_RE = re.compile(
        r"^(?:<@!?\d+>\s*)*(?:[$/])agent-memory(?:\s+(?P<rest>.*))?$",
        re.DOTALL | re.IGNORECASE,
    )
    _PROCESSING_HINT_PATTERN = re.compile(
        r"(https?://|[$/][\w-]+|[?？]|調べ|検索|最新|ソース|source|link|url|web|mcp)",
        re.IGNORECASE,
    )
    _MEMORY_SEARCH_HINT_RE = re.compile(
        r"([?？]|思い出|検索|探し|探して|どこ|いつ|何|覚えてる|決めた)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        store: SessionStore,
        runtime: AgentRuntime,
        fallback_message: str,
        processing_message: str,
        auth_config: AuthConfig | None = None,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._fallback_message = fallback_message
        self._processing_message = processing_message.strip()
        self._auth_config = auth_config or AuthConfig(
            passphrase="",
            idle_timeout_seconds=0,
            auth_window_seconds=0,
            max_retries=0,
            lock_seconds=0,
        )
        self._time_fn = time_fn or time.time
        self._auth_states: dict[str, _AuthSessionState] = {}
        self._auth_lock = asyncio.Lock()

    @classmethod
    def _should_send_processing_message(cls, content: str) -> bool:
        text = content.strip()
        if not text:
            return False
        return bool(cls._PROCESSING_HINT_PATTERN.search(text))

    @classmethod
    def _extract_agent_memory_query(cls, content: str) -> str | None:
        match = cls._AGENT_MEMORY_PREFIX_RE.match(content.strip())
        if not match:
            return None
        return (match.group("rest") or "").strip()

    @classmethod
    def _is_memory_search_query(cls, query: str) -> bool:
        return bool(cls._MEMORY_SEARCH_HINT_RE.search(query))

    @staticmethod
    def _agent_memory_scripts_dir() -> Path:
        config_dir = os.environ.get("DEEPBOT_CONFIG_DIR", "/app/config").strip() or "/app/config"
        return Path(config_dir).expanduser() / "skills" / "agent-memory" / "scripts"

    def _extract_auth_attempt(self, content: str) -> str | None:
        command = self._auth_config.auth_command
        if not command:
            return None
        if content == command:
            return ""
        prefix = f"{command} "
        if content.startswith(prefix):
            return content[len(prefix):].strip()
        return None

    @staticmethod
    def _seconds_to_minutes_text(seconds: float) -> str:
        minutes = max(1, int((seconds + 59) // 60))
        return f"{minutes}分"

    async def _auth_response_for_attempt(self, session_id: str, attempt: str, now: float) -> str:
        config = self._auth_config
        if not config.enabled:
            return ""
        async with self._auth_lock:
            state = self._auth_states.setdefault(session_id, _AuthSessionState())
            self._refresh_auth_state_locked(state, now)

            if state.locked_until is not None and now < state.locked_until:
                remaining = state.locked_until - now
                return (
                    "認証の失敗回数が上限に達しました。"
                    f"{self._seconds_to_minutes_text(remaining)}後に再試行してください。"
                )

            # compare_digest on str supports ASCII only on some Python versions.
            if hmac.compare_digest(attempt.encode("utf-8"), config.passphrase.encode("utf-8")):
                state.failed_attempts = 0
                state.locked_until = None
                state.authenticated_until = now + config.auth_window_seconds
                state.last_activity_at = now
                return (
                    "認証に成功しました。"
                    f"{self._seconds_to_minutes_text(config.auth_window_seconds)}の間、会話を継続できます。"
                )

            state.failed_attempts += 1
            state.last_activity_at = now
            remaining_attempts = config.max_retries - state.failed_attempts
            if remaining_attempts <= 0:
                state.failed_attempts = 0
                state.authenticated_until = None
                state.locked_until = now + config.lock_seconds
                return (
                    "認証に失敗しました。"
                    f"{self._seconds_to_minutes_text(config.lock_seconds)}ロックします。"
                )
            return f"認証に失敗しました。残り{remaining_attempts}回です。"

    async def _clear_auth_state(self, session_id: str) -> None:
        async with self._auth_lock:
            self._auth_states.pop(session_id, None)

    def _refresh_auth_state_locked(self, state: _AuthSessionState, now: float) -> None:
        config = self._auth_config
        if state.locked_until is not None and now >= state.locked_until:
            state.locked_until = None
            state.failed_attempts = 0

        if state.authenticated_until is not None and now >= state.authenticated_until:
            state.authenticated_until = None

        if (
            state.last_activity_at is not None
            and config.idle_timeout_seconds > 0
            and (now - state.last_activity_at) > config.idle_timeout_seconds
        ):
            state.authenticated_until = None

    async def _is_authenticated(self, session_id: str, now: float) -> bool:
        config = self._auth_config
        if not config.enabled:
            return True
        async with self._auth_lock:
            state = self._auth_states.setdefault(session_id, _AuthSessionState())
            self._refresh_auth_state_locked(state, now)
            if state.locked_until is not None and now < state.locked_until:
                return False
            authenticated = state.authenticated_until is not None and now < state.authenticated_until
            return authenticated

    async def _mark_activity(self, session_id: str, now: float) -> None:
        if not self._auth_config.enabled:
            return
        async with self._auth_lock:
            state = self._auth_states.setdefault(session_id, _AuthSessionState())
            self._refresh_auth_state_locked(state, now)
            state.last_activity_at = now

    async def _build_auth_prompt(self, session_id: str, now: float) -> str:
        command = self._auth_config.auth_command
        async with self._auth_lock:
            state = self._auth_states.setdefault(session_id, _AuthSessionState())
            self._refresh_auth_state_locked(state, now)
            if state.locked_until is not None and now < state.locked_until:
                remaining = state.locked_until - now
                return (
                    "このセッションは一時ロック中です。"
                    f"{self._seconds_to_minutes_text(remaining)}後に再試行してください。"
                )
        return f"続行するには `{command} <合言葉>` を入力してください。"

    async def _run_agent_memory_script(self, *args: str) -> tuple[int, str, str]:
        scripts_dir = self._agent_memory_scripts_dir()
        script = scripts_dir / args[0]
        if not script.exists():
            return 1, "", f"script not found: {script}"

        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(script),
            *args[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout_raw, stderr_raw = await proc.communicate()
        stdout = stdout_raw.decode("utf-8", errors="replace").strip()
        stderr = stderr_raw.decode("utf-8", errors="replace").strip()
        return int(proc.returncode or 0), stdout, stderr

    async def _handle_agent_memory(self, query: str) -> str:
        if not query:
            return "使い方: `/agent-memory 記録したい内容` または `/agent-memory 検索したい内容`"

        if self._is_memory_search_query(query):
            code, stdout, stderr = await self._run_agent_memory_script("search.sh", query, "--all")
            if code != 0:
                detail = stderr or stdout or "unknown error"
                return f"記憶の検索に失敗しました。({detail})"
            if not stdout:
                return "該当する記憶が見つかりませんでした。"
            if len(stdout) > 1200:
                stdout = stdout[:1200] + "\n...(truncated)"
            return f"記憶の検索結果です:\n```text\n{stdout}\n```"

        code1, stdout1, stderr1 = await self._run_agent_memory_script("daily_log.sh", query)
        if code1 != 0:
            detail = stderr1 or stdout1 or "unknown error"
            return f"記録に失敗しました。({detail})"

        title = query[:40]
        code2, stdout2, stderr2 = await self._run_agent_memory_script(
            "long_term.sh",
            "add",
            "Project Context",
            title,
            query,
        )
        if code2 != 0:
            detail = stderr2 or stdout2 or "unknown error"
            return f"日次ログには記録しましたが、長期メモリ追加に失敗しました。({detail})"

        msg1 = stdout1.splitlines()[-1] if stdout1 else "daily log updated"
        msg2 = stdout2.splitlines()[-1] if stdout2 else "long-term memory updated"
        return f"記録しました。\n- {msg1}\n- {msg2}"

    @staticmethod
    def build_session_id(message: MessageEnvelope) -> str:
        if message.thread_id:
            return f"thread:{message.thread_id}"
        if message.guild_id:
            return f"guild:{message.guild_id}:channel:{message.channel_id}"
        return f"dm:{message.author_id}"

    async def handle_message(
        self,
        message: MessageEnvelope,
        *,
        send_reply: Callable[[str], Awaitable[Any]],
    ) -> None:
        if message.author_is_bot:
            return

        content = message.content.strip()
        if not content:
            return

        session_id = self.build_session_id(message)
        now = self._time_fn()

        if content == "/reset":
            await self._store.clear(session_id)
            await self._clear_auth_state(session_id)
            await send_reply("このチャンネルの会話コンテキストをリセットしました。")
            return

        auth_attempt = self._extract_auth_attempt(content)
        if auth_attempt is not None:
            auth_response = await self._auth_response_for_attempt(session_id, auth_attempt, now)
            if auth_response.startswith("認証に成功しました。"):
                await self._store.clear(session_id)
            await send_reply(auth_response)
            return

        if not await self._is_authenticated(session_id, now):
            await self._mark_activity(session_id, now)
            await send_reply(await self._build_auth_prompt(session_id, now))
            return

        agent_memory_query = self._extract_agent_memory_query(content)

        await self._store.append(
            session_id,
            role="user",
            content=content,
            author_id=message.author_id,
        )

        context = await self._store.get_context(session_id)

        if self._processing_message and self._should_send_processing_message(content):
            await send_reply(self._processing_message)

        try:
            reply = await self._runtime.generate_reply(
                AgentRequest(session_id=session_id, context=context)
            )
        except Exception:
            logger.exception("Agent execution failed. session_id=%s", session_id)
            if agent_memory_query is not None:
                reply = await self._handle_agent_memory(agent_memory_query)
                await self._store.append(
                    session_id,
                    role="assistant",
                    content=reply,
                    author_id="deepbot",
                )
                await self._mark_activity(session_id, self._time_fn())
                await send_reply(reply)
                return
            await self._mark_activity(session_id, self._time_fn())
            await send_reply(self._fallback_message)
            return

        await self._store.append(
            session_id,
            role="assistant",
            content=reply,
            author_id="deepbot",
        )
        await self._mark_activity(session_id, self._time_fn())
        await send_reply(reply)


def _to_envelope(message: Any) -> MessageEnvelope:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)

    thread_id = None
    if channel is not None and hasattr(channel, "id"):
        # Discord thread channel also has id; for MVP, use message.thread when available.
        thread = getattr(message, "thread", None)
        if thread is not None and hasattr(thread, "id"):
            thread_id = str(thread.id)

    author = message.author
    return MessageEnvelope(
        message_id=str(message.id),
        content=str(message.content or ""),
        author_id=str(author.id),
        author_is_bot=bool(getattr(author, "bot", False)),
        guild_id=str(guild.id) if guild is not None else None,
        channel_id=str(channel.id),
        thread_id=thread_id,
    )


class DeepbotClientFactory:
    @staticmethod
    def create(*, processor: MessageProcessor):
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True

        class DeepbotClient(discord.Client):
            async def on_ready(self) -> None:
                logger.info("Deepbot logged in as %s", self.user)

            async def on_message(self, message: Any) -> None:
                envelope = _to_envelope(message)
                await processor.handle_message(
                    envelope,
                    send_reply=lambda text: message.channel.send(text),
                )

        return DeepbotClient(intents=intents)
