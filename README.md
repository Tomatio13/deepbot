<h1 align="center">deepbot</h1>

<p align="center">
  <a href="README_JP.md"><img src="https://img.shields.io/badge/ドキュメント-日本語-white.svg" alt="JA doc"/></a>
  <a href="README.md"><img src="https://img.shields.io/badge/english-document-white.svg" alt="EN doc"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord&logoColor=white" alt="Discord">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/Test-pytest-0A9EDC?logo=pytest&logoColor=white" alt="pytest">
</p>

Discord bot built with Strands Agents. It replies to every user message automatically.

## 🚀 Features
- Automatic replies to Discord messages via Strands Agent
- Channel-level short-term memory (last N turns + TTL)
- `/reset` command to clear session context
- Model switching with `STRANDS_MODEL_PROVIDER` / `STRANDS_MODEL_CONFIG`
- MCP server tool integration via `config/mcp.json` (container default: `/app/config/mcp.json`)
- Loads Agent Skills from `config/skills` and runs them with `$skill_name`
- Standard tools: `file_read`, `file_write`, `editor`, `shell`, `http_request`, `environment`, `calculator`, `current_time`

## 🛠️ Setup
1. Install dependencies.
```bash
pip install -e .[dev]
```
Run again after dependency updates, even if your virtual environment already exists.

2. Create `.env`.
```bash
cp .env.example .env
```

3. Set `DISCORD_BOT_TOKEN` and model settings.
- If `STRANDS_MODEL_PROVIDER=openai`, `OPENAI_API_KEY` is required.
- For OpenAI-compatible APIs, you can set `OPENAI_BASE_URL` (for example: `https://api.openai.com/v1`).
- Model can be set with `STRANDS_MODEL_CONFIG.model_id` or `OPENAI_MODEL_ID` (`STRANDS_MODEL_ID` / `MODEL_ID` are also accepted).
- MCP config path can be changed with `MCP_CONFIG_PATH` (default: `/app/config/mcp.json`).
- If a URL in `mcp.json` uses `localhost` / `127.0.0.1`, it is automatically converted to `MCP_HOST_GATEWAY` (default: `host.docker.internal`) in containers.

4. Edit `config/AGENT.md` (auto-reflected as system prompt).
5. Add `config/skills/<skill-name>/SKILL.md` if needed.
- `SKILL.md` requires `name` and `description` in YAML frontmatter.
- Example:
  ```md
  ---
  name: reviewer
  description: Document review workflow
  ---
  ```

Provider note:
- When `STRANDS_MODEL_PROVIDER=openai`, the `openai` package is required (already included in project dependencies).

## ▶️ Run
```bash
deepbot
```

## 🐳 Run with Docker Compose
1. Create `.env`.
```bash
cp .env.example .env
```

2. Set required environment variables (for example `DISCORD_BOT_TOKEN`).
- Default config directory is `DEEPBOT_CONFIG_DIR=/app/config`.
- Compose mounts `./config` to the container as read-only.
- Compose mounts `./workspace` to `/workspace` (you can inspect container outputs from host).

3. Start:
```bash
docker compose up -d
```

4. Check logs:
```bash
docker compose logs -f deepbot
```

5. Stop:
```bash
docker compose down
```

Skill usage example:
- Send `$reviewer Improve this README` in Discord to run the matching skill.

## 🧠 Session Behavior
- DM: `dm:{user_id}`
- Guild channel: `guild:{guild_id}:channel:{channel_id}`
- Thread: `thread:{thread_id}`

Retention policy:
- `SESSION_MAX_TURNS` (internally retains twice the message count)
- Session is discarded after `SESSION_TTL_MINUTES`

## 🧪 Test
```bash
pytest -q
```

## 📌 Notes
- Auto-reply to every message may hit rate limits.
- The bot does not reply to its own messages.
- It loads `config/AGENT.md` (falls back to default prompt if missing).
- In secure default mode, only `http_request`, `calculator`, and `current_time` are enabled.
- Set `DANGEROUS_TOOLS_ENABLED=true` to enable `file_read`, `file_write`, `editor`, `environment`, and `shell` (trusted environments only).
