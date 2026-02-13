from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

DEFAULT_MCP_CONFIG_PATH = Path("/app/config/mcp.json")

try:
    from strands.tools.mcp import MCPClient
except Exception:  # pragma: no cover
    MCPClient = None  # type: ignore[assignment]

try:
    from strands.tools.registry import ToolProvider
except Exception:  # pragma: no cover
    class ToolProvider:  # type: ignore[no-redef]
        pass

try:
    from mcp import StdioServerParameters, stdio_client
except Exception:  # pragma: no cover
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]

try:
    from mcp.client.sse import sse_client
except Exception:  # pragma: no cover
    sse_client = None  # type: ignore[assignment]

try:
    from mcp.client.streamable_http import streamablehttp_client
except Exception:  # pragma: no cover
    streamablehttp_client = None  # type: ignore[assignment]


class SafeMCPClient(ToolProvider):
    """Fail-soft wrapper so broken MCP servers do not break agent startup."""

    def __init__(self, inner: Any, *, name: str) -> None:
        self._inner = inner
        self._name = name

    @property
    def prefix(self) -> str:
        return getattr(self._inner, "prefix", self._name)

    async def load_tools(self, **kwargs: Any) -> list[Any]:
        try:
            return await self._inner.load_tools(**kwargs)
        except Exception as exc:
            logger.warning("MCP server '%s' failed to load tools: %s", self._name, exc)
            return []

    def add_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        try:
            self._inner.add_consumer(consumer_id, **kwargs)
        except Exception:
            return None

    def remove_consumer(self, consumer_id: Any, **kwargs: Any) -> None:
        try:
            self._inner.remove_consumer(consumer_id, **kwargs)
        except Exception:
            return None

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


def _resolve_mcp_config_path() -> Path:
    raw = os.environ.get("MCP_CONFIG_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_MCP_CONFIG_PATH


def _load_mcp_servers(config_path: Path) -> dict[str, dict[str, Any]]:
    if not config_path.exists():
        return {}
    if not config_path.is_file():
        logger.warning("MCP config path is not a file: %s", config_path)
        return {}

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse MCP config (%s): %s", config_path, exc)
        return {}
    except Exception as exc:
        logger.warning("Failed to read MCP config (%s): %s", config_path, exc)
        return {}

    servers = payload.get("mcpServers", {})
    if not isinstance(servers, dict):
        logger.warning("Invalid MCP config format (%s): mcpServers must be an object", config_path)
        return {}
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)}


def _create_mcp_client(server_name: str, server_config: dict[str, Any]) -> Any | None:
    if MCPClient is None:
        logger.warning("strands.tools.mcp is unavailable. MCP server '%s' is skipped.", server_name)
        return None

    if server_config.get("disabled", False):
        logger.info("Skipping disabled MCP server: %s", server_name)
        return None

    if "url" in server_config:
        url = _normalize_mcp_url(str(server_config["url"]))
        if "/sse" in url or url.endswith("/sse"):
            if sse_client is None:
                logger.warning("SSE client is unavailable. MCP server '%s' is skipped.", server_name)
                return None
            return MCPClient(lambda: sse_client(url), prefix=server_name)

        if streamablehttp_client is None:
            logger.warning("Streamable HTTP client is unavailable. MCP server '%s' is skipped.", server_name)
            return None
        headers = server_config.get("headers")
        if headers is not None and not isinstance(headers, dict):
            logger.warning("Invalid headers in MCP server '%s'. Expected object.", server_name)
            return None
        return MCPClient(
            lambda: streamablehttp_client(url, headers=headers if headers else None),
            prefix=server_name,
        )

    if "command" in server_config:
        if StdioServerParameters is None or stdio_client is None:
            logger.warning("stdio MCP client is unavailable. MCP server '%s' is skipped.", server_name)
            return None
        command = str(server_config["command"])
        args = server_config.get("args", [])
        env = server_config.get("env", {})
        if not isinstance(args, list):
            logger.warning("Invalid args in MCP server '%s'. Expected array.", server_name)
            return None
        if not isinstance(env, dict):
            logger.warning("Invalid env in MCP server '%s'. Expected object.", server_name)
            return None

        merged_env = os.environ.copy()
        merged_env.update({str(k): str(v) for k, v in env.items()})
        params = StdioServerParameters(
            command=command,
            args=[str(a) for a in args],
            env=merged_env,
        )
        return MCPClient(lambda: stdio_client(params), prefix=server_name)

    logger.warning(
        "Invalid MCP server config for '%s'. Require either 'url' or 'command'.",
        server_name,
    )
    return None


def _normalize_mcp_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if host not in {"localhost", "127.0.0.1"}:
        return url

    # In containers localhost points to itself; route to host gateway instead.
    replacement_host = os.environ.get("MCP_HOST_GATEWAY", "host.docker.internal").strip() or "host.docker.internal"
    if parsed.port is not None:
        netloc = f"{replacement_host}:{parsed.port}"
    else:
        netloc = replacement_host
    return urlunparse(parsed._replace(netloc=netloc))


def load_mcp_tool_providers() -> list[Any]:
    config_path = _resolve_mcp_config_path()
    servers = _load_mcp_servers(config_path)
    if not servers:
        logger.info("No MCP servers configured at %s", config_path)
        return []

    providers: list[Any] = []
    for server_name, server_config in servers.items():
        try:
            client = _create_mcp_client(server_name, server_config)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to create MCP server '%s': %s", server_name, exc)
            continue
        if client is None:
            continue
        providers.append(SafeMCPClient(client, name=server_name))
        logger.info("Loaded MCP server: %s", server_name)
    return providers
