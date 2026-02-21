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

Discord bot built with Strands Agents. It replies automatically to user messages.

## 🚀 What It Does
- Auto-reply in Discord channels/threads
- Per-user short-term memory per channel/thread
- `/reset` command to clear session context
- Skill execution from `config/skills` via `$skill_name` or `/skill_name`
- Image attachment forwarding (`png/jpeg/gif/webp`) to model input
- Prompt-driven rich replies via JSON (`markdown`, `ui_intent.buttons`, `images`) for buttons and image embeds
- A2UI v0.9-style envelopes via JSON (`a2ui`) with Discord Components V2 rendering
- Sends lightweight progress updates during long tool-based responses

Developer notes for A2UI behavior and renderer constraints are documented in `docs/a2ui.md`.

## ⚡ Quick Start (Beginner)
1. Create split env files
```bash
cp .env.deepbot.example .env.deepbot
cp .env.litellm.example .env.litellm
```
2. Set these values first
- `DISCORD_BOT_TOKEN`
- `.env.deepbot: OPENAI_API_KEY` (internal key for deepbot -> litellm)
- `.env.litellm: LITELLM_MASTER_KEY` (must match deepbot internal key)
- `.env.litellm: OPENAI_API_KEY` (upstream OpenAI key)
- `AUTH_PASSPHRASE` (must not be empty)
3. Build and run
```bash
docker compose build deepbot
docker compose up -d
docker compose logs -f deepbot
```

## 🐳 Docker Compose Behavior
- Request flow is `deepbot -> litellm -> OpenAI`.
- Secrets are split by service: `.env.deepbot` and `.env.litellm`.
- Upstream provider keys are only in `.env.litellm`.
- `config/litellm.yaml` defines model aliases. Default aliases: `gpt-4o-mini`, `glm-4.7`.
- Runs code from built image (`/app` is not bind-mounted).
- Mounts `./config` to `/app/config` as read-only.
- Mounts `./workspace` to `/workspace` as read-write.
- Uses `SYS_ADMIN/NET_ADMIN` and unconfined `seccomp/apparmor` for `srt` (`bubblewrap`).
- `srt` filesystem policy blocks read/write access to `/app` and only allows writes under `/workspace` and `/tmp`.
- Rebuild after code changes:
```bash
docker compose build deepbot
docker compose up -d
```
- Apply `.env.deepbot` changes without code rebuild:
```bash
docker compose up -d --force-recreate deepbot
```

### Container Topology (Secret Boundaries)
```text
Discord
  -> deepbot container
       env: .env.deepbot
       key: OPENAI_API_KEY (internal key for litellm)
  -> litellm container
       env: .env.litellm
       keys: LITELLM_MASTER_KEY, OPENAI_API_KEY, GLM_API_KEY
  -> Provider APIs
       OpenAI / GLM (OpenAI-compatible endpoint)
```

### Env ownership
- `.env.deepbot`: only bot runtime settings; do not place upstream provider keys here.
- `.env.litellm`: provider credentials and litellm routing secrets.
- Shared internal key: `.env.deepbot` `OPENAI_API_KEY` must equal `.env.litellm` `LITELLM_MASTER_KEY`.

## ⚙️ Configuration Guide
Use split env files to enforce least privilege.

### 1. Required (`.env.deepbot`)
- `DISCORD_BOT_TOKEN`: Discord bot token
- `OPENAI_API_KEY`: internal proxy key between deepbot and litellm
- `AUTH_PASSPHRASE`: passphrase for `/auth`
- `OPENAI_MODEL_ID`: model alias in `config/litellm.yaml` (for example `gpt-4o-mini` or `glm-4.7`)

### 1.1 Required (`.env.litellm`)
- `LITELLM_MASTER_KEY`: internal key for deepbot -> litellm (must equal `.env.deepbot` `OPENAI_API_KEY`)
- `OPENAI_API_KEY`: upstream OpenAI key (optional if you only use non-OpenAI routes)
- `GLM_API_KEY`: required when using `glm-4.7`

### 2. Usually Keep Defaults (`.env.deepbot`)
- `SESSION_MAX_TURNS`, `SESSION_TTL_MINUTES`
- `AUTO_THREAD_ENABLED`, `AUTO_THREAD_MODE`, `AUTO_THREAD_TRIGGER_KEYWORDS`
- `AUTO_THREAD_CHANNEL_IDS`, `AUTO_THREAD_ARCHIVE_MINUTES`, `AUTO_THREAD_RENAME_FROM_REPLY`
- `BOT_FALLBACK_MESSAGE`
- `BOT_PROCESSING_MESSAGE`
- `LOG_LEVEL`
- `DEEPBOT_TRANSCRIPT`, `DEEPBOT_TRANSCRIPT_DIR`

### 2.1 Use GLM-4.7 via LiteLLM
- Set `GLM_API_KEY` in `.env.litellm`
- Keep or adjust `GLM_API_BASE` in `.env.litellm` (default: `https://open.bigmodel.cn/api/paas/v4`)
- Set `OPENAI_MODEL_ID=glm-4.7` in `.env.deepbot`
- Restart:
```bash
docker compose up -d --build
```

### 3. Security Controls (Important)
- `AUTH_REQUIRED=true`
- `AUTH_COMMAND`
- `AUTH_IDLE_TIMEOUT_MINUTES`
- `AUTH_WINDOW_MINUTES`
- `AUTH_MAX_RETRIES`, `AUTH_LOCK_MINUTES`
- `DEFENDER_*` (prompt-injection defense)
- `ATTACHMENT_ALLOWED_HOSTS` (remote attachment allowlist)

### 3.1 Scheduled Jobs (Cron-like)
- `CRON_ENABLED=true`
- `CRON_JOBS_DIR=/workspace/jobs` (must be writable)
- `CRON_DEFAULT_TIMEZONE=Asia/Tokyo`
- `CRON_POLL_SECONDS=15`
- `CRON_BUSY_MESSAGE`

### 3.2 Scheduled Job Commands (Multilingual Aliases)
Scheduled job commands are normalized to internal command IDs (`job_create`, `job_list`, `job_pause`, `job_resume`, `job_delete`, `job_run_now`).
This means users can operate jobs with either Japanese or English command labels.

- `job_create`
  - `/定期登録`
  - `/schedule`, `/job-create`, `/cron-register`, `/schedule register`
- `job_list`
  - `/定期一覧`
  - `/schedule-list`, `/job-list`, `/cron-list`, `/schedule list`
- `job_pause`
  - `/定期停止 <job-id>`
  - `/schedule-pause <job-id>`, `/job-pause <job-id>`, `/cron-pause <job-id>`, `/schedule pause <job-id>`
- `job_resume`
  - `/定期再開 <job-id>`
  - `/schedule-resume <job-id>`, `/job-resume <job-id>`, `/cron-resume <job-id>`, `/schedule resume <job-id>`
- `job_delete`
  - `/定期削除 <job-id>`
  - `/schedule-delete <job-id>`, `/job-delete <job-id>`, `/cron-delete <job-id>`, `/schedule delete <job-id>`
- `job_run_now`
  - `/定期今すぐ実行 <job-id>`
  - `/schedule-run-now <job-id>`, `/job-run-now <job-id>`, `/cron-run-now <job-id>`, `/schedule run-now <job-id>`

Examples:
```text
/定期登録 プロンプト="今日の天気をまとめて" 頻度="平日 7:00"
/schedule prompt="Post weather summary" schedule="毎時"
/定期一覧
/schedule list
/定期停止 job-20260221-070000
/schedule resume job-20260221-070000
/schedule delete job-20260221-070000
/schedule run-now job-20260221-070000
```

Registration argument keys (both supported):
- Prompt key: `プロンプト` or `prompt`
- Schedule key: `頻度` or `schedule`
- Timezone key: `タイムゾーン` or `timezone`

Current supported schedule text:
- `毎時`
- `毎日 HH:MM`
- `平日 HH:MM`

### 4. Dangerous Tool Controls (Advanced)
- `DANGEROUS_TOOLS_ENABLED=false` (recommended default)
- `ENABLED_DANGEROUS_TOOLS`
- `SHELL_SRT_ENFORCED=true`
- `SHELL_SRT_SETTINGS_PATH`
- `SHELL_DENY_PATH_PREFIXES=/app`
- `TOOL_WRITE_ROOTS` (applies to `file_read` / `file_write` / `editor`)

Minimal example for dangerous tools:
```env
DANGEROUS_TOOLS_ENABLED=true
ENABLED_DANGEROUS_TOOLS=shell,file_read
SHELL_SRT_ENFORCED=true
SHELL_DENY_PATH_PREFIXES=/app
TOOL_WRITE_ROOTS=/workspace
```

## 🧩 Related Files
- `config/AGENT.md`: system prompt content
- `config/mcp.json`: MCP server config
- `config/skills/<name>/SKILL.md`: custom skills

Minimal `SKILL.md`:
```md
---
name: reviewer
description: Document review workflow
---
```

## 🧠 Session IDs
- DM: `dm:{user_id}`
- Guild channel: `guild:{guild_id}:channel:{channel_id}:user:{user_id}`
- Thread: `thread:{thread_id}:user:{user_id}`

## 🧪 Test
```bash
pytest -q
```

## 📌 Troubleshooting
1. Startup error: check `AUTH_PASSPHRASE` is not empty
2. `.env.deepbot` changes not applied: run `docker compose up -d --force-recreate deepbot`
3. Python code changes not applied: run `docker compose build deepbot && docker compose up -d deepbot`
4. Tool unavailable: check `DANGEROUS_TOOLS_ENABLED` and `ENABLED_DANGEROUS_TOOLS`
5. Shell rejected: use `srt --settings ... -c "<command>"`
6. `openai.OpenAIError: api_key must be set`: `.env.deepbot` `OPENAI_API_KEY` is empty
7. `Invalid model name`: `OPENAI_MODEL_ID` does not match an alias in `config/litellm.yaml`
8. GLM route fails: use `GLM_API_BASE` (not `GLM_BASE_URL`) in `.env.litellm`
