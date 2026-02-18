from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dotenv


dotenv.load_dotenv(override=True)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AppConfig:
    discord_bot_token: str
    strands_model_provider: str
    strands_model_config: dict[str, Any]
    openai_base_url: str | None
    agent_md_path: Path
    session_max_turns: int
    session_ttl_minutes: int
    auto_reply_all: bool
    auto_thread_enabled: bool
    auto_thread_mode: str
    auto_thread_channel_ids: tuple[str, ...]
    auto_thread_trigger_keywords: tuple[str, ...]
    auto_thread_archive_minutes: int
    auto_thread_rename_from_reply: bool
    agent_timeout_seconds: int
    bot_fallback_message: str
    bot_processing_message: str
    log_level: str
    dangerous_tools_enabled: bool
    enabled_dangerous_tools: tuple[str, ...]
    shell_srt_enforced: bool
    shell_srt_settings_path: str
    shell_deny_path_prefixes: tuple[str, ...]
    tool_write_roots: tuple[str, ...]
    auth_passphrase: str
    auth_required: bool
    auth_idle_timeout_minutes: int
    auth_window_minutes: int
    auth_max_retries: int
    auth_lock_minutes: int
    auth_command: str
    defender_enabled: bool
    defender_default_mode: str
    defender_block_threshold: float
    defender_warn_threshold: float
    defender_sanitize_mode: str
    attachment_allowed_hosts: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeSettings:
    max_messages: int
    ttl_seconds: int
    timeout_seconds: int


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    items = [item.strip().lower() for item in raw.split(",")]
    return tuple(item for item in items if item)


def _parse_json_or_file(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}

    raw = raw.strip()
    maybe_path = Path(raw).expanduser()
    if maybe_path.exists() and maybe_path.is_file():
        return json.loads(maybe_path.read_text())
    return json.loads(raw)


def _resolve_agent_md_path() -> Path:
    config_dir = os.environ.get("DEEPBOT_CONFIG_DIR", "/app/config").strip()
    if config_dir:
        return Path(config_dir).expanduser() / "AGENT.md"

    dotenv_path = dotenv.find_dotenv(usecwd=True)
    base_dir = Path(dotenv_path).resolve().parent if dotenv_path else Path.cwd().resolve()
    return base_dir / "AGENT.md"


def load_config() -> AppConfig:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("DISCORD_BOT_TOKEN is required")

    provider = os.environ.get("STRANDS_MODEL_PROVIDER", "").strip().lower()
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if provider == "openai" and not openai_api_key and not openai_base_url:
        raise ConfigError(
            "OPENAI_API_KEY is required when STRANDS_MODEL_PROVIDER=openai and OPENAI_BASE_URL is not set"
        )

    try:
        model_config = _parse_json_or_file(os.environ.get("STRANDS_MODEL_CONFIG"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid STRANDS_MODEL_CONFIG: {exc}") from exc
    if not isinstance(model_config, dict):
        raise ConfigError("STRANDS_MODEL_CONFIG must be a JSON object")

    model_id_from_env = (
        os.environ.get("STRANDS_MODEL_ID", "").strip()
        or os.environ.get("OPENAI_MODEL_ID", "").strip()
        or os.environ.get("MODEL_ID", "").strip()
    )
    if model_id_from_env and not str(model_config.get("model_id", "")).strip():
        model_config = dict(model_config)
        model_config["model_id"] = model_id_from_env

    session_max_turns = int(os.environ.get("SESSION_MAX_TURNS", "10"))
    session_ttl_minutes = int(os.environ.get("SESSION_TTL_MINUTES", "30"))
    auto_thread_archive_minutes = int(os.environ.get("AUTO_THREAD_ARCHIVE_MINUTES", "1440"))
    agent_timeout_seconds = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "45"))

    if session_max_turns <= 0:
        raise ConfigError("SESSION_MAX_TURNS must be > 0")
    if session_ttl_minutes <= 0:
        raise ConfigError("SESSION_TTL_MINUTES must be > 0")
    if auto_thread_archive_minutes <= 0:
        raise ConfigError("AUTO_THREAD_ARCHIVE_MINUTES must be > 0")
    if agent_timeout_seconds <= 0:
        raise ConfigError("AGENT_TIMEOUT_SECONDS must be > 0")
    if provider == "openai" and not str(model_config.get("model_id", "")).strip():
        raise ConfigError(
            "model_id is required for openai provider. "
            "Set STRANDS_MODEL_CONFIG.model_id or OPENAI_MODEL_ID/STRANDS_MODEL_ID."
        )

    auth_passphrase = os.environ.get("AUTH_PASSPHRASE", "").strip()
    auto_thread_mode = os.environ.get("AUTO_THREAD_MODE", "keyword").strip().lower() or "keyword"
    enabled_dangerous_tools = _parse_csv(
        os.environ.get("ENABLED_DANGEROUS_TOOLS", "shell,file_read")
    )
    shell_srt_enforced = _parse_bool(os.environ.get("SHELL_SRT_ENFORCED"), default=True)
    shell_srt_settings_path = (
        os.environ.get("SHELL_SRT_SETTINGS_PATH", "/app/config/srt-settings.json").strip()
        or "/app/config/srt-settings.json"
    )
    shell_deny_path_prefixes = _parse_csv(os.environ.get("SHELL_DENY_PATH_PREFIXES", "/app"))
    tool_write_roots = _parse_csv(os.environ.get("TOOL_WRITE_ROOTS", "/workspace"))
    auth_required = _parse_bool(os.environ.get("AUTH_REQUIRED"), default=True)
    auth_idle_timeout_minutes = int(os.environ.get("AUTH_IDLE_TIMEOUT_MINUTES", "15"))
    auth_window_minutes = int(os.environ.get("AUTH_WINDOW_MINUTES", "10"))
    auth_max_retries = int(os.environ.get("AUTH_MAX_RETRIES", "3"))
    auth_lock_minutes = int(os.environ.get("AUTH_LOCK_MINUTES", "30"))
    auth_command = os.environ.get("AUTH_COMMAND", "/auth").strip() or "/auth"
    defender_default_mode = os.environ.get("DEFENDER_DEFAULT_MODE", "warn").strip().lower() or "warn"
    defender_sanitize_mode = os.environ.get("DEFENDER_SANITIZE_MODE", "full-redact").strip().lower() or "full-redact"
    defender_block_threshold = float(os.environ.get("DEFENDER_BLOCK_THRESHOLD", "0.95"))
    defender_warn_threshold = float(os.environ.get("DEFENDER_WARN_THRESHOLD", "0.35"))

    if auth_idle_timeout_minutes <= 0:
        raise ConfigError("AUTH_IDLE_TIMEOUT_MINUTES must be > 0")
    if auto_thread_mode not in {"keyword", "channel"}:
        raise ConfigError("AUTO_THREAD_MODE must be one of: keyword, channel")
    supported_dangerous_tools = {"file_read", "file_write", "editor", "environment", "shell"}
    invalid_dangerous_tools = [name for name in enabled_dangerous_tools if name not in supported_dangerous_tools]
    if invalid_dangerous_tools:
        raise ConfigError(
            "ENABLED_DANGEROUS_TOOLS contains unsupported tools: "
            + ", ".join(sorted(invalid_dangerous_tools))
        )
    if not shell_srt_settings_path.startswith("/"):
        raise ConfigError("SHELL_SRT_SETTINGS_PATH must be an absolute path")
    if not shell_deny_path_prefixes:
        raise ConfigError("SHELL_DENY_PATH_PREFIXES must include at least one prefix")
    if any(not prefix.startswith("/") for prefix in shell_deny_path_prefixes):
        raise ConfigError("SHELL_DENY_PATH_PREFIXES must contain absolute prefixes")
    if not tool_write_roots:
        raise ConfigError("TOOL_WRITE_ROOTS must include at least one root path")
    if any(not root.startswith("/") for root in tool_write_roots):
        raise ConfigError("TOOL_WRITE_ROOTS must contain absolute paths")
    if auth_required and not auth_passphrase:
        raise ConfigError("AUTH_PASSPHRASE is required when AUTH_REQUIRED=true")
    if auth_window_minutes <= 0:
        raise ConfigError("AUTH_WINDOW_MINUTES must be > 0")
    if auth_max_retries <= 0:
        raise ConfigError("AUTH_MAX_RETRIES must be > 0")
    if auth_lock_minutes <= 0:
        raise ConfigError("AUTH_LOCK_MINUTES must be > 0")
    if not auth_command.startswith("/"):
        raise ConfigError("AUTH_COMMAND must start with '/'")
    if defender_default_mode not in {"warn", "sanitize", "block"}:
        raise ConfigError("DEFENDER_DEFAULT_MODE must be one of: warn, sanitize, block")
    if defender_sanitize_mode not in {"full-redact"}:
        raise ConfigError("DEFENDER_SANITIZE_MODE must be 'full-redact'")
    if not (0.0 <= defender_warn_threshold <= 1.0):
        raise ConfigError("DEFENDER_WARN_THRESHOLD must be between 0 and 1")
    if not (0.0 <= defender_block_threshold <= 1.0):
        raise ConfigError("DEFENDER_BLOCK_THRESHOLD must be between 0 and 1")
    if defender_warn_threshold > defender_block_threshold:
        raise ConfigError("DEFENDER_WARN_THRESHOLD must be <= DEFENDER_BLOCK_THRESHOLD")

    attachment_allowed_hosts = _parse_csv(
        os.environ.get(
            "ATTACHMENT_ALLOWED_HOSTS",
            "cdn.discordapp.com,media.discordapp.net",
        )
    )
    if not attachment_allowed_hosts:
        raise ConfigError("ATTACHMENT_ALLOWED_HOSTS must include at least one host")

    return AppConfig(
        discord_bot_token=token,
        strands_model_provider=provider,
        strands_model_config=model_config,
        openai_base_url=openai_base_url,
        agent_md_path=_resolve_agent_md_path(),
        session_max_turns=session_max_turns,
        session_ttl_minutes=session_ttl_minutes,
        auto_reply_all=_parse_bool(os.environ.get("AUTO_REPLY_ALL"), default=True),
        auto_thread_enabled=_parse_bool(os.environ.get("AUTO_THREAD_ENABLED"), default=False),
        auto_thread_mode=auto_thread_mode,
        auto_thread_channel_ids=_parse_csv(os.environ.get("AUTO_THREAD_CHANNEL_IDS", "")),
        auto_thread_trigger_keywords=_parse_csv(
            os.environ.get(
                "AUTO_THREAD_TRIGGER_KEYWORDS",
                "スレッド立てて,/thread,thread please",
            )
        ),
        auto_thread_archive_minutes=auto_thread_archive_minutes,
        auto_thread_rename_from_reply=_parse_bool(
            os.environ.get("AUTO_THREAD_RENAME_FROM_REPLY"),
            default=True,
        ),
        agent_timeout_seconds=agent_timeout_seconds,
        bot_fallback_message=os.environ.get(
            "BOT_FALLBACK_MESSAGE",
            "今ちょっと調子が悪いです。少し待ってからもう一度お願いします。",
        ),
        bot_processing_message=os.environ.get(
            "BOT_PROCESSING_MESSAGE",
            "お調べしますね。少しお待ちください。",
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        dangerous_tools_enabled=_parse_bool(
            os.environ.get("DANGEROUS_TOOLS_ENABLED"),
            default=False,
        ),
        enabled_dangerous_tools=enabled_dangerous_tools,
        shell_srt_enforced=shell_srt_enforced,
        shell_srt_settings_path=shell_srt_settings_path,
        shell_deny_path_prefixes=shell_deny_path_prefixes,
        tool_write_roots=tool_write_roots,
        auth_passphrase=auth_passphrase,
        auth_required=auth_required,
        auth_idle_timeout_minutes=auth_idle_timeout_minutes,
        auth_window_minutes=auth_window_minutes,
        auth_max_retries=auth_max_retries,
        auth_lock_minutes=auth_lock_minutes,
        auth_command=auth_command,
        defender_enabled=_parse_bool(os.environ.get("DEFENDER_ENABLED"), default=True),
        defender_default_mode=defender_default_mode,
        defender_block_threshold=defender_block_threshold,
        defender_warn_threshold=defender_warn_threshold,
        defender_sanitize_mode=defender_sanitize_mode,
        attachment_allowed_hosts=attachment_allowed_hosts,
    )


def to_runtime_settings(config: AppConfig) -> RuntimeSettings:
    return RuntimeSettings(
        max_messages=config.session_max_turns * 2,
        ttl_seconds=config.session_ttl_minutes * 60,
        timeout_seconds=config.agent_timeout_seconds,
    )
