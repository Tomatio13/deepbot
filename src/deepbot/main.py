from __future__ import annotations

import logging

from deepbot.agent.runtime import create_runtime
from deepbot.config import ConfigError, load_config, to_runtime_settings
from deepbot.gateway.discord_bot import AuthConfig, DeepbotClientFactory, MessageProcessor
from deepbot.logging import setup_logging
from deepbot.memory.session_store import SessionStore
from deepbot.security import DefenderSettings, PromptInjectionDefender


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    settings = to_runtime_settings(config)
    session_store = SessionStore(
        max_messages=settings.max_messages,
        ttl_seconds=settings.ttl_seconds,
    )
    runtime = create_runtime(config, settings)

    processor = MessageProcessor(
        store=session_store,
        runtime=runtime,
        fallback_message=config.bot_fallback_message,
        processing_message=config.bot_processing_message,
        defender=PromptInjectionDefender(
            DefenderSettings(
                enabled=config.defender_enabled,
                default_mode=config.defender_default_mode,
                block_threshold=config.defender_block_threshold,
                warn_threshold=config.defender_warn_threshold,
                sanitize_mode=config.defender_sanitize_mode,
            )
        ),
        auth_config=AuthConfig(
            passphrase=config.auth_passphrase,
            idle_timeout_seconds=config.auth_idle_timeout_minutes * 60,
            auth_window_seconds=config.auth_window_minutes * 60,
            max_retries=config.auth_max_retries,
            lock_seconds=config.auth_lock_minutes * 60,
            auth_command=config.auth_command,
        ),
        allowed_attachment_hosts=config.attachment_allowed_hosts,
    )
    client = DeepbotClientFactory.create(processor=processor)

    logger.info("Starting Deepbot (auto_reply_all=%s)", config.auto_reply_all)
    client.run(config.discord_bot_token)


if __name__ == "__main__":
    main()
