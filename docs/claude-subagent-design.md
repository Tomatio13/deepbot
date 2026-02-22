# Claude Sub-agent Integration Design (Deepbot)

## Goal
Strands Agents ベースの deepbot から、コード作業に強い `claude` CLI を「サブエージェント」として安全に呼び出せるようにする。

本設計は以下を満たす。
- 既存の Strands 実行経路を壊さない（追加機能として導入）
- デフォルト無効（明示的に有効化した場合のみ利用可能）
- 失敗時は fail-soft（起動失敗でも bot 全体は起動継続）

## Scope (Implemented)
- 新規ツール `claude_subagent` を追加（ワンショット実行）
- `claude -p --output-format json` を subprocess で実行
- `resume_session_id` を受け取り `--resume` を付与可能
- 実行結果（`result`, `session_id`, `duration_ms`, `total_cost_usd`）を JSON 文字列で返却
- sidecar transport（HTTP）を追加し、`claude-runner` サービス経由で実行可能

## Out of Scope (Future Phases)
- 常駐プロセス（persistent runner）
- チャンネル単位のランナープール / LRU eviction
- sidecar と deepbot のネットワーク分離強化（deny-by-default）

## Architecture
1. deepbot runtime がツールロード時に `CLAUDE_SUBAGENT_ENABLED=true` を検出  
2. `claude_subagent` ツールを `tools` に追加  
3. LLM が必要時に `claude_subagent(task=..., resume_session_id=...)` を呼ぶ  
4. ツール内で
   - `transport=direct`: `subprocess.run([...])` で `claude` CLI 実行
   - `transport=sidecar`: `CLAUDE_SUBAGENT_SIDECAR_URL` に HTTP POST
5. JSON出力を parse して tool result として返却

## Security & Risk Controls
- デフォルト無効（`CLAUDE_SUBAGENT_ENABLED=false`）
- 実行コマンドは単一実行ファイル名/パスのみ許可（空白禁止）
- 作業ディレクトリは絶対パス必須
- タイムアウト強制（`CLAUDE_SUBAGENT_TIMEOUT_SECONDS`）
- ツール登録失敗時は warning ログのみで継続（bot 全停止を避ける）

## Configuration
`.env.deepbot`:

```env
CLAUDE_SUBAGENT_ENABLED=false
CLAUDE_SUBAGENT_COMMAND=claude
CLAUDE_SUBAGENT_WORKDIR=/workspace/bot-rw
CLAUDE_SUBAGENT_TIMEOUT_SECONDS=300
CLAUDE_SUBAGENT_MODEL=
CLAUDE_SUBAGENT_SKIP_PERMISSIONS=false
CLAUDE_SUBAGENT_TRANSPORT=direct
CLAUDE_SUBAGENT_SIDECAR_URL=http://claude-runner:8787/v1/run
CLAUDE_SUBAGENT_SIDECAR_TOKEN=
```

sidecar を使う場合:
- Compose profile `claude-sidecar` で `claude-runner` を起動
- deepbot は `CLAUDE_SUBAGENT_TRANSPORT=sidecar` に切り替える
- 認証系環境変数は `.env.claude` に分離し、`.env.deepbot` と混在させない

## Operational Notes
- `claude` CLI 本体と認証状態はコンテナ内に事前準備が必要
- sidecar 運用時は認証情報を `claude-runner` 側に集約できる
- さらなる強化として、deepbot から sidecar 以外の外向き通信制御を追加予定

## Validation Strategy
- `config` 単体テスト:
  - 設定値の取り込み
  - 相対パス拒否バリデーション
- `claude_subagent` 単体テスト:
  - 引数構築（`--resume`, `--model`, skip permissions）
  - 非JSON応答時のエラーハンドリング

## Rollout Plan
1. 開発環境で `CLAUDE_SUBAGENT_ENABLED=true` で試験
2. Bot利用者を限定して検証（管理者/特定チャンネル）
3. sidecar を使う場合は `docker compose --profile claude-sidecar up -d claude-runner` を起動
4. 問題なければ本番で段階開放
5. 次フェーズで persistent 化を検討
