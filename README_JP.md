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

Strands Agents を使った Discord Bot です。ユーザーの全発言に自動返信します。

## 🚀 Features
- Discord メッセージを受信して Strands Agent で自動返信
- チャンネル単位の短期メモリ（直近 N ターン + TTL）
- `/reset` でセッションコンテキストをクリア
- `STRANDS_MODEL_PROVIDER` / `STRANDS_MODEL_CONFIG` でモデル切り替え
- `config/mcp.json` 経由の MCP サーバーツール連携（コンテナ既定: `/app/config/mcp.json`）
- `config/skills` 配下の Agent Skills を読み込み、`$skill名` または `/skill名` で実行
- 添付ファイルのメタ情報を会話コンテキストに含め、対応画像（`png/jpeg/gif/webp`）はモデル入力へ渡す
- 標準ツール: `file_read`, `file_write`, `editor`, `shell`, `http_request`, `environment`, `calculator`, `current_time`

## 🛠️ Setup
1. 依存関係をインストールします。
```bash
pip install -e .[dev]
```
仮想環境を作成済みでも、依存更新後は再実行してください。

2. `.env` を作成します。
```bash
cp .env.example .env
```

3. `DISCORD_BOT_TOKEN` とモデル設定を記入します。
- `STRANDS_MODEL_PROVIDER=openai` の場合は `OPENAI_API_KEY` が必須です。
- OpenAI 互換 API を使う場合は `OPENAI_BASE_URL` を指定できます（例: `https://api.openai.com/v1`）。
- モデルは `STRANDS_MODEL_CONFIG.model_id` または `OPENAI_MODEL_ID` で指定します（`STRANDS_MODEL_ID` / `MODEL_ID` も可）。
- MCP 設定ファイルは `MCP_CONFIG_PATH` で変更できます（既定: `/app/config/mcp.json`）。
- `mcp.json` の URL が `localhost` / `127.0.0.1` の場合、コンテナ内で `MCP_HOST_GATEWAY`（既定: `host.docker.internal`）へ自動変換されます。
- agent-memory の保存先は既定で `${WORKSPACE_DIR}/agent-memory/memory` です（`AGENT_MEMORY_DIR` で上書き可能）。
- コンテナ内でツールを非対話で実行するため、`STRANDS_TOOL_CONSOLE_MODE=disable` と `BYPASS_TOOL_CONSENT=true` の設定を推奨します。
- Prompt defender の環境変数:
  - `DEFENDER_ENABLED`
  - `DEFENDER_DEFAULT_MODE`
  - `DEFENDER_WARN_THRESHOLD`
  - `DEFENDER_BLOCK_THRESHOLD`
  - `DEFENDER_SANITIZE_MODE`

4. `config/AGENT.md` を編集します（system prompt に自動反映）。
5. 必要に応じて `config/skills/<skill-name>/SKILL.md` を作成します。
- `SKILL.md` は YAML frontmatter に `name` と `description` が必須です。
- 例:
  ```md
  ---
  name: reviewer
  description: ドキュメントレビュー手順
  ---
  ```

プロバイダ補足:
- `STRANDS_MODEL_PROVIDER=openai` では `openai` パッケージが必要です（このプロジェクト依存に含まれています）。

## ▶️ Run
```bash
deepbot
```

## 🐳 Docker Compose で実行
1. `.env` を作成します。
```bash
cp .env.example .env
```

2. 必要な環境変数（例: `DISCORD_BOT_TOKEN`）を設定します。
- デフォルトで `DEEPBOT_CONFIG_DIR=/app/config` を参照します。
- Compose は `./config` をコンテナへ read-only マウントします。
- Compose は `./workspace` を `/workspace` にマウントします（コンテナ出力をホスト側で参照可能）。
- `srt`（`bubblewrap`）を使うため、Compose は `cap_add: [SYS_ADMIN, NET_ADMIN]` と `seccomp/apparmor` の unconfined 設定を使用します。
- 本番運用時は `enableWeakerNestedSandbox` を `false`（デフォルト）にしてください。

3. 起動:
```bash
docker compose up -d
```

4. ログ確認:
```bash
docker compose logs -f deepbot
```

5. 停止:
```bash
docker compose down
```

Skill 実行例:
- Discord で `$reviewer このREADMEを改善して` または `/reviewer このREADMEを改善して` のように送ると、対応 Skill を実行できます。

## 🧠 Session Behavior
- DM: `dm:{user_id}`
- ギルドチャンネル: `guild:{guild_id}:channel:{channel_id}`
- スレッド: `thread:{thread_id}`

保持ポリシー:
- `SESSION_MAX_TURNS`（内部では 2 倍メッセージ数で保持）
- `SESSION_TTL_MINUTES` 経過でセッション破棄

## 🧪 Test
```bash
pytest -q
```

## 📌 Notes
- 全発言自動返信はレート制限に注意が必要です。
- Bot 自身のメッセージには返信しません。
- `config/AGENT.md` を読み込みます（見つからない場合はデフォルト prompt のみ利用）。
- 回答生成前の先行返信メッセージは `BOT_PROCESSING_MESSAGE` で設定できます（URL、`$skill`、疑問文、調査系キーワード入力時のみ送信。空文字で無効化）。
- `AUTH_PASSPHRASE` を設定すると、無通信時間 (`AUTH_IDLE_TIMEOUT_MINUTES`) 経過後は `/auth <合言葉>` が必須になります（有効期間: `AUTH_WINDOW_MINUTES`、失敗制限: `AUTH_MAX_RETRIES`、ロック時間: `AUTH_LOCK_MINUTES`）。
- セキュアデフォルトでは `http_request`, `calculator`, `current_time` のみ有効です。
- `DANGEROUS_TOOLS_ENABLED=true` で `file_read`, `file_write`, `editor`, `environment`, `shell` を有効化できます（信頼できる環境のみ）。
