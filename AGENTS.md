# Repository Guidelines

## Project Structure & Module Organization
Core code lives under `src/deepbot/` and is split by responsibility:
- `agent/`: runtime and prompt orchestration
- `gateway/`: Discord client and message processing
- `memory/`: session storage and TTL handling
- top-level modules like `config.py`, `skills.py`, and `mcp_tools.py` for app configuration and integrations

Tests are in `tests/` and follow module behavior (for example, `tests/test_agent_runtime.py`). Runtime configuration is in `config/` (`config/AGENT.md` for system prompt text). Container-related files are `Dockerfile` and `compose.yaml`. Use `workspace/` for mounted runtime outputs in Docker.

## Build, Test, and Development Commands
- `pip install -e .[dev]`: install package and dev dependencies in editable mode.
- `deepbot`: start the bot locally (requires `.env` with `DISCORD_BOT_TOKEN` and model settings).
- `pytest -q`: run the full test suite.
- `docker compose up -d`: run the bot in background container mode.
- `docker compose logs -f deepbot`: stream bot logs.
- `docker compose down`: stop and remove containers.

## Coding Style & Naming Conventions
Target Python `3.11+` with PEP 8 conventions:
- 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes.
- Prefer explicit typing (`list[dict[str, str]]`, dataclasses) and small focused functions.
- Keep imports grouped: standard library, third-party, local modules.
- Write comments only for non-obvious intent, not for restating code.

## Testing Guidelines
Use `pytest` with `pytest-asyncio` (`asyncio_mode=auto` in `pyproject.toml`).
- Name files `test_*.py` and functions `test_*`.
- Cover behavior, failure paths, and timeout/retry boundaries.
- For async paths, mark with `@pytest.mark.asyncio`.
- Run `pytest -q` before opening a PR.

## Commit & Pull Request Guidelines
Git history shows Conventional Commit usage (for example, `feat: improve runtime reliability and tool execution`). Follow:
- Commit format: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Keep commits atomic and scoped to one change.
- PRs should include purpose, key changes, test evidence (`pytest -q` output), and linked issue/ticket.
- Include screenshots/log snippets when behavior changes are user-visible (Discord responses, container logs).

## Security & Configuration Tips
- Do not commit secrets; keep tokens/API keys in `.env`.
- Treat `DANGEROUS_TOOLS_ENABLED=true` as trusted-environment only.
- Validate changes to `config/mcp.json` and skills under `config/skills/` to avoid unsafe tool exposure.
