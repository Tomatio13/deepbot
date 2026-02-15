from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from deepbot.config import AppConfig, ConfigError, RuntimeSettings
from deepbot.mcp_tools import load_mcp_tool_providers
from deepbot.skills import (
    build_selected_skill_prompt,
    build_skills_discovery_prompt,
    extract_selected_skill,
    list_skills,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class AgentRequest:
    session_id: str
    context: list[dict[str, str]]
    image_attachments: tuple["ImageAttachment", ...] = ()


@dataclass(frozen=True)
class ImageAttachment:
    format: str
    data: bytes


class AgentRuntime:
    def __init__(self, *, agent_callable: Callable[[str], Any], timeout_seconds: int) -> None:
        self._agent_callable = agent_callable
        self._timeout_seconds = timeout_seconds
        # Strands Agent does not support concurrent invocations.
        self._call_lock = asyncio.Lock()

    async def generate_reply(self, request: AgentRequest) -> str:
        model_input = self._build_model_input(request)
        fallback_prompt = self._build_prompt(request)
        async with self._call_lock:
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(self._agent_callable, model_input),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                if request.image_attachments and self._should_retry_without_images(exc):
                    logger.warning(
                        "Image input rejected by model provider. Retrying without images. session_id=%s",
                        request.session_id,
                    )
                    response = await asyncio.wait_for(
                        asyncio.to_thread(self._agent_callable, fallback_prompt),
                        timeout=self._timeout_seconds,
                    )
                else:
                    raise
        return str(response).strip()

    @staticmethod
    def _should_retry_without_images(exc: Exception) -> bool:
        text = str(exc).lower()
        if "invalid api parameter" in text:
            return True
        if "unsupported" in text and "image" in text:
            return True
        if "invalid" in text and "image" in text:
            return True
        return False

    @classmethod
    def _build_model_input(cls, request: AgentRequest) -> str | list[dict[str, Any]]:
        prompt = cls._build_prompt(request)
        if not request.image_attachments:
            return prompt

        content_blocks: list[dict[str, Any]] = [{"text": prompt}]
        for attachment in request.image_attachments:
            content_blocks.append(
                {
                    "image": {
                        "format": attachment.format,
                        "source": {"bytes": attachment.data},
                    }
                }
            )
        return [{"role": "user", "content": content_blocks}]

    @staticmethod
    def _build_prompt(request: AgentRequest) -> str:
        context = [dict(m) for m in request.context]
        skills = list_skills()
        selected_skill_prompt: str | None = None
        skills_discovery_prompt = build_skills_discovery_prompt(skills)

        if context and context[-1].get("role") == "user":
            original_content = context[-1].get("content", "")
            selected_skill, cleaned_content = extract_selected_skill(original_content, skills)
            if selected_skill is not None:
                selected_skill_prompt = build_selected_skill_prompt(selected_skill)
                context[-1]["content"] = cleaned_content

        lines = [
            "You are a Discord assistant.",
            "When the user asks for web facts, latest info, URLs, or verification, you must use the http_request tool.",
            "Prefer tool-based answers over memory for time-sensitive topics.",
            f"Session ID: {request.session_id}",
        ]
        if skills_discovery_prompt:
            lines.extend(["", skills_discovery_prompt])
        if selected_skill_prompt:
            lines.extend(["", selected_skill_prompt])
        lines.extend([
            "",
            "Conversation history:",
        ]
        )
        for message in context:
            role = message.get("role", "user")
            content = str(message.get("content", "")).strip()
            if not content:
                logger.warning(
                    "Skipped empty context message while building prompt. session_id=%s role=%s",
                    request.session_id,
                    role,
                )
                continue
            lines.append(f"[{role}] {content}")
        lines.append("Reply in Japanese, concise and helpful.")
        return "\n".join(lines)


def _patch_openai_image_content_formatter(model_cls: type[Any]) -> None:
    if getattr(model_cls, "_deepbot_image_url_patch_applied", False):
        return

    original_attr = model_cls.__dict__.get("format_request_message_content")
    original_func: Callable[..., Any]
    if isinstance(original_attr, classmethod):
        original_func = original_attr.__func__
    else:
        original_func = getattr(model_cls, "format_request_message_content")

    def patched(cls: type[Any], content: Any, **kwargs: Any) -> dict[str, Any]:
        formatted = original_func(cls, content, **kwargs)
        if isinstance(formatted, dict) and formatted.get("type") == "image_url":
            image_url = formatted.get("image_url")
            if isinstance(image_url, dict):
                image_url.pop("format", None)
                image_url.pop("detail", None)
        return formatted

    setattr(model_cls, "format_request_message_content", classmethod(patched))
    setattr(model_cls, "_deepbot_image_url_patch_applied", True)


def _load_model(config: AppConfig) -> Any | None:
    provider = config.strands_model_provider
    model_config = dict(config.strands_model_config)

    if not provider:
        return None

    model_imports = {
        "openai": ("strands.models.openai", "OpenAIModel"),
        "bedrock": ("strands.models.bedrock", "BedrockModel"),
        "anthropic": ("strands.models.anthropic", "AnthropicModel"),
        "ollama": ("strands.models.ollama", "OllamaModel"),
        "gemini": ("strands.models.gemini", "GeminiModel"),
    }
    import_path = model_imports.get(provider)
    if import_path is None:
        supported = ", ".join(sorted(model_imports.keys()))
        raise ConfigError(f"Unsupported STRANDS_MODEL_PROVIDER '{provider}'. Supported: {supported}")

    module_name, class_name = import_path
    try:
        module = __import__(module_name, fromlist=[class_name])
        model_cls = getattr(module, class_name)
    except Exception as exc:  # pragma: no cover
        raise ConfigError(
            f"Failed to import model provider '{provider}'. "
            f"Install required dependency for {module_name}: {exc}"
        ) from exc

    if provider == "openai":
        _patch_openai_image_content_formatter(model_cls)
        if config.openai_base_url:
            client_args = model_config.get("client_args")
            if client_args is None:
                model_config["client_args"] = {"base_url": config.openai_base_url}
            elif isinstance(client_args, dict):
                model_config["client_args"] = dict(client_args)
                model_config["client_args"].setdefault("base_url", config.openai_base_url)
            else:
                raise ConfigError("STRANDS_MODEL_CONFIG.client_args must be an object")

    try:
        return model_cls(**model_config)
    except TypeError as exc:
        pretty_config = json.dumps(model_config, ensure_ascii=False)
        raise ConfigError(
            f"Invalid STRANDS_MODEL_CONFIG for provider '{provider}': {pretty_config} ({exc})"
        ) from exc


def _load_default_tools(config: AppConfig) -> list[Any]:
    # Safe-by-default: only tools with lower impact are enabled.
    tool_specs = [
        ("strands_tools", "http_request"),
        ("strands_tools", "calculator"),
        ("strands_tools", "current_time"),
    ]
    if config.dangerous_tools_enabled:
        tool_specs.extend(
            [
                ("strands_tools", "file_read"),
                ("strands_tools", "file_write"),
                ("strands_tools", "editor"),
                ("strands_tools", "environment"),
                ("strands_tools", "shell"),
            ]
        )
    else:
        logger.info(
            "Dangerous tools disabled. Set DANGEROUS_TOOLS_ENABLED=true only in trusted environments."
        )

    loaded_tools: list[Any] = []
    for module_name, tool_name in tool_specs:
        try:
            module = __import__(module_name, fromlist=[tool_name])
            loaded_tools.append(getattr(module, tool_name))
        except Exception as exc:
            logger.warning("Tool unavailable: %s.%s (%s)", module_name, tool_name, exc)

    logger.info("Loaded Strands tools: %s", ", ".join(getattr(t, "__name__", str(t)) for t in loaded_tools))

    mcp_providers = load_mcp_tool_providers()
    if mcp_providers:
        loaded_tools.extend(mcp_providers)
        logger.info("Loaded MCP tool providers: %s", ", ".join(getattr(p, "prefix", str(p)) for p in mcp_providers))

    return loaded_tools


def _load_agent_md(agent_md_path: Path) -> str:
    if not agent_md_path.exists():
        logger.info("AGENT.md not found at %s. Continuing with default system prompt.", agent_md_path)
        return ""
    if not agent_md_path.is_file():
        logger.warning("AGENT.md path is not a file: %s", agent_md_path)
        return ""
    try:
        content = agent_md_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Failed to read AGENT.md (%s): %s", agent_md_path, exc)
        return ""
    if content:
        logger.info("Loaded AGENT.md from %s", agent_md_path)
    return content


def _build_system_prompt(config: AppConfig) -> str:
    base_prompt = (
        "You are Deepbot, a Discord assistant. "
        "Use tools proactively. "
        "If a request involves web content, links, latest information, or fact checking, "
        "call http_request and answer based on fetched results. "
        "If tool access fails, state the failure briefly and suggest a retry. "
        "When using shell, always run a single non-interactive command and never start an interactive shell. "
        "For shell, always provide a concrete command string; do not call shell with empty input. "
        "For file_read/file_write/editor, always use absolute paths."
    )
    agent_md = _load_agent_md(config.agent_md_path)
    if not agent_md:
        return base_prompt
    return f"{base_prompt}\n\n<agent_md>\n{agent_md}\n</agent_md>"


def create_runtime(config: AppConfig, settings: RuntimeSettings) -> AgentRuntime:
    try:
        from strands import Agent
    except Exception as exc:  # pragma: no cover
        raise ConfigError(f"Failed to import Strands Agent: {exc}") from exc

    model = _load_model(config)
    tools = _load_default_tools(config)

    agent_kwargs: dict[str, Any] = {
        "tools": tools,
        "system_prompt": _build_system_prompt(config),
    }
    if model is not None:
        agent_kwargs["model"] = model

    agent = Agent(**agent_kwargs)
    return AgentRuntime(agent_callable=agent, timeout_seconds=settings.timeout_seconds)
