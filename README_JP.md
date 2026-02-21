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
- `/cleanup` でチャンネルログを一括削除（管理者のみ実行可）
- `config/skills` の Skill を `$skill名` / `/skill名` で実行
- 画像添付（`png/jpeg/gif/webp`）をモデル入力へ転送
- JSON（`markdown`, `ui_intent.buttons`, `images`）による見せ方指定で、ボタンUIと画像Embedを返せる
- A2UI v0.9 形式の JSON（`a2ui`）で Discord Components V2 UI を返せる
- 長時間のツール実行時に軽量な進捗メッセージを返せる

A2UI の実装仕様・制約は `docs/a2ui.md` を参照してください。

## ⚡ 最短スタート（初心者向け）
1. 分割envを作成
```bash
cp .env.deepbot.example .env.deepbot
cp .env.litellm.example .env.litellm
```
2. 先にこの値を設定
- `DISCORD_BOT_TOKEN`
- `.env.deepbot: OPENAI_API_KEY`（deepbot -> litellm の内部キー）
- `.env.litellm: LITELLM_MASTER_KEY`（deepbot側と同じ値）
- `.env.litellm: OPENAI_API_KEY`（上流OpenAIキー）
- `AUTH_PASSPHRASE`（空のままだと起動エラー）
3. 起動
```bash
docker compose build deepbot
docker compose up -d
docker compose logs -f deepbot
```

## 🐳 Docker Compose 運用の前提
- リクエスト経路は `deepbot -> litellm -> OpenAI` です。
- 秘密情報は `.env.deepbot` と `.env.litellm` に分離します。
- 上流プロバイダのAPIキーは `.env.litellm` のみに置きます。
- `config/litellm.yaml` でモデル別のエイリアスを管理します（既定: `gpt-4o-mini`, `glm-4.7`）。
- コンテナはビルド済みイメージの `/app` コードで動きます（`/app` は bind mount しません）。
- `./config` は `/app/config` に read-only マウント。
- `./workspace` は `/workspace` に read-write マウント。
- `srt`（bubblewrap）を使うため、Compose は `SYS_ADMIN/NET_ADMIN` と `seccomp/apparmor=unconfined` を使います。
- `srt` のファイルシステムポリシーで `/app` は書き込み禁止のまま維持し、書き込みは `/workspace` と `/tmp` のみ許可、読み取りは禁止対象を機密パス（例: `/app/.env`, `/app/.git`, `/app/config/mcp.json`, `/app/config/AGENT.md`）に限定しています。
- コード変更後は必ず再ビルドが必要です。
```bash
docker compose build deepbot
docker compose up -d
```
- `.env.deepbot` のみ変更した場合は再ビルド不要で再作成のみ:
```bash
docker compose up -d --force-recreate deepbot
```

### コンテナ構成（認証情報の境界）
```text
Discord
  -> deepbot container
       env: .env.deepbot
       key: OPENAI_API_KEY (internal; litellm用)
  -> litellm container
       env: .env.litellm
       keys: LITELLM_MASTER_KEY, OPENAI_API_KEY, GLM_API_KEY
  -> Provider APIs
       OpenAI / GLM(OpenAI互換)
```

### envの責務
- `.env.deepbot`: Discord Bot実行に必要な設定のみ。上流プロバイダキーは置かない。
- `.env.litellm`: プロバイダ接続に必要な秘密情報のみ。
- 共有キー: `.env.deepbot` の `OPENAI_API_KEY` と `.env.litellm` の `LITELLM_MASTER_KEY` は同じ値にする。

## ⚙️ 設定ガイド
以下は「どこを触ればよいか」が分かるように分類しています。

### 1. 必須（`.env.deepbot`）
- `DISCORD_BOT_TOKEN`: Discord Bot トークン
- `OPENAI_API_KEY`: deepbot->litellm 間の内部認証キー
- `AUTH_PASSPHRASE`: `/auth` 用の合言葉
- `OPENAI_MODEL_ID`: `config/litellm.yaml` のモデルエイリアス（例: `gpt-4o-mini`, `glm-4.7`）

### 1.1 必須（`.env.litellm`）
- `LITELLM_MASTER_KEY`: deepbot -> litellm の内部キー（`.env.deepbot` の `OPENAI_API_KEY` と同じ値）
- `OPENAI_API_KEY`: 上流OpenAIキー（OpenAIルートを使う場合）
- `GLM_API_KEY`: `glm-4.7` ルートを使う場合に必須

### 2. 通常はデフォルトでOK（`.env.deepbot`）
- `SESSION_MAX_TURNS`, `SESSION_TTL_MINUTES`: 会話履歴の保持量/保持時間
- `AUTO_THREAD_ENABLED`, `AUTO_THREAD_MODE`, `AUTO_THREAD_TRIGGER_KEYWORDS`: 自動スレッド作成の有効化/モード/トリガー語
- `AUTO_THREAD_CHANNEL_IDS`, `AUTO_THREAD_ARCHIVE_MINUTES`, `AUTO_THREAD_RENAME_FROM_REPLY`: 対象チャンネル絞り込み/自動アーカイブ時間/初回返信由来のタイトル更新
- `BOT_FALLBACK_MESSAGE`: 失敗時の返信文
- `BOT_PROCESSING_MESSAGE`: 「調べます」等の先行返信
- `LOG_LEVEL`: ログレベル（通常 `INFO`）
- `DEEPBOT_TRANSCRIPT`, `DEEPBOT_TRANSCRIPT_DIR`: 監査ログJSONLの有効化/出力先

### 2.1 GLM-4.7 を使う場合
- `.env.litellm` に `GLM_API_KEY` を設定
- 必要なら `.env.litellm` の `GLM_API_BASE` を調整（既定: `https://open.bigmodel.cn/api/paas/v4`）
- `.env.deepbot` の `OPENAI_MODEL_ID=glm-4.7` に変更
- 再起動:
```bash
docker compose up -d --build
```

### 3. セキュリティ関連（重要）
- `AUTH_REQUIRED=true`: 認証を必須化（推奨）
- `AUTH_COMMAND=/auth`: 認証コマンド
- `AUTH_IDLE_TIMEOUT_MINUTES`: 無操作タイムアウト
- `AUTH_WINDOW_MINUTES`: 認証成功後の有効時間
- `AUTH_MAX_RETRIES`, `AUTH_LOCK_MINUTES`: 失敗時ロック制御
- `DEFENDER_*`: プロンプトインジェクション防御設定
- `ATTACHMENT_ALLOWED_HOSTS`: 添付画像取得先ホストの許可リスト

### 3.1 定期ジョブ設定（cron風）
- `CRON_ENABLED=true`
- `CRON_JOBS_DIR=/workspace/bot-rw/jobs`（書き込み可能なパスが必須）
- `CRON_DEFAULT_TIMEZONE=Asia/Tokyo`
- `CRON_POLL_SECONDS=15`
- `CRON_BUSY_MESSAGE`

### 3.2 定期ジョブコマンド（多言語エイリアス）
定期ジョブのコマンドは内部的にコマンドID（`job_create`, `job_list`, `job_pause`, `job_resume`, `job_delete`, `job_run_now`）へ正規化されます。  
そのため、**日本語ラベルでも英語ラベルでも同じ処理**で実行されます。

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

実行例:
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

登録時の引数キー（どちらでも可）:
- プロンプト: `プロンプト` または `prompt`
- 頻度: `頻度` または `schedule`
- タイムゾーン: `タイムゾーン` または `timezone`

現在サポートされる頻度書式:
- `毎時`
- `毎日 HH:MM`
- `平日 HH:MM`

### 4. 危険ツール設定（変更時のみ）
- `DANGEROUS_TOOLS_ENABLED=false`: 通常は `false` 推奨
- `ENABLED_DANGEROUS_TOOLS`: 有効化する危険ツールの許可リスト
- `SHELL_SRT_ENFORCED=true`: shell を `srt --settings ... -c` 形式に強制
- `SHELL_SRT_SETTINGS_PATH`: srt 設定ファイル
- `SHELL_DENY_PATH_PREFIXES`: shell で参照を禁止する絶対パス接頭辞（`/app` 全体ではなく機密パスを列挙）
- `TOOL_WRITE_ROOTS`: `file_read`/`file_write`/`editor` の許可範囲
- `WORKSPACE_DIR` / `BOT_RW_DIR`: ツール・メモリ保存先として使う推奨RWルート

危険ツールを有効化する場合の最小例:
```env
DANGEROUS_TOOLS_ENABLED=true
ENABLED_DANGEROUS_TOOLS=shell,file_read
SHELL_SRT_ENFORCED=true
WORKSPACE_DIR=/workspace/bot-rw
BOT_RW_DIR=/workspace/bot-rw
TOOL_WRITE_ROOTS=/workspace/bot-rw,/tmp
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

## 🧹 `/cleanup` コマンド
- 実行方法: サーバー内のテキストチャンネルで `/cleanup` を送信。
- 実行者の条件: `Administrator` 権限を持つユーザーのみ実行可能。
- 対象チャンネルで Bot に必要な権限:
  - `View Channel`（チャンネルを見る）
  - `Read Message History`（メッセージ履歴を読む）
  - `Manage Messages`（メッセージの管理）
- 古いメッセージ混在で bulk 削除が失敗した場合、deepbot は non-bulk 削除に自動フォールバックします。

## 📌 トラブル時の確認
1. 起動しない: `AUTH_PASSPHRASE` が空でないか確認
2. `.env.deepbot` 変更が反映されない: `docker compose up -d --force-recreate deepbot` を実行
3. Pythonコード変更が反映されない: `docker compose build deepbot && docker compose up -d deepbot`
4. ツールが動かない: `DANGEROUS_TOOLS_ENABLED` と `ENABLED_DANGEROUS_TOOLS` を確認
5. shell が拒否される: `srt --settings ... -c` 形式になっているか確認
6. `openai.OpenAIError: api_key must be set`: `.env.deepbot` の `OPENAI_API_KEY` が空
7. `model=... Invalid model name`: `OPENAI_MODEL_ID` と `config/litellm.yaml` のエイリアス不一致
8. GLM接続できない: `.env.litellm` の変数名は `GLM_API_BASE`（`GLM_BASE_URL` ではない）
