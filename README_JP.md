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

Strands Agents を使った Discord Bot です。ユーザーの発言に自動返信します。

## 🚀 できること
- Discord メッセージに自動返信
- ユーザー単位の短期メモリ（チャンネル/スレッドごと）
- `/reset` で会話コンテキストを初期化
- `config/skills` の Skill を `$skill名` / `/skill名` で実行
- 画像添付（`png/jpeg/gif/webp`）をモデル入力へ転送
- JSON（`markdown`, `ui_intent.buttons`, `images`）による見せ方指定で、ボタンUIと画像Embedを返せる

## ⚡ 最短スタート（初心者向け）
1. `.env` を作成
```bash
cp .env.example .env
```
2. `.env` のこの3つだけ先に設定
- `DISCORD_BOT_TOKEN`
- `OPENAI_API_KEY`
- `AUTH_PASSPHRASE`（空のままだと起動エラー）
3. 起動
```bash
docker compose build deepbot
docker compose up -d
docker compose logs -f deepbot
```

## 🐳 Docker Compose 運用の前提
- コンテナはビルド済みイメージの `/app` コードで動きます（`/app` は bind mount しません）。
- `./config` は `/app/config` に read-only マウント。
- `./workspace` は `/workspace` に read-write マウント。
- `srt`（bubblewrap）を使うため、Compose は `SYS_ADMIN/NET_ADMIN` と `seccomp/apparmor=unconfined` を使います。
- `srt` のファイルシステムポリシーで `/app` の read/write を禁止し、書き込みは `/workspace` と `/tmp` のみ許可しています。
- コード変更後は必ず再ビルドが必要です。
```bash
docker compose build deepbot
docker compose up -d
```

## ⚙️ 設定ガイド（.env）
以下は「どこを触ればよいか」が分かるように分類しています。

### 1. 必須（まずここだけ）
- `DISCORD_BOT_TOKEN`: Discord Bot トークン
- `OPENAI_API_KEY`: OpenAI API キー
- `AUTH_PASSPHRASE`: `/auth` 用の合言葉

### 2. 通常はデフォルトでOK
- `OPENAI_MODEL_ID`: 使用モデル（例: `gpt-4o-mini`）
- `SESSION_MAX_TURNS`, `SESSION_TTL_MINUTES`: 会話履歴の保持量/保持時間
- `BOT_FALLBACK_MESSAGE`: 失敗時の返信文
- `BOT_PROCESSING_MESSAGE`: 「調べます」等の先行返信
- `LOG_LEVEL`: ログレベル（通常 `INFO`）

### 3. セキュリティ関連（重要）
- `AUTH_REQUIRED=true`: 認証を必須化（推奨）
- `AUTH_COMMAND=/auth`: 認証コマンド
- `AUTH_IDLE_TIMEOUT_MINUTES`: 無操作タイムアウト
- `AUTH_WINDOW_MINUTES`: 認証成功後の有効時間
- `AUTH_MAX_RETRIES`, `AUTH_LOCK_MINUTES`: 失敗時ロック制御
- `DEFENDER_*`: プロンプトインジェクション防御設定
- `ATTACHMENT_ALLOWED_HOSTS`: 添付画像取得先ホストの許可リスト

### 4. 危険ツール設定（変更時のみ）
- `DANGEROUS_TOOLS_ENABLED=false`: 通常は `false` 推奨
- `ENABLED_DANGEROUS_TOOLS`: 有効化する危険ツールの許可リスト
- `SHELL_SRT_ENFORCED=true`: shell を `srt --settings ... -c` 形式に強制
- `SHELL_SRT_SETTINGS_PATH`: srt 設定ファイル
- `SHELL_DENY_PATH_PREFIXES=/app`: shell で参照を禁止する絶対パス接頭辞
- `TOOL_WRITE_ROOTS=/workspace`: `file_read`/`file_write`/`editor` の許可範囲

危険ツールを有効化する場合の最小例:
```env
DANGEROUS_TOOLS_ENABLED=true
ENABLED_DANGEROUS_TOOLS=shell,file_read
SHELL_SRT_ENFORCED=true
TOOL_WRITE_ROOTS=/workspace
```

## 🧩 補助ファイル
- `config/AGENT.md`: システムプロンプト（起動時に読み込み）
- `config/mcp.json`: MCP サーバー設定
- `config/skills/<name>/SKILL.md`: 追加 Skill 定義

`SKILL.md` 最小例:
```md
---
name: reviewer
description: ドキュメントレビュー手順
---
```

## 🧠 セッション仕様
- DM: `dm:{user_id}`
- ギルド: `guild:{guild_id}:channel:{channel_id}:user:{user_id}`
- スレッド: `thread:{thread_id}:user:{user_id}`

## 🧪 テスト
```bash
pytest -q
```

## 📌 トラブル時の確認
1. 起動しない: `AUTH_PASSPHRASE` が空でないか確認
2. 設定を変えたのに反映されない: `docker compose build deepbot` を実行
3. ツールが動かない: `DANGEROUS_TOOLS_ENABLED` と `ENABLED_DANGEROUS_TOOLS` を確認
4. shell が拒否される: `srt --settings ... -c` 形式になっているか確認
