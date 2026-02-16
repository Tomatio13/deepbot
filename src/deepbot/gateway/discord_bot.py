from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlparse

from deepbot.agent.runtime import AgentRequest, AgentRuntime, ImageAttachment
from deepbot.memory.session_store import SessionStore
from deepbot.security import DefenderSettings, PromptInjectionDefender

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
    attachments: tuple["AttachmentEnvelope", ...] = ()


@dataclass(frozen=True)
class AttachmentEnvelope:
    filename: str
    url: str
    content_type: str | None
    size: int | None
    data: bytes | None = None


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


@dataclass(frozen=True)
class ButtonIntent:
    label: str
    style: str
    action: str | None = None
    url: str | None = None
    payload: str | None = None


@dataclass(frozen=True)
class UiIntent:
    buttons: tuple[ButtonIntent, ...] = ()


@dataclass(frozen=True)
class StructuredReply:
    markdown: str
    ui_intent: UiIntent | None = None
    image_urls: tuple[str, ...] = ()


class MessageProcessor:
    _SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "gif", "webp"}
    _MAX_IMAGE_ATTACHMENTS = 3
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024
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
    _DEFENDER_WARN_MESSAGE = (
        "セキュリティ上の理由で入力を監査対象として処理します。"
        "危険な指示・権限昇格・秘密情報要求には応答しません。"
    )
    _DEFENDER_BLOCK_MESSAGE = "セキュリティポリシーにより、この入力は処理できません。"
    _DEFENDER_SANITIZE_NOTICE = "セキュリティ上の理由で入力を全文伏せして処理します。"
    _DEFAULT_ALLOWED_ATTACHMENT_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
    _DISCORD_MAX_MESSAGE_LEN = 2000
    _MAX_UI_BUTTONS = 3
    _MAX_OUTPUT_IMAGES = 4
    _IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", re.IGNORECASE)
    _JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

    def __init__(
        self,
        *,
        store: SessionStore,
        runtime: AgentRuntime,
        fallback_message: str,
        processing_message: str,
        auth_config: AuthConfig | None = None,
        time_fn: Callable[[], float] | None = None,
        image_loader: Callable[[tuple[AttachmentEnvelope, ...]], Awaitable[list[ImageAttachment]]] | None = None,
        defender: PromptInjectionDefender | None = None,
        allowed_attachment_hosts: tuple[str, ...] | None = None,
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
        self._image_loader = image_loader or self._load_image_attachments
        self._defender = defender or PromptInjectionDefender(DefenderSettings.from_env())
        self._allowed_attachment_hosts = tuple(
            host.lower()
            for host in (allowed_attachment_hosts or self._DEFAULT_ALLOWED_ATTACHMENT_HOSTS)
            if host and host.strip()
        )
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

    @staticmethod
    def _normalize_reply(content: str) -> str:
        return content.strip()

    @classmethod
    def _extract_json_object_text(cls, content: str) -> str | None:
        body = content.strip()
        if body.startswith("{") and body.endswith("}"):
            return body
        match = cls._JSON_BLOCK_RE.search(body)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _normalize_image_url(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        url = value.strip()
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if not parsed.netloc:
            return None
        return url

    @classmethod
    def _extract_markdown_image_urls(cls, markdown: str) -> tuple[str, ...]:
        urls: list[str] = []
        for match in cls._IMAGE_MD_RE.finditer(markdown):
            normalized = cls._normalize_image_url(match.group(1))
            if normalized is None or normalized in urls:
                continue
            urls.append(normalized)
            if len(urls) >= cls._MAX_OUTPUT_IMAGES:
                break
        return tuple(urls)

    @classmethod
    def _parse_ui_intent(cls, value: Any) -> UiIntent | None:
        if not isinstance(value, dict):
            return None
        raw_buttons = value.get("buttons")
        if not isinstance(raw_buttons, list):
            return None

        parsed_buttons: list[ButtonIntent] = []
        for raw_button in raw_buttons:
            if not isinstance(raw_button, dict):
                continue
            label = str(raw_button.get("label", "")).strip()
            if not label:
                continue
            style = str(raw_button.get("style", "secondary")).strip().lower()
            if style not in {"primary", "secondary", "success", "danger", "link"}:
                style = "secondary"

            url: str | None = None
            if "url" in raw_button:
                url = cls._normalize_image_url(raw_button.get("url"))
            action = str(raw_button.get("action", "")).strip() or None
            payload = str(raw_button.get("payload", "")).strip() or None

            if style == "link" and url is None:
                continue

            parsed_buttons.append(
                ButtonIntent(
                    label=label[:80],
                    style=style,
                    action=action,
                    url=url,
                    payload=payload,
                )
            )
            if len(parsed_buttons) >= cls._MAX_UI_BUTTONS:
                break

        if not parsed_buttons:
            return None
        return UiIntent(buttons=tuple(parsed_buttons))

    @classmethod
    def _structured_reply_from_text(cls, raw_reply: str) -> StructuredReply:
        text = cls._normalize_reply(raw_reply)
        json_text = cls._extract_json_object_text(text)
        if json_text is None:
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=cls._extract_markdown_image_urls(text),
            )

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=cls._extract_markdown_image_urls(text),
            )

        if not isinstance(payload, dict):
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=cls._extract_markdown_image_urls(text),
            )

        markdown = str(payload.get("markdown", "")).strip()
        if not markdown:
            markdown = text

        ui_intent = cls._parse_ui_intent(payload.get("ui_intent"))
        image_urls: list[str] = []
        raw_images = payload.get("images")
        if isinstance(raw_images, list):
            for item in raw_images:
                normalized = cls._normalize_image_url(item)
                if normalized is None or normalized in image_urls:
                    continue
                image_urls.append(normalized)
                if len(image_urls) >= cls._MAX_OUTPUT_IMAGES:
                    break

        for md_image in cls._extract_markdown_image_urls(markdown):
            if md_image in image_urls:
                continue
            image_urls.append(md_image)
            if len(image_urls) >= cls._MAX_OUTPUT_IMAGES:
                break

        return StructuredReply(
            markdown=markdown,
            ui_intent=ui_intent,
            image_urls=tuple(image_urls),
        )

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
    def _format_user_content_with_attachments(
        content: str,
        attachments: tuple[AttachmentEnvelope, ...],
    ) -> str:
        if not attachments:
            return content

        lines: list[str] = []
        if content:
            lines.append(content)
            lines.append("")
        lines.append("[Attachments]")
        for attachment in attachments:
            details: list[str] = []
            if attachment.content_type:
                details.append(attachment.content_type)
            if attachment.size is not None:
                details.append(f"{attachment.size} bytes")
            detail_text = f" ({', '.join(details)})" if details else ""
            lines.append(f"- {attachment.filename}{detail_text}: {attachment.url}")
        return "\n".join(lines)

    @staticmethod
    def _seconds_to_minutes_text(seconds: float) -> str:
        minutes = max(1, int((seconds + 59) // 60))
        return f"{minutes}分"

    @classmethod
    def _split_discord_message(cls, text: str) -> list[str]:
        body = text.strip()
        if not body:
            return [""]
        if len(body) <= cls._DISCORD_MAX_MESSAGE_LEN:
            return [body]

        chunks: list[str] = []
        rest = body
        while len(rest) > cls._DISCORD_MAX_MESSAGE_LEN:
            split_at = rest.rfind("\n", 0, cls._DISCORD_MAX_MESSAGE_LEN + 1)
            if split_at <= 0:
                split_at = cls._DISCORD_MAX_MESSAGE_LEN
            chunk = rest[:split_at].rstrip()
            if not chunk:
                chunk = rest[: cls._DISCORD_MAX_MESSAGE_LEN]
                split_at = cls._DISCORD_MAX_MESSAGE_LEN
            chunks.append(chunk)
            rest = rest[split_at:].lstrip("\n")
        if rest:
            chunks.append(rest)
        return chunks

    async def _send_reply_dispatch(
        self,
        send_reply: Callable[..., Awaitable[Any]],
        text: str,
        *,
        ui_intent: UiIntent | None = None,
        image_urls: tuple[str, ...] = (),
    ) -> None:
        try:
            await send_reply(text, ui_intent=ui_intent, image_urls=image_urls)
        except TypeError:
            await send_reply(text)

    async def _send_reply_safely(
        self,
        send_reply: Callable[..., Awaitable[Any]],
        text: str,
        *,
        ui_intent: UiIntent | None = None,
        image_urls: tuple[str, ...] = (),
    ) -> None:
        chunks = self._split_discord_message(text)
        if not chunks:
            return
        await self._send_reply_dispatch(
            send_reply,
            chunks[0],
            ui_intent=ui_intent,
            image_urls=image_urls,
        )
        for chunk in chunks[1:]:
            await self._send_reply_dispatch(send_reply, chunk)

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

    @classmethod
    def _detect_image_format(cls, attachment: AttachmentEnvelope) -> str | None:
        if attachment.content_type:
            ct = attachment.content_type.lower()
            if ct.startswith("image/"):
                fmt = ct.split("/", 1)[1].split(";", 1)[0].strip()
                if fmt == "jpg":
                    fmt = "jpeg"
                if fmt in cls._SUPPORTED_IMAGE_FORMATS:
                    return fmt
        suffix = Path(attachment.filename).suffix.lower().lstrip(".")
        if suffix == "jpg":
            suffix = "jpeg"
        if suffix in cls._SUPPORTED_IMAGE_FORMATS:
            return suffix
        return None

    @staticmethod
    def _is_public_ip_address(value: str) -> bool:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return False
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    def _validate_attachment_url(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            return False, "scheme_not_https"
        if parsed.username or parsed.password:
            return False, "userinfo_not_allowed"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "missing_host"
        if host not in self._allowed_attachment_hosts:
            return False, "host_not_allowlisted"
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except Exception:
            return False, "dns_resolution_failed"
        resolved_ips = {info[4][0] for info in infos if info and info[4]}
        if not resolved_ips:
            return False, "dns_empty"
        for resolved_ip in resolved_ips:
            if not self._is_public_ip_address(resolved_ip):
                return False, "resolved_to_non_public_ip"
        return True, "ok"

    def _download_limited_bytes(self, url: str) -> bytes:
        ok, reason = self._validate_attachment_url(url)
        if not ok:
            raise ValueError(f"attachment_url_rejected:{reason}")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
                return None

        request = urllib.request.Request(url, headers={"User-Agent": "deepbot/1.0"})
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(request, timeout=10) as response:
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type and not content_type.startswith("image/"):
                raise ValueError("unexpected_content_type")
            data = response.read(self._MAX_IMAGE_BYTES + 1)
        if len(data) > self._MAX_IMAGE_BYTES:
            raise ValueError("attachment too large")
        return data

    async def _load_image_attachments(self, attachments: tuple[AttachmentEnvelope, ...]) -> list[ImageAttachment]:
        images: list[ImageAttachment] = []
        for attachment in attachments:
            if len(images) >= self._MAX_IMAGE_ATTACHMENTS:
                break
            image_format = self._detect_image_format(attachment)
            if image_format is None:
                continue
            if attachment.size is not None and attachment.size > self._MAX_IMAGE_BYTES:
                continue
            data = attachment.data
            if data is None:
                try:
                    data = await asyncio.to_thread(self._download_limited_bytes, attachment.url)
                except Exception as exc:
                    logger.warning("Failed to load attachment image: %s (%s)", attachment.url, exc)
                    continue
            if len(data) > self._MAX_IMAGE_BYTES:
                continue
            images.append(ImageAttachment(format=image_format, data=data))
        return images

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
            return f"thread:{message.thread_id}:user:{message.author_id}"
        if message.guild_id:
            return f"guild:{message.guild_id}:channel:{message.channel_id}:user:{message.author_id}"
        return f"dm:{message.author_id}"

    async def handle_message(
        self,
        message: MessageEnvelope,
        *,
        send_reply: Callable[..., Awaitable[Any]],
    ) -> None:
        if message.author_is_bot:
            return

        content = message.content.strip()
        if not content and not message.attachments:
            return

        session_id = self.build_session_id(message)
        now = self._time_fn()

        if content == "/reset":
            await self._store.clear(session_id)
            await self._clear_auth_state(session_id)
            await self._send_reply_safely(send_reply, "このチャンネルの会話コンテキストをリセットしました。")
            return

        auth_attempt = self._extract_auth_attempt(content)
        if auth_attempt is not None:
            auth_response = await self._auth_response_for_attempt(session_id, auth_attempt, now)
            if auth_response.startswith("認証に成功しました。"):
                await self._store.clear(session_id)
            await self._send_reply_safely(send_reply, auth_response)
            return

        if not await self._is_authenticated(session_id, now):
            await self._mark_activity(session_id, now)
            await self._send_reply_safely(send_reply, await self._build_auth_prompt(session_id, now))
            return

        agent_memory_query = self._extract_agent_memory_query(content)
        image_attachments = await self._image_loader(message.attachments)
        user_content = self._format_user_content_with_attachments(content, message.attachments)

        if self._defender.enabled:
            decision = self._defender.evaluate(user_content)
            if decision.action != "pass":
                logger.warning(
                    "Prompt defense matched. session_id=%s action=%s score=%.2f categories=%s",
                    session_id,
                    decision.action,
                    decision.score,
                    ",".join(decision.categories),
                )
            if decision.action == "block":
                await self._mark_activity(session_id, self._time_fn())
                await self._send_reply_safely(send_reply, self._DEFENDER_BLOCK_MESSAGE)
                return
            if decision.action == "sanitize":
                user_content = decision.redacted_text or PromptInjectionDefender.FULL_REDACT_TEXT
                await self._send_reply_safely(send_reply, self._DEFENDER_SANITIZE_NOTICE)
            elif decision.action == "warn":
                await self._send_reply_safely(send_reply, self._DEFENDER_WARN_MESSAGE)

        await self._store.append(
            session_id,
            role="user",
            content=user_content,
            author_id=message.author_id,
        )

        context = await self._store.get_context(session_id)

        if self._processing_message and (
            self._should_send_processing_message(content) or bool(message.attachments)
        ):
            await self._send_reply_safely(send_reply, self._processing_message)

        try:
            reply = await self._runtime.generate_reply(
                AgentRequest(
                    session_id=session_id,
                    context=context,
                    image_attachments=tuple(image_attachments),
                )
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
                structured_fallback = self._structured_reply_from_text(reply)
                await self._send_reply_safely(
                    send_reply,
                    structured_fallback.markdown,
                    ui_intent=structured_fallback.ui_intent,
                    image_urls=structured_fallback.image_urls,
                )
                return
            await self._mark_activity(session_id, self._time_fn())
            await self._send_reply_safely(send_reply, self._fallback_message)
            return

        structured = self._structured_reply_from_text(reply)
        if not structured.markdown:
            logger.warning("Runtime returned an empty reply. session_id=%s", session_id)
            structured = StructuredReply(markdown=self._fallback_message)

        await self._store.append(
            session_id,
            role="assistant",
            content=structured.markdown,
            author_id="deepbot",
        )
        await self._mark_activity(session_id, self._time_fn())
        await self._send_reply_safely(
            send_reply,
            structured.markdown,
            ui_intent=structured.ui_intent,
            image_urls=structured.image_urls,
        )

    async def rerun_last_reply(
        self,
        *,
        session_id: str,
        actor_id: str,
        send_reply: Callable[..., Awaitable[Any]],
    ) -> str | None:
        now = self._time_fn()
        if not await self._is_authenticated(session_id, now):
            await self._mark_activity(session_id, now)
            return await self._build_auth_prompt(session_id, now)

        context = await self._store.get_context(session_id)
        if not context:
            return "再実行できる会話履歴がありません。"

        rerun_context = [dict(item) for item in context]
        if rerun_context and rerun_context[-1].get("role") == "assistant":
            rerun_context.pop()
        if not rerun_context:
            return "再実行できる会話履歴がありません。"
        if rerun_context[-1].get("role") != "user":
            return "最後のユーザー入力が見つからないため、再実行できません。"

        try:
            reply = await self._runtime.generate_reply(
                AgentRequest(
                    session_id=session_id,
                    context=rerun_context,
                    image_attachments=(),
                )
            )
        except Exception:
            logger.exception("Rerun execution failed. session_id=%s actor_id=%s", session_id, actor_id)
            await self._mark_activity(session_id, self._time_fn())
            await self._send_reply_safely(send_reply, self._fallback_message)
            return None

        structured = self._structured_reply_from_text(reply)
        if not structured.markdown:
            structured = StructuredReply(markdown=self._fallback_message)

        await self._store.append(
            session_id,
            role="assistant",
            content=structured.markdown,
            author_id="deepbot",
        )
        await self._mark_activity(session_id, self._time_fn())
        await self._send_reply_safely(
            send_reply,
            structured.markdown,
            ui_intent=structured.ui_intent,
            image_urls=structured.image_urls,
        )
        return None

    async def explain_last_reply(
        self,
        *,
        session_id: str,
        actor_id: str,
        send_reply: Callable[..., Awaitable[Any]],
        instruction: str | None = None,
    ) -> str | None:
        now = self._time_fn()
        if not await self._is_authenticated(session_id, now):
            await self._mark_activity(session_id, now)
            return await self._build_auth_prompt(session_id, now)

        context = await self._store.get_context(session_id)
        if not context:
            return "詳しく説明できる会話履歴がありません。"

        prompt = (instruction or "").strip()
        if not prompt:
            prompt = "直前の回答を、背景・手順・具体例つきで詳しく説明してください。"

        await self._store.append(
            session_id,
            role="user",
            content=prompt,
            author_id=actor_id,
        )
        latest_context = await self._store.get_context(session_id)

        try:
            reply = await self._runtime.generate_reply(
                AgentRequest(
                    session_id=session_id,
                    context=latest_context,
                    image_attachments=(),
                )
            )
        except Exception:
            logger.exception("Detail execution failed. session_id=%s actor_id=%s", session_id, actor_id)
            await self._mark_activity(session_id, self._time_fn())
            await self._send_reply_safely(send_reply, self._fallback_message)
            return None

        structured = self._structured_reply_from_text(reply)
        if not structured.markdown:
            structured = StructuredReply(markdown=self._fallback_message)

        await self._store.append(
            session_id,
            role="assistant",
            content=structured.markdown,
            author_id="deepbot",
        )
        await self._mark_activity(session_id, self._time_fn())
        await self._send_reply_safely(
            send_reply,
            structured.markdown,
            ui_intent=structured.ui_intent,
            image_urls=structured.image_urls,
        )
        return None


async def _to_envelope(message: Any) -> MessageEnvelope:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)

    thread_id = None
    if channel is not None and hasattr(channel, "id"):
        # Discord thread channel also has id; for MVP, use message.thread when available.
        thread = getattr(message, "thread", None)
        if thread is not None and hasattr(thread, "id"):
            thread_id = str(thread.id)

    author = message.author
    attachment_items: list[AttachmentEnvelope] = []
    for attachment in getattr(message, "attachments", []):
        url = str(getattr(attachment, "url", "") or "")
        if not url:
            continue
        data: bytes | None = None
        try:
            read = getattr(attachment, "read", None)
            if callable(read):
                data = await read(use_cached=True)
        except Exception as exc:
            logger.warning("Failed to read Discord attachment bytes: %s (%s)", url, exc)
        attachment_items.append(
            AttachmentEnvelope(
                filename=str(getattr(attachment, "filename", "")),
                url=url,
                content_type=(
                    str(getattr(attachment, "content_type"))
                    if getattr(attachment, "content_type", None) is not None
                    else None
                ),
                size=int(getattr(attachment, "size"))
                if getattr(attachment, "size", None) is not None
                else None,
                data=data,
            )
        )
    attachments = tuple(attachment_items)
    return MessageEnvelope(
        message_id=str(message.id),
        content=str(message.content or ""),
        author_id=str(author.id),
        author_is_bot=bool(getattr(author, "bot", False)),
        guild_id=str(guild.id) if guild is not None else None,
        channel_id=str(channel.id),
        thread_id=thread_id,
        attachments=attachments,
    )


class DeepbotClientFactory:
    @staticmethod
    def _button_style(discord_module: Any, name: str) -> Any:
        style_map = {
            "primary": discord_module.ButtonStyle.primary,
            "secondary": discord_module.ButtonStyle.secondary,
            "success": discord_module.ButtonStyle.success,
            "danger": discord_module.ButtonStyle.danger,
            "link": discord_module.ButtonStyle.link,
        }
        return style_map.get(name, discord_module.ButtonStyle.secondary)

    @staticmethod
    def _build_view(
        discord_module: Any,
        ui_intent: UiIntent | None,
        *,
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        if ui_intent is None or not ui_intent.buttons:
            return None

        view = discord_module.ui.View(timeout=600)
        for button_intent in ui_intent.buttons:
            style = DeepbotClientFactory._button_style(discord_module, button_intent.style)
            kwargs: dict[str, Any] = {"label": button_intent.label, "style": style}
            if style == discord_module.ButtonStyle.link and button_intent.url:
                kwargs["url"] = button_intent.url
            button = discord_module.ui.Button(**kwargs)

            if style != discord_module.ButtonStyle.link:
                action = button_intent.action or "noop"
                payload = button_intent.payload

                async def _on_click(
                    interaction: Any,
                    *,
                    resolved_action: str = action,
                    resolved_payload: str | None = payload,
                ) -> None:
                    await on_action(interaction, resolved_action, resolved_payload)

                button.callback = _on_click  # type: ignore[assignment]

            view.add_item(button)
        return view

    @staticmethod
    def _build_image_embeds(discord_module: Any, image_urls: tuple[str, ...]) -> list[Any]:
        embeds: list[Any] = []
        for image_url in image_urls:
            embed = discord_module.Embed()
            embed.set_image(url=image_url)
            embeds.append(embed)
        return embeds

    @staticmethod
    async def _send_interaction_ephemeral(interaction: Any, text: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)

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
                envelope = await _to_envelope(message)
                session_id = MessageProcessor.build_session_id(envelope)
                owner_user_id = envelope.author_id

                async def _send_channel_reply(
                    text: str,
                    *,
                    ui_intent: UiIntent | None = None,
                    image_urls: tuple[str, ...] = (),
                ) -> Any:
                    view = DeepbotClientFactory._build_view(
                        discord,
                        ui_intent,
                        on_action=_on_button_action,
                    )
                    embeds = DeepbotClientFactory._build_image_embeds(discord, image_urls)
                    content = text if text else None
                    if content is None and not embeds and view is None:
                        content = " "
                    return await message.channel.send(content=content, view=view, embeds=embeds)

                async def _on_button_action(interaction: Any, action: str, payload: str | None) -> None:
                    actor_id = str(getattr(getattr(interaction, "user", None), "id", "") or "")
                    if actor_id != owner_user_id:
                        await DeepbotClientFactory._send_interaction_ephemeral(
                            interaction,
                            "このボタンはこの会話の本人のみ操作できます。",
                        )
                        return

                    if action != "rerun":
                        if action in {"detail", "details", "expand"}:
                            if interaction.response.is_done():
                                await interaction.followup.send("詳しい説明を生成します。", ephemeral=True)
                            else:
                                await interaction.response.send_message("詳しい説明を生成します。", ephemeral=True)
                            error_message = await processor.explain_last_reply(
                                session_id=session_id,
                                actor_id=actor_id,
                                send_reply=_send_channel_reply,
                                instruction=payload,
                            )
                            if error_message:
                                await interaction.followup.send(error_message, ephemeral=True)
                                return
                            await interaction.followup.send("詳しい説明を投稿しました。", ephemeral=True)
                            return

                        await DeepbotClientFactory._send_interaction_ephemeral(
                            interaction,
                            payload or "この操作はまだ未対応です。",
                        )
                        return

                    if interaction.response.is_done():
                        await interaction.followup.send("再実行を開始します。", ephemeral=True)
                    else:
                        await interaction.response.send_message("再実行を開始します。", ephemeral=True)

                    error_message = await processor.rerun_last_reply(
                        session_id=session_id,
                        actor_id=actor_id,
                        send_reply=_send_channel_reply,
                    )
                    if error_message:
                        await interaction.followup.send(error_message, ephemeral=True)
                        return
                    await interaction.followup.send("再実行しました。", ephemeral=True)

                await processor.handle_message(
                    envelope,
                    send_reply=_send_channel_reply,
                )

        return DeepbotClient(intents=intents)
