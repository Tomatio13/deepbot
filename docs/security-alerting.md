# ホストセキュリティ通知

このドキュメントでは、`deepbot` に統合した Fluent Bit ベースのホストセキュリティ通知機能を説明します。

## 概要

`deepbot` は `POST /alerts` で受け取ったホストイベントを正規化・集約し、LLM で日本語要約を生成して、指定した Discord チャンネルへ通知できます。

現在の流れは次のとおりです。

1. Fluent Bit が `auth.log`, `secure`, `journald`, `ufw.log` などのホストログを読む
2. Fluent Bit がレコードをカテゴリ分けして `http://127.0.0.1:8088/alerts` へ送る
3. `deepbot` がイベントを正規化・集約する
4. `deepbot` が incident を日本語で要約し、指定 Discord チャンネルへ通知する

## 必須設定

`.env.deepbot` に最低限次を設定してください。

```env
SECURITY_ENABLED=true
SECURITY_ALERT_CHANNEL_ID=<discord channel id>
SECURITY_ALERT_BIND_HOST=0.0.0.0
SECURITY_ALERT_BIND_PORT=8088
SECURITY_RULES_PATH=/app/config/security/detection-rules.yaml
SECURITY_STATE_DIR=/workspace/bot-rw/security
SECURITY_PORT_MONITOR_ENABLED=false
```

補足:

- `deepbot` を Docker で動かし、Fluent Bit をホスト側で動かす場合は `SECURITY_ALERT_BIND_HOST=0.0.0.0` にしてください
- `SECURITY_PORT_MONITOR_ENABLED` は、実行環境に `ss` があり、かつ監視対象のネットワーク名前空間を見られる場合だけ有効にしてください
- `SECURITY_ALLOWLIST` を未設定にすると、既定値として `127.0.0.1,::1` が入ります
- localhost からの SSH 検証を通したい場合は、明示的に空にしてください

```env
SECURITY_ALLOWLIST=
```

## Docker での挙動

`compose.yaml` では、ホスト側 Fluent Bit が送れるように `127.0.0.1:8088:8088` を公開しています。

env を変更した場合:

```bash
docker compose up -d --force-recreate deepbot
```

コードを変更した場合:

```bash
docker compose build deepbot
docker compose up -d
```

## Fluent Bit のセットアップ

使用するファイル:

- `infra/fluent-bit/fluent-bit.conf`
- `infra/fluent-bit/parsers.conf`
- `infra/systemd/fluent-bit.service`

配置例:

```bash
sudo mkdir -p /opt/deepbot/infra/fluent-bit
sudo cp infra/fluent-bit/fluent-bit.conf /opt/deepbot/infra/fluent-bit/fluent-bit.conf
sudo cp infra/fluent-bit/parsers.conf /opt/deepbot/infra/fluent-bit/parsers.conf
sudo cp infra/systemd/fluent-bit.service /etc/systemd/system/fluent-bit.service
sudo mkdir -p /var/lib/deepbot/fluent-bit
sudo systemctl daemon-reload
sudo systemctl restart fluent-bit
```

## 検知ルール

検知ルールは次のファイルで管理します。

- `config/security/detection-rules.yaml`

主な調整ポイント:

- `threshold`: incident 化する最小イベント数
- `window_seconds`: 集約ウィンドウ
- `dedupe_seconds`: 同じ fingerprint の再通知抑止時間

ローカル検証を急ぐ場合は、`dedupe_seconds` を一時的に小さくするか、`SECURITY_ALLOWLIST` を空にしてください。

## 動作確認

待受確認:

```bash
docker compose exec deepbot python -c "import socket; s=socket.socket(); print(s.connect_ex(('127.0.0.1', 8088)))"
```

期待値:

- `0` なら alert receiver が待受中

API 直投入テスト:

```bash
curl -X POST http://127.0.0.1:8088/alerts \
  -H 'content-type: application/json' \
  -d '[
    {
      "date": "2026-03-07T06:25:00Z",
      "category": "sudo_auth_failure",
      "service": "sudo",
      "message": "pam_unix(sudo:auth): authentication failure; logname=masato uid=1000"
    }
  ]'
```

期待値:

```json
{"accepted_incidents":1}
```

状態ファイル:

- `/workspace/bot-rw/security/incidents.jsonl`
- `/workspace/bot-rw/security/dead-letter.jsonl`

## よくあるハマりどころ

- `accepted_incidents: 0`
  - `SECURITY_ALLOWLIST` を確認する
  - `config/security/detection-rules.yaml` の `dedupe_seconds` を確認する
- `curl` 直投入は通るのに Fluent Bit 経由だけ通知されない
  - Fluent Bit が `deepbot` 側の最新設定ファイルを読んでいるか確認する
  - 同じ fingerprint が dedupe されていないか確認する
- Discord に通知が来ない
  - `/workspace/bot-rw/security/dead-letter.jsonl` を確認する
  - `SECURITY_ALERT_CHANNEL_ID` が正しいか、Bot に投稿権限があるか確認する
