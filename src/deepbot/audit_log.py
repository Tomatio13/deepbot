from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_transcript_root() -> Path:
    raw = os.environ.get("DEEPBOT_TRANSCRIPT_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path("/workspace/transcripts")


def transcripts_enabled() -> bool:
    raw = os.environ.get("DEEPBOT_TRANSCRIPT", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class AuditSession:
    session_id: str
    path: Path


class AuditLogger:
    """Codex-style JSONL audit logger with best-effort writes."""
    _REDACTED = "[REDACTED]"
    _SENSITIVE_KEY_TOKENS = (
        "token",
        "api_key",
        "apikey",
        "passphrase",
        "password",
        "secret",
        "authorization",
        "auth",
    )
    _AUTH_COMMAND_RE = re.compile(r"(?i)(^|\s)(/auth)\s+\S+")
    _KV_SECRET_RE = re.compile(
        r"(?i)\b(api[_-]?key|token|passphrase|password|secret|authorization)\b\s*[:=]\s*([^\s,;]+)"
    )
    _BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")

    def __init__(self, path: Path, *, originator: str = "deepbot") -> None:
        self._path = path
        self._originator = originator
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid.uuid4().hex
        self._write(
            "session_meta",
            {
                "id": self.session_id,
                "timestamp": _now_iso(),
                "cwd": str(Path.cwd()),
                "originator": self._originator,
            },
        )

    @property
    def path(self) -> Path:
        return self._path

    def _write(self, record_type: str, payload: dict[str, Any]) -> None:
        event = {
            "timestamp": _now_iso(),
            "type": record_type,
            "payload": payload,
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def safe_write(self, record_type: str, payload: dict[str, Any]) -> None:
        try:
            self._write(record_type, payload)
        except Exception:
            # Logging must never break the bot runtime path.
            return

    def log_user_message(
        self,
        *,
        session_id: str,
        author_id: str,
        message_id: str,
        guild_id: str | None,
        channel_id: str,
        thread_id: str | None,
        content: str,
        attachment_count: int,
    ) -> None:
        self.safe_write(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "session_id": session_id,
                "author_id": author_id,
                "message_id": message_id,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "attachment_count": attachment_count,
                "content": [{"type": "input_text", "text": self._sanitize_text(content)}],
            },
        )

    def log_assistant_message(
        self,
        *,
        session_id: str,
        content: str,
        image_count: int = 0,
        has_ui_intent: bool = False,
        has_surface_directives: bool = False,
    ) -> None:
        self.safe_write(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "session_id": session_id,
                "image_count": image_count,
                "has_ui_intent": has_ui_intent,
                "has_surface_directives": has_surface_directives,
                "content": [{"type": "output_text", "text": self._sanitize_text(content)}],
            },
        )

    def log_event(self, *, event: str, session_id: str, data: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"event": event, "session_id": session_id}
        if data:
            payload.update(data)
        self.safe_write("event", payload)

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        lowered = key.strip().lower()
        return any(token in lowered for token in cls._SENSITIVE_KEY_TOKENS)

    @classmethod
    def _sanitize_text(cls, text: str) -> str:
        if not text:
            return text
        redacted = cls._AUTH_COMMAND_RE.sub(r"\1\2 " + cls._REDACTED, text)
        redacted = cls._KV_SECRET_RE.sub(lambda m: f"{m.group(1)}={cls._REDACTED}", redacted)
        redacted = cls._BEARER_RE.sub("Bearer " + cls._REDACTED, redacted)
        return redacted

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            masked: dict[str, Any] = {}
            for k, v in value.items():
                key = str(k)
                if cls._is_sensitive_key(key):
                    masked[key] = cls._REDACTED
                else:
                    masked[key] = cls._sanitize_value(v)
            return masked
        if isinstance(value, list):
            return [cls._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize_value(item) for item in value]
        if isinstance(value, str):
            return cls._sanitize_text(value)
        return value

    @classmethod
    def _json_text(cls, value: Any) -> str:
        sanitized = cls._sanitize_value(value)
        if isinstance(sanitized, str):
            return sanitized
        try:
            return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(sanitized)

    def log_function_call(
        self,
        *,
        session_id: str,
        name: str,
        arguments: Any,
        call_id: str,
    ) -> None:
        self.safe_write(
            "response_item",
            {
                "type": "function_call",
                "session_id": session_id,
                "name": name,
                "arguments": self._json_text(arguments),
                "call_id": call_id,
                "started_at": time.time(),
            },
        )

    def log_function_call_output(
        self,
        *,
        session_id: str,
        call_id: str,
        output: Any,
        duration_ms: int | None = None,
        tool_name: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "function_call_output",
            "session_id": session_id,
            "call_id": call_id,
            "output": self._json_text(output),
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if tool_name:
            payload["name"] = tool_name
        self.safe_write("response_item", payload)


def create_audit_logger() -> AuditLogger | None:
    if not transcripts_enabled():
        return None
    now = datetime.now(timezone.utc)
    sessions_dir = (
        _resolve_transcript_root()
        / "sessions"
        / now.strftime("%Y")
        / now.strftime("%m")
        / now.strftime("%d")
    )
    suffix = uuid.uuid4().hex
    filename = f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{suffix}.jsonl"
    return AuditLogger(sessions_dir / filename)
