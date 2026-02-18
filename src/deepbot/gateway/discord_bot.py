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
from dataclasses import dataclass, replace
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
    a2ui_components: tuple[dict[str, Any], ...] = ()
    surface_directives: tuple["SurfaceDirective", ...] = ()


@dataclass(frozen=True)
class SurfaceDirective:
    type: str
    surface_id: str


@dataclass
class _SurfaceState:
    rendered: StructuredReply
    template_components: tuple[dict[str, Any], ...]
    data_model: dict[str, Any]


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
    _MAX_A2UI_ENVELOPES = 8
    _DATA_BIND_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
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
        self._surface_states: dict[tuple[str, str], _SurfaceState] = {}
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

            # If a valid URL is provided without explicit action, treat it as a link button.
            if url is not None and action is None:
                style = "link"

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
    def _extract_a2ui_messages(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = payload.get("a2ui")
        if not isinstance(candidates, list):
            candidates = payload.get("messages")
        if not isinstance(candidates, list):
            return []
        messages: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            msg_type = str(item.get("type", "")).strip()
            if not msg_type:
                continue
            messages.append(item)
            if len(messages) >= cls._MAX_A2UI_ENVELOPES:
                break
        return messages

    @classmethod
    def _coerce_components(cls, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    @classmethod
    def _extract_text_from_component(cls, component: dict[str, Any]) -> str | None:
        for key in ("markdown", "text", "content", "label", "description"):
            raw = component.get(key)
            if isinstance(raw, str):
                text = raw.strip()
                if text:
                    return text
        return None

    @classmethod
    def _render_from_components(
        cls,
        components: list[dict[str, Any]],
        fallback_markdown: str,
    ) -> StructuredReply:
        markdown_parts: list[str] = []
        parsed_buttons: list[ButtonIntent] = []
        image_urls: list[str] = []
        queue = list(components)

        while queue:
            component = queue.pop(0)
            comp_type = str(component.get("type", "")).strip().lower()
            if comp_type not in {"button", "action", "image"}:
                text = cls._extract_text_from_component(component)
                if text:
                    markdown_parts.append(text)
            if comp_type in {"button", "action"} and len(parsed_buttons) < cls._MAX_UI_BUTTONS:
                label = str(component.get("label", "")).strip()
                if label:
                    style = str(component.get("style", "secondary")).strip().lower()
                    if style not in {"primary", "secondary", "success", "danger", "link"}:
                        style = "secondary"
                    action = str(component.get("action", "")).strip() or None
                    payload = str(component.get("payload", "")).strip() or None
                    url = cls._normalize_image_url(component.get("url"))
                    if url is not None and action is None:
                        style = "link"
                    if style != "link" or url is not None:
                        parsed_buttons.append(
                            ButtonIntent(
                                label=label[:80],
                                style=style,
                                action=action,
                                url=url,
                                payload=payload,
                            )
                        )

            if comp_type == "image":
                image_url = cls._normalize_image_url(component.get("url"))
                if image_url and image_url not in image_urls:
                    image_urls.append(image_url)
                    if len(image_urls) >= cls._MAX_OUTPUT_IMAGES:
                        image_urls = image_urls[: cls._MAX_OUTPUT_IMAGES]

            for child_key in ("components", "children", "items"):
                children = cls._coerce_components(component.get(child_key))
                if not children:
                    continue
                queue.extend(children)

        markdown = "\n\n".join(part for part in markdown_parts if part).strip() or fallback_markdown
        ui_intent = UiIntent(buttons=tuple(parsed_buttons)) if parsed_buttons else None
        return StructuredReply(
            markdown=markdown,
            ui_intent=ui_intent,
            image_urls=tuple(image_urls),
            a2ui_components=tuple(components),
        )

    @classmethod
    def _surface_id_from_message(cls, message: dict[str, Any]) -> str | None:
        for key in ("surfaceId", "surface_id", "id"):
            value = message.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    return normalized
        return None

    @staticmethod
    def _surface_state_key(session_id: str, surface_id: str) -> tuple[str, str]:
        return (session_id, surface_id)

    @classmethod
    def _lookup_data_value(cls, data_model: dict[str, Any], path: str) -> Any:
        current: Any = data_model
        for key in path.split("."):
            if not isinstance(current, dict):
                return None
            if key not in current:
                return None
            current = current[key]
        return current

    @classmethod
    def _bind_text_with_data_model(cls, text: str, data_model: dict[str, Any]) -> str:
        def _repl(match: re.Match[str]) -> str:
            value = cls._lookup_data_value(data_model, match.group(1))
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)

        return cls._DATA_BIND_RE.sub(_repl, text)

    @classmethod
    def _bind_component_with_data_model(
        cls,
        value: Any,
        data_model: dict[str, Any],
    ) -> Any:
        if isinstance(value, str):
            return cls._bind_text_with_data_model(value, data_model)
        if isinstance(value, list):
            return [cls._bind_component_with_data_model(item, data_model) for item in value]
        if isinstance(value, dict):
            return {
                str(key): cls._bind_component_with_data_model(item, data_model)
                for key, item in value.items()
            }
        return value

    @classmethod
    def _bind_components_with_data_model(
        cls,
        components: list[dict[str, Any]],
        data_model: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not data_model:
            return [dict(component) for component in components]
        bound: list[dict[str, Any]] = []
        for component in components:
            mapped = cls._bind_component_with_data_model(component, data_model)
            if isinstance(mapped, dict):
                bound.append(mapped)
        return bound

    def _surface_state_summary_for_session(self, session_id: str) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for (state_session_id, surface_id), state in self._surface_states.items():
            if state_session_id != session_id:
                continue
            summary[surface_id] = {
                "dataModel": state.data_model,
                "markdown": state.rendered.markdown[:200],
            }
        return summary

    def _clear_surface_states_for_session(self, session_id: str) -> None:
        stale_keys = [key for key in self._surface_states.keys() if key[0] == session_id]
        for key in stale_keys:
            self._surface_states.pop(key, None)

    def _structured_reply_from_a2ui(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        fallback_markdown: str,
    ) -> StructuredReply | None:
        messages = self._extract_a2ui_messages(payload)
        if not messages:
            return None

        latest: StructuredReply | None = None
        directives: list[SurfaceDirective] = []
        for message in messages:
            message_type = str(message.get("type", "")).strip().lower()
            surface_id = self._surface_id_from_message(message)
            if surface_id and message_type in {
                "createsurface",
                "updatecomponents",
                "updatedatamodel",
                "deletesurface",
            }:
                directives.append(
                    SurfaceDirective(
                        type=message_type,
                        surface_id=surface_id,
                    )
                )

            if message_type == "createsurface":
                template_components = self._coerce_components(message.get("components"))
                data_model_raw = message.get("dataModel")
                data_model = dict(data_model_raw) if isinstance(data_model_raw, dict) else {}
                rendered_components = self._bind_components_with_data_model(template_components, data_model)
                rendered = self._render_from_components(rendered_components, fallback_markdown)
                if surface_id:
                    self._surface_states[self._surface_state_key(session_id, surface_id)] = _SurfaceState(
                        rendered=rendered,
                        template_components=tuple(template_components),
                        data_model=data_model,
                    )
                latest = rendered
                continue

            if message_type == "updatecomponents":
                if not surface_id:
                    continue
                state_key = self._surface_state_key(session_id, surface_id)
                state = self._surface_states.get(state_key)
                if state is None:
                    continue
                template_components = self._coerce_components(message.get("components"))
                rendered_components = self._bind_components_with_data_model(template_components, state.data_model)
                rendered = self._render_from_components(rendered_components, state.rendered.markdown)
                state.template_components = tuple(template_components)
                state.rendered = rendered
                latest = rendered
                continue

            if message_type == "updatedatamodel":
                if not surface_id:
                    continue
                state_key = self._surface_state_key(session_id, surface_id)
                state = self._surface_states.get(state_key)
                if state is None:
                    continue
                data_model = message.get("dataModel")
                if isinstance(data_model, dict):
                    state.data_model = dict(data_model)
                    rendered_components = self._bind_components_with_data_model(
                        list(state.template_components),
                        state.data_model,
                    )
                    rendered = self._render_from_components(
                        rendered_components,
                        state.rendered.markdown,
                    )
                    state.rendered = rendered
                    latest = rendered
                continue

            if message_type == "deletesurface":
                if surface_id:
                    self._surface_states.pop(self._surface_state_key(session_id, surface_id), None)
                continue

        if latest is not None:
            return StructuredReply(
                markdown=latest.markdown,
                ui_intent=latest.ui_intent,
                image_urls=latest.image_urls,
                a2ui_components=latest.a2ui_components,
                surface_directives=tuple(directives),
            )
        if directives:
            return StructuredReply(
                markdown="",
                ui_intent=None,
                image_urls=(),
                a2ui_components=(),
                surface_directives=tuple(directives),
            )
        return None

    def _structured_reply_from_text(
        self,
        raw_reply: str,
        *,
        session_id: str = "__global__",
    ) -> StructuredReply:
        text = self._normalize_reply(raw_reply)
        json_text = self._extract_json_object_text(text)
        if json_text is None:
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=self._extract_markdown_image_urls(text),
            )

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=self._extract_markdown_image_urls(text),
            )

        if not isinstance(payload, dict):
            return StructuredReply(
                markdown=text,
                ui_intent=None,
                image_urls=self._extract_markdown_image_urls(text),
            )

        markdown = str(payload.get("markdown", "")).strip()
        if not markdown:
            markdown = text

        a2ui_structured = self._structured_reply_from_a2ui(
            payload,
            session_id=session_id,
            fallback_markdown=markdown,
        )
        if a2ui_structured is not None:
            return a2ui_structured

        ui_intent = self._parse_ui_intent(payload.get("ui_intent"))
        image_urls: list[str] = []
        raw_images = payload.get("images")
        if isinstance(raw_images, list):
            for item in raw_images:
                normalized = self._normalize_image_url(item)
                if normalized is None or normalized in image_urls:
                    continue
                image_urls.append(normalized)
                if len(image_urls) >= self._MAX_OUTPUT_IMAGES:
                    break

        for md_image in self._extract_markdown_image_urls(markdown):
            if md_image in image_urls:
                continue
            image_urls.append(md_image)
            if len(image_urls) >= self._MAX_OUTPUT_IMAGES:
                break

        return StructuredReply(
            markdown=markdown,
            ui_intent=ui_intent,
            image_urls=tuple(image_urls),
            surface_directives=(),
        )

    async def handle_ui_action(
        self,
        *,
        session_id: str,
        actor_id: str,
        action: str,
        payload: str | None,
        send_reply: Callable[..., Awaitable[Any]],
    ) -> str | None:
        now = self._time_fn()
        if not await self._is_authenticated(session_id, now):
            await self._mark_activity(session_id, now)
            return await self._build_auth_prompt(session_id, now)

        context = await self._store.get_context(session_id)
        if not context:
            return "操作できる会話履歴がありません。"

        action_name = action.strip() or "noop"
        lines = [f"UIアクションが実行されました: {action_name}"]
        if payload:
            lines.append(f"payload: {payload}")
        state_summary = self._surface_state_summary_for_session(session_id)
        if state_summary:
            lines.append("現在のSurface状態:")
            lines.append(json.dumps(state_summary, ensure_ascii=False))
        lines.append("この操作に対する返答を1件生成してください。")
        prompt = "\n".join(lines)

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
            logger.exception(
                "UI action execution failed. session_id=%s actor_id=%s action=%s",
                session_id,
                actor_id,
                action_name,
            )
            await self._mark_activity(session_id, self._time_fn())
            await self._send_reply_safely(send_reply, self._fallback_message)
            return None

        structured = self._structured_reply_from_text(reply, session_id=session_id)
        if not structured.markdown and not structured.surface_directives:
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
            a2ui_components=structured.a2ui_components,
            surface_directives=structured.surface_directives,
        )
        return None

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
        a2ui_components: tuple[dict[str, Any], ...] = (),
        surface_directives: tuple[SurfaceDirective, ...] = (),
    ) -> None:
        try:
            await send_reply(
                text,
                ui_intent=ui_intent,
                image_urls=image_urls,
                a2ui_components=a2ui_components,
                surface_directives=surface_directives,
            )
        except TypeError:
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
        a2ui_components: tuple[dict[str, Any], ...] = (),
        surface_directives: tuple[SurfaceDirective, ...] = (),
    ) -> None:
        chunks = self._split_discord_message(text)
        if not chunks:
            return
        await self._send_reply_dispatch(
            send_reply,
            chunks[0],
            ui_intent=ui_intent,
            image_urls=image_urls,
            a2ui_components=a2ui_components,
            surface_directives=surface_directives,
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
            self._clear_surface_states_for_session(session_id)
            await self._send_reply_safely(send_reply, "このチャンネルの会話コンテキストをリセットしました。")
            return

        auth_attempt = self._extract_auth_attempt(content)
        if auth_attempt is not None:
            auth_response = await self._auth_response_for_attempt(session_id, auth_attempt, now)
            if auth_response.startswith("認証に成功しました。"):
                await self._store.clear(session_id)
                self._clear_surface_states_for_session(session_id)
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
            async def _progress_update(text: str) -> None:
                await self._send_reply_safely(send_reply, text)

            reply = await self._runtime.generate_reply(
                AgentRequest(
                    session_id=session_id,
                    context=context,
                    image_attachments=tuple(image_attachments),
                    progress_callback=_progress_update,
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
                structured_fallback = self._structured_reply_from_text(reply, session_id=session_id)
                await self._send_reply_safely(
                    send_reply,
                    structured_fallback.markdown,
                    ui_intent=structured_fallback.ui_intent,
                    image_urls=structured_fallback.image_urls,
                    a2ui_components=structured_fallback.a2ui_components,
                    surface_directives=structured_fallback.surface_directives,
                )
                return
            await self._mark_activity(session_id, self._time_fn())
            await self._send_reply_safely(send_reply, self._fallback_message)
            return

        structured = self._structured_reply_from_text(reply, session_id=session_id)
        if not structured.markdown and not structured.surface_directives:
            logger.warning("Runtime returned an empty reply. session_id=%s", session_id)
            structured = StructuredReply(markdown=self._fallback_message)

        if structured.markdown:
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
            a2ui_components=structured.a2ui_components,
            surface_directives=structured.surface_directives,
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
        while rerun_context and rerun_context[-1].get("role") == "assistant":
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

        structured = self._structured_reply_from_text(reply, session_id=session_id)
        if not structured.markdown and not structured.surface_directives:
            structured = StructuredReply(markdown=self._fallback_message)

        if structured.markdown:
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
            a2ui_components=structured.a2ui_components,
            surface_directives=structured.surface_directives,
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

        structured = self._structured_reply_from_text(reply, session_id=session_id)
        if not structured.markdown and not structured.surface_directives:
            structured = StructuredReply(markdown=self._fallback_message)

        if structured.markdown:
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
            a2ui_components=structured.a2ui_components,
            surface_directives=structured.surface_directives,
        )
        return None


async def _to_envelope(message: Any) -> MessageEnvelope:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)

    thread_id = None
    raw_thread_id = getattr(message, "thread_id", None)
    if raw_thread_id is not None:
        thread_id = str(raw_thread_id)
    elif channel is not None and hasattr(channel, "id"):
        # For some payloads `message.thread` is missing even when channel itself is a thread.
        thread = getattr(message, "thread", None)
        if thread is not None and hasattr(thread, "id"):
            thread_id = str(thread.id)
        elif getattr(channel, "parent_id", None) is not None:
            thread_id = str(channel.id)

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
    _MAX_LAYOUT_ITEMS = 25
    _LAYOUT_TOP_LEVEL_TYPES = {
        "text",
        "textdisplay",
        "markdown",
        "separator",
        "thumbnail",
        "media_gallery",
        "mediagallery",
        "container",
        "section",
        "button",
        "action",
        "select",
        "string_select",
    }
    _THREAD_TITLE_BLOCK_PATTERNS = (
        re.compile(r"^\s*[-*]\s*"),
        re.compile(r"^\s*\d+[.)]\s*"),
        re.compile(r"^\s*#+\s*"),
        re.compile(r"^\s*>+\s*"),
        re.compile(r"^\s*`+"),
    )

    @staticmethod
    def _should_auto_thread_for_message(
        envelope: MessageEnvelope,
        *,
        enabled: bool,
        mode: str,
        channel_ids: tuple[str, ...],
        trigger_keywords: tuple[str, ...],
    ) -> bool:
        if not enabled:
            return False
        if envelope.author_is_bot:
            return False
        if envelope.guild_id is None:
            return False
        if envelope.thread_id is not None:
            return False
        if channel_ids and envelope.channel_id not in channel_ids:
            return False
        if mode == "channel":
            return bool(channel_ids)
        normalized = envelope.content.strip().lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in trigger_keywords if keyword)

    @staticmethod
    def _auto_thread_name(message: Any) -> str:
        display_name = str(getattr(getattr(message, "author", None), "display_name", "")).strip()
        if not display_name:
            display_name = "thread"
        base = f"{display_name}-{getattr(message, 'id', '')}"
        return base[:95] if len(base) > 95 else base

    @staticmethod
    def _resolve_thread_for_rename(
        *,
        auto_thread: Any | None,
        reply_channel: Any,
        envelope: MessageEnvelope,
    ) -> Any | None:
        if auto_thread is not None:
            return auto_thread
        if envelope.thread_id is None:
            return None
        reply_channel_id = str(getattr(reply_channel, "id", "") or "").strip()
        if (
            reply_channel_id
            and reply_channel_id == envelope.thread_id
            and hasattr(reply_channel, "edit")
        ):
            return reply_channel
        return None

    @staticmethod
    def _build_thread_title_from_reply(text: str, *, fallback: str = "thread") -> str:
        raw = text.strip()
        if not raw:
            return fallback[:95]
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return fallback[:95]
        line = lines[0]
        for pattern in DeepbotClientFactory._THREAD_TITLE_BLOCK_PATTERNS:
            line = pattern.sub("", line).strip()
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = re.sub(r"__(.*?)__", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
        sentence = re.split(r"[。.!?\n]", line, maxsplit=1)[0].strip()
        candidate = sentence or line or fallback
        if len(candidate) > 95:
            candidate = candidate[:95].rstrip()
        return candidate or fallback[:95]

    @staticmethod
    def _should_use_reply_for_thread_title(
        text: str,
        *,
        processing_message: str | None = None,
        fallback_message: str | None = None,
    ) -> bool:
        body = text.strip()
        if not body:
            return False
        if processing_message and body == processing_message.strip():
            return False
        if fallback_message and body == fallback_message.strip():
            return False
        if body.startswith("調査を続けています"):
            return False
        if body.startswith("続行するには") and "/auth" in body:
            return False
        if body.startswith("認証に成功しました。"):
            return False
        if body.startswith("認証に失敗しました。"):
            return False
        if body.startswith("このセッションは一時ロック中です。"):
            return False
        return True

    @staticmethod
    async def _maybe_rename_thread_from_reply(
        *,
        thread: Any | None,
        text: str,
        renamed_threads: set[str],
        enabled: bool,
        processing_message: str | None = None,
        fallback_message: str | None = None,
    ) -> None:
        if not enabled or thread is None:
            return
        thread_id = str(getattr(thread, "id", "") or "").strip()
        if not thread_id or thread_id in renamed_threads:
            return
        if not DeepbotClientFactory._should_use_reply_for_thread_title(
            text,
            processing_message=processing_message,
            fallback_message=fallback_message,
        ):
            return
        fallback_name = str(getattr(thread, "name", "thread") or "thread")
        new_name = DeepbotClientFactory._build_thread_title_from_reply(
            text,
            fallback=fallback_name,
        )
        if new_name == fallback_name:
            renamed_threads.add(thread_id)
            return
        try:
            await thread.edit(name=new_name)
            renamed_threads.add(thread_id)
        except Exception as exc:
            logger.warning("Auto thread rename failed. thread_id=%s (%s)", thread_id, exc)

    @staticmethod
    async def _maybe_start_auto_thread(
        message: Any,
        envelope: MessageEnvelope,
        *,
        enabled: bool,
        mode: str,
        channel_ids: tuple[str, ...],
        trigger_keywords: tuple[str, ...],
        archive_minutes: int,
    ) -> Any | None:
        if not DeepbotClientFactory._should_auto_thread_for_message(
            envelope,
            enabled=enabled,
            mode=mode,
            channel_ids=channel_ids,
            trigger_keywords=trigger_keywords,
        ):
            return None
        try:
            return await message.create_thread(
                name=DeepbotClientFactory._auto_thread_name(message),
                auto_archive_duration=archive_minutes,
            )
        except Exception as exc:
            logger.warning(
                "Auto thread creation failed. channel_id=%s message_id=%s (%s)",
                envelope.channel_id,
                envelope.message_id,
                exc,
            )
            return None

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
    def _component_type(component: dict[str, Any]) -> str:
        return str(component.get("type", "")).strip().lower()

    @staticmethod
    def _component_children(component: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("components", "children", "items"):
            value = component.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _collect_section_select_components(component: dict[str, Any]) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        queue = DeepbotClientFactory._component_children(component)
        while queue:
            child = queue.pop(0)
            child_type = DeepbotClientFactory._component_type(child)
            if child_type in {"select", "string_select"}:
                collected.append(child)
                continue
            queue.extend(DeepbotClientFactory._component_children(child))
        return collected

    @staticmethod
    def _build_button_item(
        discord_module: Any,
        component: dict[str, Any],
        *,
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        label = str(component.get("label", "")).strip()[:80]
        if not label:
            return None
        style = DeepbotClientFactory._button_style(discord_module, str(component.get("style", "secondary")))
        kwargs: dict[str, Any] = {"label": label, "style": style}
        url = MessageProcessor._normalize_image_url(component.get("url"))
        raw_action = str(component.get("action", "")).strip() or None
        payload = str(component.get("payload", "")).strip() or None
        if url is not None and raw_action is None:
            style = discord_module.ButtonStyle.link
            kwargs["style"] = style
        action = raw_action or "noop"
        if style == discord_module.ButtonStyle.link:
            if not url:
                return None
            kwargs["url"] = url
        button = discord_module.ui.Button(**kwargs)
        if style != discord_module.ButtonStyle.link:
            async def _on_click(
                interaction: Any,
                *,
                resolved_action: str = action,
                resolved_payload: str | None = payload,
            ) -> None:
                await on_action(interaction, resolved_action, resolved_payload)

            button.callback = _on_click  # type: ignore[assignment]
        return button

    @staticmethod
    def _build_select_item(
        discord_module: Any,
        component: dict[str, Any],
        *,
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        raw_options = component.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            return None
        options: list[Any] = []
        for raw in raw_options:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label", "")).strip()[:100]
            value = str(raw.get("value", label)).strip()[:100]
            if not label or not value:
                continue
            options.append(
                discord_module.SelectOption(
                    label=label,
                    value=value,
                    description=str(raw.get("description", "")).strip()[:100] or None,
                    default=bool(raw.get("default", False)),
                )
            )
        if not options:
            return None

        min_values = int(component.get("min_values", 1) or 1)
        max_values = int(component.get("max_values", 1) or 1)
        max_values = max(min_values, max_values)
        max_values = min(max_values, len(options))
        select = discord_module.ui.Select(
            placeholder=str(component.get("placeholder", "")).strip()[:150] or None,
            options=options,
            min_values=max(0, min_values),
            max_values=max_values,
            disabled=bool(component.get("disabled", False)),
        )
        action = str(component.get("action", "")).strip() or "select"
        base_payload = str(component.get("payload", "")).strip() or None

        async def _on_select(interaction: Any) -> None:
            selected_values = tuple(getattr(select, "values", ()))
            payload_obj = {"selected": list(selected_values)}
            if base_payload:
                payload_obj["payload"] = base_payload
            payload_text = json.dumps(payload_obj, ensure_ascii=False)
            await on_action(interaction, action, payload_text)

        select.callback = _on_select  # type: ignore[assignment]
        return select

    @staticmethod
    def _build_layout_item(
        discord_module: Any,
        component: dict[str, Any],
        *,
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        comp_type = DeepbotClientFactory._component_type(component)
        if comp_type in {"button", "action"}:
            return DeepbotClientFactory._build_button_item(
                discord_module,
                component,
                on_action=on_action,
            )
        if comp_type in {"select", "string_select"}:
            return DeepbotClientFactory._build_select_item(
                discord_module,
                component,
                on_action=on_action,
            )
        if comp_type in {"text", "textdisplay", "markdown"}:
            text = MessageProcessor._extract_text_from_component(component)
            if not text:
                return None
            return discord_module.ui.TextDisplay(text)
        if comp_type == "separator":
            return discord_module.ui.Separator()
        if comp_type == "thumbnail":
            media_url = MessageProcessor._normalize_image_url(component.get("url"))
            if not media_url:
                return None
            description = str(component.get("description", "")).strip() or None
            return discord_module.ui.Thumbnail(media_url, description=description)
        if comp_type in {"media_gallery", "mediagallery"}:
            raw_items = component.get("items")
            if not isinstance(raw_items, list):
                return None
            gallery_items: list[Any] = []
            for raw in raw_items:
                if isinstance(raw, dict):
                    media_url = MessageProcessor._normalize_image_url(raw.get("url"))
                    if not media_url:
                        continue
                    gallery_items.append(
                        discord_module.MediaGalleryItem(
                            media_url,
                            description=str(raw.get("description", "")).strip() or None,
                        )
                    )
                elif isinstance(raw, str):
                    media_url = MessageProcessor._normalize_image_url(raw)
                    if media_url:
                        gallery_items.append(discord_module.MediaGalleryItem(media_url))
            if not gallery_items:
                return None
            return discord_module.ui.MediaGallery(*gallery_items[:10])
        if comp_type == "container":
            children = DeepbotClientFactory._component_children(component)
            child_items: list[Any] = []
            for child in children:
                item = DeepbotClientFactory._build_layout_item(
                    discord_module,
                    child,
                    on_action=on_action,
                )
                if item is None:
                    continue
                child_items.append(item)
                if len(child_items) >= DeepbotClientFactory._MAX_LAYOUT_ITEMS:
                    break
            if not child_items:
                return None
            return discord_module.ui.Container(*child_items)
        if comp_type == "section":
            children = DeepbotClientFactory._component_children(component)
            section_children: list[Any] = []
            accessory_item: Any | None = None
            for child in children:
                child_type = DeepbotClientFactory._component_type(child)
                if accessory_item is None and child_type in {"button", "action", "thumbnail"}:
                    accessory_item = DeepbotClientFactory._build_layout_item(
                        discord_module,
                        child,
                        on_action=on_action,
                    )
                    continue
                # select類はSection直下に置けないため、Section外のActionRow描画に回す。
                if child_type in {"select", "string_select"}:
                    continue
                text = MessageProcessor._extract_text_from_component(child)
                if text:
                    section_children.append(text)
                else:
                    rendered = DeepbotClientFactory._build_layout_item(
                        discord_module,
                        child,
                        on_action=on_action,
                    )
                    if rendered is not None:
                        section_children.append(rendered)
            if accessory_item is None:
                return None
            if not section_children:
                fallback_title = str(component.get("title", "")).strip() or " "
                section_children = [fallback_title]
            return discord_module.ui.Section(*section_children[:3], accessory=accessory_item)
        return None

    @staticmethod
    def _build_layout_view(
        discord_module: Any,
        a2ui_components: tuple[dict[str, Any], ...],
        *,
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        if not a2ui_components:
            return None
        if not hasattr(discord_module.ui, "LayoutView"):
            return None
        view = discord_module.ui.LayoutView(timeout=600)
        for component in a2ui_components:
            comp_type = DeepbotClientFactory._component_type(component)
            if comp_type not in DeepbotClientFactory._LAYOUT_TOP_LEVEL_TYPES:
                continue
            if comp_type in {"button", "action", "select", "string_select"}:
                interactive = DeepbotClientFactory._build_layout_item(
                    discord_module,
                    component,
                    on_action=on_action,
                )
                if interactive is None:
                    continue
                try:
                    view.add_item(discord_module.ui.ActionRow(interactive))
                except Exception as exc:
                    logger.warning("A2UI interactive component failed to render in ActionRow: %s", exc)
                continue
            item = DeepbotClientFactory._build_layout_item(
                discord_module,
                component,
                on_action=on_action,
            )
            if item is None:
                continue
            try:
                view.add_item(item)
            except Exception as exc:
                logger.warning("A2UI component failed to render in LayoutView: %s", exc)
                continue
            if comp_type == "section":
                for select_component in DeepbotClientFactory._collect_section_select_components(component):
                    select_item = DeepbotClientFactory._build_layout_item(
                        discord_module,
                        select_component,
                        on_action=on_action,
                    )
                    if select_item is None:
                        continue
                    try:
                        view.add_item(discord_module.ui.ActionRow(select_item))
                    except Exception as exc:
                        logger.warning("Section select failed to render in ActionRow: %s", exc)
                        continue
            if len(view.children) >= DeepbotClientFactory._MAX_LAYOUT_ITEMS:
                break
        if not view.children:
            return None
        return view

    @staticmethod
    def _build_view(
        discord_module: Any,
        ui_intent: UiIntent | None,
        *,
        a2ui_components: tuple[dict[str, Any], ...] = (),
        on_action: Callable[[Any, str, str | None], Awaitable[None]],
    ) -> Any | None:
        a2ui_view = DeepbotClientFactory._build_layout_view(
            discord_module,
            a2ui_components,
            on_action=on_action,
        )
        if a2ui_view is not None:
            return a2ui_view

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
    def _is_layout_view(view: Any | None) -> bool:
        if view is None:
            return False
        cls_name = type(view).__name__
        return cls_name == "LayoutView"

    @staticmethod
    def _build_image_embeds(discord_module: Any, image_urls: tuple[str, ...]) -> list[Any]:
        embeds: list[Any] = []
        for image_url in image_urls:
            embed = discord_module.Embed()
            embed.set_image(url=image_url)
            embeds.append(embed)
        return embeds

    @staticmethod
    def _last_surface_directive(
        surface_directives: tuple[SurfaceDirective, ...],
    ) -> SurfaceDirective | None:
        if not surface_directives:
            return None
        return surface_directives[-1]

    @staticmethod
    async def _send_or_update_surface_message(
        *,
        channel: Any,
        session_id: str,
        surface_messages: dict[tuple[str, str], Any],
        directive: SurfaceDirective,
        content: str | None,
        view: Any | None,
        embeds: list[Any],
    ) -> Any | None:
        message_key = (session_id, directive.surface_id)
        existing_message = surface_messages.get(message_key)
        if directive.type == "deletesurface":
            if existing_message is not None:
                try:
                    await existing_message.delete()
                finally:
                    surface_messages.pop(message_key, None)
            return None

        if existing_message is not None and directive.type in {"updatecomponents", "updatedatamodel"}:
            try:
                await existing_message.edit(content=content, view=view, embeds=embeds)
            except Exception as exc:
                logger.warning("Surface message edit failed with view. Retrying without view: %s", exc)
                fallback_content = content
                if fallback_content is None and not embeds:
                    fallback_content = " "
                await existing_message.edit(content=fallback_content, embeds=embeds)
            return existing_message

        try:
            sent = await channel.send(content=content, view=view, embeds=embeds)
        except Exception as exc:
            logger.warning("Surface message send failed with view. Retrying without view: %s", exc)
            fallback_content = content
            if fallback_content is None and not embeds:
                fallback_content = " "
            sent = await channel.send(content=fallback_content, embeds=embeds)
        surface_messages[message_key] = sent
        return sent

    @staticmethod
    async def _send_interaction_ephemeral(interaction: Any, text: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
            return
        await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def create(
        *,
        processor: MessageProcessor,
        auto_thread_enabled: bool = False,
        auto_thread_mode: str = "keyword",
        auto_thread_channel_ids: tuple[str, ...] = (),
        auto_thread_trigger_keywords: tuple[str, ...] = (),
        auto_thread_archive_minutes: int = 1440,
        auto_thread_rename_from_reply: bool = True,
    ):
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
                auto_thread = await DeepbotClientFactory._maybe_start_auto_thread(
                    message,
                    envelope,
                    enabled=auto_thread_enabled,
                    mode=auto_thread_mode,
                    channel_ids=auto_thread_channel_ids,
                    trigger_keywords=auto_thread_trigger_keywords,
                    archive_minutes=auto_thread_archive_minutes,
                )
                reply_channel = auto_thread or message.channel
                if auto_thread is not None and envelope.thread_id is None:
                    thread_id = str(getattr(auto_thread, "id", "") or "").strip()
                    if thread_id:
                        envelope = replace(envelope, thread_id=thread_id)
                thread_for_rename = DeepbotClientFactory._resolve_thread_for_rename(
                    auto_thread=auto_thread,
                    reply_channel=reply_channel,
                    envelope=envelope,
                )
                session_id = MessageProcessor.build_session_id(envelope)
                owner_user_id = envelope.author_id
                surface_messages: dict[tuple[str, str], Any] = getattr(self, "_surface_messages", {})
                setattr(self, "_surface_messages", surface_messages)
                renamed_threads: set[str] = getattr(self, "_renamed_threads", set())
                setattr(self, "_renamed_threads", renamed_threads)
                processing_message = processor._processing_message
                fallback_message = processor._fallback_message

                async def _send_channel_reply(
                    text: str,
                    *,
                    ui_intent: UiIntent | None = None,
                    image_urls: tuple[str, ...] = (),
                    a2ui_components: tuple[dict[str, Any], ...] = (),
                    surface_directives: tuple[SurfaceDirective, ...] = (),
                ) -> Any:
                    view = DeepbotClientFactory._build_view(
                        discord,
                        ui_intent,
                        a2ui_components=a2ui_components,
                        on_action=_on_button_action,
                    )
                    embeds = DeepbotClientFactory._build_image_embeds(discord, image_urls)
                    content = text if text else None
                    if DeepbotClientFactory._is_layout_view(view):
                        # Components V2 forbids using message content with LayoutView.
                        content = None
                    surface_directive = DeepbotClientFactory._last_surface_directive(surface_directives)
                    if surface_directive is not None:
                        sent = await DeepbotClientFactory._send_or_update_surface_message(
                            channel=reply_channel,
                            session_id=session_id,
                            surface_messages=surface_messages,
                            directive=surface_directive,
                            content=content,
                            view=view,
                            embeds=embeds,
                        )
                        if sent is not None:
                            await DeepbotClientFactory._maybe_rename_thread_from_reply(
                                thread=thread_for_rename,
                                text=text,
                                renamed_threads=renamed_threads,
                                enabled=auto_thread_rename_from_reply,
                                processing_message=processing_message,
                                fallback_message=fallback_message,
                            )
                        return sent
                    if content is None and not embeds and view is None:
                        content = " "
                    sent = await reply_channel.send(content=content, view=view, embeds=embeds)
                    await DeepbotClientFactory._maybe_rename_thread_from_reply(
                        thread=thread_for_rename,
                        text=text,
                        renamed_threads=renamed_threads,
                        enabled=auto_thread_rename_from_reply,
                        processing_message=processing_message,
                        fallback_message=fallback_message,
                    )
                    return sent

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

                        if interaction.response.is_done():
                            await interaction.followup.send("操作を処理しています。", ephemeral=True)
                        else:
                            await interaction.response.send_message("操作を処理しています。", ephemeral=True)
                        error_message = await processor.handle_ui_action(
                            session_id=session_id,
                            actor_id=actor_id,
                            action=action,
                            payload=payload,
                            send_reply=_send_channel_reply,
                        )
                        if error_message:
                            await interaction.followup.send(error_message, ephemeral=True)
                            return
                        await interaction.followup.send("操作を反映しました。", ephemeral=True)
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
