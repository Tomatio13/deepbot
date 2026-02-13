from __future__ import annotations

import logging

from deepbot.agent.runtime import create_runtime
from deepbot.config import ConfigError, load_config, to_runtime_settings
from deepbot.gateway.discord_bot import DeepbotClientFactory, MessageProcessor
from deepbot.logging import setup_logging
from deepbot.memory.session_store import SessionStore


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
    )
    client = DeepbotClientFactory.create(processor=processor)

    logger.info("Starting Deepbot (auto_reply_all=%s)", config.auto_reply_all)
    client.run(config.discord_bot_token)


if __name__ == "__main__":
    main()
