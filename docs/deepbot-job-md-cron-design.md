---
name: deepbot-job-md-cron-design
description: Job md format and cron-like scheduler design for deepbot
---

# Plan

deepbot に cron ライクな定期実行機能を追加する。ジョブ定義は `config/jobs/*.md` の1ジョブ1ファイル方式とし、ユーザー指定は自然言語頻度（毎時/毎日7:00/平日7:00など）中心にする。OpenClaw の実運用要素（永続化・履歴・配送分離）を踏襲しつつ、実行モデルは単純な直列実行（同時実行1）で運用する。

## Requirements
- ジョブ定義は Markdown(frontmatter + body) で管理できる。
- ユーザーは cron 式不要で登録できる（自然言語の頻度指定）。
- 送信先は Bot 側で自動解決（作成元チャンネル）できる。
- ジョブ本文に手順を追記可能で、人手編集を前提にする。
- ジョブ実行中のユーザー入力は待機案内を返し、完了後に順次処理する。
- ジョブごとに skills / mcp 指定を持てる。
- 実行履歴（成功/失敗/時間/エラー）を記録する。

## Scope
- In:
- `config/jobs/*.md` の定義読み込み、検証、実行
- `/定期登録` 系コマンドの追加
- スケジューラの常駐実行ループ追加
- ジョブ実行履歴の保存
- Out:
- 分散実行、クラスタ協調
- Web UI での管理
- 外部 webhook の高度な配送制御（v1は announce/none のみ）

## Files and entry points
- `src/deepbot/main.py`
- `src/deepbot/gateway/discord_bot.py`
- `src/deepbot/agent/runtime.py`
- `src/deepbot/config.py`
- `src/deepbot/scheduler/models.py` (new)
- `src/deepbot/scheduler/loader.py` (new)
- `src/deepbot/scheduler/engine.py` (new)
- `src/deepbot/scheduler/history.py` (new)
- `tests/test_scheduler_loader.py` (new)
- `tests/test_scheduler_engine.py` (new)
- `tests/test_discord_gateway.py` (update)

## Job MD format (detailed)
### File location and naming
- Directory: `config/jobs/`
- Extension: `.md`
- Filename rule: `<job-id>.md` (lower-case, `[a-z0-9-]+`)
- One file = one job

### Frontmatter schema
Required keys:
- `name`: job id (`[a-z0-9-]+`, filename と一致推奨)
- `description`: 1行説明
- `schedule`: 自然言語頻度（例: `毎時`, `毎日 7:00`, `平日 7:00`）

Optional keys:
- `timezone`: IANA TZ（default: `Asia/Tokyo`）
- `enabled`: bool（default: true）
- `delivery`: `announce` | `none`（default: `announce`）
- `channel`: `auto` | `<discord_channel_id>`（default: `auto`）
- `mode`: `isolated` | `main`（default: `isolated`）
- `skills`: string[]（job 実行時だけ有効）
- `mcp_servers`: string[]（allowlist）
- `mcp_tools`: string[]（allowlist, `server.tool` 形式）
- `timeout_seconds`: int（未指定時は `AGENT_TIMEOUT_SECONDS`）
- `max_retries`: int（default: 0）
- `retry_backoff`: `none` | `exponential`（default: `none`）
- `created_by`: user id（作成時に自動設定）
- `created_channel_id`: channel id（作成時に自動設定、`channel:auto` 解決用）

Validation rules:
- `schedule` は対応パターンに一致しない場合 reject
- `timezone` は正当な IANA 名のみ許可
- `skills` の未存在要素がある場合 job を `invalid` とし未実行
- `mcp_servers` / `mcp_tools` は未存在要素を reject
- `delivery=announce` 時は解決可能な channel が必要

### Body sections
Recommended structure:
- `# Prompt` (required)
- `# Steps` (optional)
- `# Output` (optional)

Parsing rules:
- `# Prompt` セクション本文をベースプロンプトとして使用
- `# Steps` がある場合は箇条書きを手順として連結
- `# Output` がある場合は出力形式制約として末尾に連結
- 未知セクションは無視せず付帯指示として末尾に追記（将来互換）

Execution prompt assembly:
- final_prompt = Prompt + "\n\n手順:\n..." + "\n\n出力条件:\n..."

### Example job file
```md
---
name: morning-weather
description: 平日朝の天気通知
schedule: 平日 7:00
timezone: Asia/Tokyo
enabled: true
delivery: announce
channel: auto
mode: isolated
skills:
  - agent-memory
mcp_servers:
  - openweather
mcp_tools:
  - openweather.get_forecast
max_retries: 1
retry_backoff: exponential
---

# Prompt
今日の東京の天気予報を、出勤前に読みやすい長さでまとめて。

# Steps
- 最高気温/最低気温、降水確率を含める
- 要点3つに絞る
- 最後に傘が必要か一言で書く

# Output
- 箇条書き3-5項目
- 150文字以内
```

## Data model / API changes
- Loader output model:
- `JobDefinition`: frontmatter + body parsed representation
- `JobExecutionSpec`: 実行時に必要な正規化済み設定
- Gateway commands:
- `/定期登録 プロンプト="..." 頻度="..."`
- `/定期一覧`
- `/定期停止 <job-id>`
- `/定期再開 <job-id>`
- `/定期今すぐ実行 <job-id>`
- Natural frequency parser:
- `毎時`
- `毎日 HH:MM`
- `平日 HH:MM`

## Action items
[ ] `scheduler/models.py` に JobDefinition / JobExecutionSpec を追加
[ ] `scheduler/loader.py` に MD(frontmatter + sections) ローダーを実装
[ ] `schedule` 自然文パーサと timezone バリデータを実装
[ ] `skills` / `mcp` 存在チェックと invalid 判定を実装
[ ] `scheduler/engine.py` で due 判定と直列実行ループを実装
[ ] ジョブ実行中ユーザー入力への待機メッセージ返却を gateway に実装
[ ] `/定期*` コマンドを実装し `config/jobs/` に md を作成/更新
[ ] 実行履歴 JSONL 書き込み（start/end/error/duration）を実装
[ ] テストを追加（ローダー、競合時挙動、同時刻複数ジョブ）
[ ] README/README_JP に job.md 仕様と運用手順を追記

## Testing and validation
- `pytest -q`
- Loader tests:
- 正常系（必須キー、sections抽出）
- 異常系（未知schedule、invalid timezone、未存在skill/mcp）
- Engine tests:
- ジョブ実行中ユーザー入力で待機メッセージを返す
- 同時刻2ジョブが直列処理される
- `enabled=false` が実行されない
- Gateway command tests:
- `/定期登録` が適切な md を生成
- `/定期停止` `/定期再開` が frontmatter を更新

## Risks and edge cases
- 手動編集で frontmatter が壊れるリスク（明確なエラー通知が必要）
- timezone 設定ミスによる意図しない時刻実行
- ジョブ本文肥大化による応答遅延
- 無効な skills/mcp 指定があるジョブのサイレント失敗

## Open questions
- `channel:auto` の解決失敗時にどこへフォールバックするか
- `mode=main` を v1 で露出するか（`isolated` 固定でもよい）
- `retry_backoff=exponential` の初期間隔を何秒にするか
