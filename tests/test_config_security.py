from __future__ import annotations

import pytest

from deepbot.config import ConfigError, load_config


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token")
    monkeypatch.setenv("STRANDS_MODEL_PROVIDER", "")
    monkeypatch.setenv("STRANDS_MODEL_CONFIG", "{}")
    monkeypatch.setenv("AUTH_IDLE_TIMEOUT_MINUTES", "15")
    monkeypatch.setenv("AUTH_WINDOW_MINUTES", "10")
    monkeypatch.setenv("AUTH_MAX_RETRIES", "3")
    monkeypatch.setenv("AUTH_LOCK_MINUTES", "30")
    monkeypatch.setenv("ATTACHMENT_ALLOWED_HOSTS", "cdn.discordapp.com")


def test_config_fails_closed_when_auth_required_and_passphrase_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")

    with pytest.raises(ConfigError, match="AUTH_PASSPHRASE is required"):
        load_config()


def test_config_allows_disabled_auth_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")

    config = load_config()
    assert config.auth_required is False


def test_config_rejects_empty_attachment_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("ATTACHMENT_ALLOWED_HOSTS", " , ")

    with pytest.raises(ConfigError, match="ATTACHMENT_ALLOWED_HOSTS"):
        load_config()


def test_config_rejects_invalid_enabled_dangerous_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("ENABLED_DANGEROUS_TOOLS", "shell,unknown_tool")

    with pytest.raises(ConfigError, match="ENABLED_DANGEROUS_TOOLS"):
        load_config()


def test_config_rejects_relative_tool_write_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("TOOL_WRITE_ROOTS", "workspace")

    with pytest.raises(ConfigError, match="TOOL_WRITE_ROOTS"):
        load_config()


def test_config_rejects_relative_tool_read_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("TOOL_READ_ROOTS", "workspace")

    with pytest.raises(ConfigError, match="TOOL_READ_ROOTS"):
        load_config()


def test_config_rejects_relative_shell_deny_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("SHELL_DENY_PATH_PREFIXES", "app")

    with pytest.raises(ConfigError, match="SHELL_DENY_PATH_PREFIXES"):
        load_config()


def test_config_allows_openai_provider_without_key_when_base_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("STRANDS_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://litellm:4000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL_ID", "gpt-4o-mini")

    config = load_config()
    assert config.strands_model_provider == "openai"
    assert config.openai_base_url == "http://litellm:4000/v1"


def test_config_rejects_openai_provider_without_key_and_without_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("STRANDS_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL_ID", "gpt-4o-mini")

    with pytest.raises(ConfigError, match="OPENAI_API_KEY is required"):
        load_config()


def test_config_parses_auto_thread_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("AUTO_THREAD_ENABLED", "true")
    monkeypatch.setenv("AUTO_THREAD_MODE", "keyword")
    monkeypatch.setenv("AUTO_THREAD_CHANNEL_IDS", "12345,67890")
    monkeypatch.setenv("AUTO_THREAD_TRIGGER_KEYWORDS", "スレッド立てて,/thread")
    monkeypatch.setenv("AUTO_THREAD_ARCHIVE_MINUTES", "1440")
    monkeypatch.setenv("AUTO_THREAD_RENAME_FROM_REPLY", "true")

    config = load_config()
    assert config.auto_thread_enabled is True
    assert config.auto_thread_mode == "keyword"
    assert config.auto_thread_channel_ids == ("12345", "67890")
    assert config.auto_thread_trigger_keywords == ("スレッド立てて", "/thread")
    assert config.auto_thread_archive_minutes == 1440
    assert config.auto_thread_rename_from_reply is True


def test_config_rejects_non_positive_auto_thread_archive_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("AUTO_THREAD_ARCHIVE_MINUTES", "0")

    with pytest.raises(ConfigError, match="AUTO_THREAD_ARCHIVE_MINUTES must be > 0"):
        load_config()


def test_config_rejects_invalid_auto_thread_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("AUTO_THREAD_MODE", "invalid")

    with pytest.raises(ConfigError, match="AUTO_THREAD_MODE must be one of"):
        load_config()


def test_config_parses_claude_subagent_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_SUBAGENT_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_SUBAGENT_COMMAND", "claude")
    monkeypatch.setenv("CLAUDE_SUBAGENT_WORKDIR", "/workspace/bot-rw")
    monkeypatch.setenv("CLAUDE_SUBAGENT_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("CLAUDE_SUBAGENT_MODEL", "sonnet")
    monkeypatch.setenv("CLAUDE_SUBAGENT_SKIP_PERMISSIONS", "true")

    config = load_config()
    assert config.claude_subagent_enabled is True
    assert config.claude_subagent_command == "claude"
    assert config.claude_subagent_workdir == "/workspace/bot-rw"
    assert config.claude_subagent_timeout_seconds == 180
    assert config.claude_subagent_model == "sonnet"
    assert config.claude_subagent_skip_permissions is True


def test_config_rejects_relative_claude_subagent_workdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_SUBAGENT_WORKDIR", "workspace")

    with pytest.raises(ConfigError, match="CLAUDE_SUBAGENT_WORKDIR"):
        load_config()


def test_config_rejects_invalid_claude_subagent_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_SUBAGENT_TRANSPORT", "invalid")

    with pytest.raises(ConfigError, match="CLAUDE_SUBAGENT_TRANSPORT"):
        load_config()


def test_config_requires_http_sidecar_url_when_sidecar_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_SUBAGENT_TRANSPORT", "sidecar")
    monkeypatch.setenv("CLAUDE_SUBAGENT_SIDECAR_URL", "claude-runner:8787/v1/run")

    with pytest.raises(ConfigError, match="CLAUDE_SUBAGENT_SIDECAR_URL"):
        load_config()


def test_config_parses_claude_hooks_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_HOOKS_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_HOOKS_TIMEOUT_MS", "2500")
    monkeypatch.setenv("CLAUDE_HOOKS_FAIL_MODE", "closed")
    monkeypatch.setenv(
        "CLAUDE_HOOKS_SETTINGS_PATHS",
        ".claude/settings.local.json,.claude/settings.json,~/.claude/settings.json",
    )

    config = load_config()
    assert config.claude_hooks_enabled is True
    assert config.claude_hooks_timeout_ms == 2500
    assert config.claude_hooks_fail_mode == "closed"
    assert config.claude_hooks_settings_paths == (
        ".claude/settings.local.json",
        ".claude/settings.json",
        "~/.claude/settings.json",
    )


def test_config_rejects_invalid_claude_hooks_fail_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_base_env(monkeypatch)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("AUTH_PASSPHRASE", "")
    monkeypatch.setenv("CLAUDE_HOOKS_FAIL_MODE", "invalid")

    with pytest.raises(ConfigError, match="CLAUDE_HOOKS_FAIL_MODE"):
        load_config()
