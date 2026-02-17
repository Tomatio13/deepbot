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
