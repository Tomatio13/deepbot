from __future__ import annotations

import logging
from dataclasses import dataclass
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


class MessageProcessor:
    def __init__(
        self,
        *,
        store: SessionStore,
        runtime: AgentRuntime,
        fallback_message: str,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._fallback_message = fallback_message

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

        if content == "/reset":
            await self._store.clear(session_id)
            await send_reply("このチャンネルの会話コンテキストをリセットしました。")
            return

        await self._store.append(
            session_id,
            role="user",
            content=content,
            author_id=message.author_id,
        )
        context = await self._store.get_context(session_id)

        try:
            reply = await self._runtime.generate_reply(
                AgentRequest(session_id=session_id, context=context)
            )
        except Exception:
            logger.exception("Agent execution failed. session_id=%s", session_id)
            await send_reply(self._fallback_message)
            return

        await self._store.append(
            session_id,
            role="assistant",
            content=reply,
            author_id="deepbot",
        )
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
