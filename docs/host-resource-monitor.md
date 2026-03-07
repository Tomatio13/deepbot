# ホスト resource monitor

このドキュメントでは、`deepbot` 本体とは別ディレクトリで動かすホスト用 resource monitor の使い方を説明します。

## 概要

CPU / メモリ / ディスクの監視は、`deepbot` コンテナ内の常駐 monitor ではなく、ホスト側で一回実行型の Python スクリプトを `systemd timer` から定期実行する構成を推奨します。

この monitor は:

- ホスト全体の resource 使用率を取る
- 閾値超過時だけ `http://127.0.0.1:8088/alerts` に `host_resource_pressure` を送る
- 圧迫状態が続いている間は `state.json` で再通知を抑止する
- ログを専用ディレクトリに残す

`deepbot` 側との役割分担は次のとおりです。

| 設定ファイル | 何を管理するか |
| --- | --- |
| `.env.deepbot` | `deepbot` 本体の alert 受信、Discord 通知、検知ルールの読み込み |
| `.env.host-resource-monitor` | ホスト全体の CPU / メモリ / ディスクの閾値、state 保存先、alert 送信先 |

つまり、standalone monitor を使う場合は `.env.deepbot` の `SECURITY_RESOURCE_*` を積極的に使いません。`deepbot` 側では `SECURITY_RESOURCE_MONITOR_ENABLED=false` にして、閾値は host monitor 側で管理するのが前提です。

## リポジトリ内の配置

実装テンプレートは次に置いています。

- `host-resource-monitor/host_resource_monitor.py`
- `host-resource-monitor/.env.host-resource-monitor.sample`
- `host-resource-monitor/host-resource-monitor.service`
- `host-resource-monitor/host-resource-monitor.timer`

## 実ホストへの配置先

実運用では、任意の専用ディレクトリへコピーして使います。以降は `/opt/deepbot-host-resource-monitor` を例として説明します。

この例はあくまでテンプレートです。自分の環境に合わせて、配置先ディレクトリ、ログ出力先、state 保存先、`systemd` unit のパスは変更して構いません。

このディレクトリに置く想定ファイル:

- `host_resource_monitor.py`
- `.env.host-resource-monitor`
- `host-resource-monitor.service`
- `host-resource-monitor.timer`
- ログ: `/var/log/deepbot-host-resource-monitor/host-resource-monitor.log`
- state: `/var/lib/deepbot-host-resource-monitor/state.json`

関連するファイル・ディレクトリ一覧:

| パス | 種別 | 役割 | どこから参照されるか |
| --- | --- | --- | --- |
| `/opt/deepbot-host-resource-monitor/` | 作業ディレクトリ | monitor 本体と設定テンプレートを置くディレクトリ | 手動実行、service/timer 配置元 |
| `/opt/deepbot-host-resource-monitor/host_resource_monitor.py` | 実行ファイル | Python monitor 本体 | `host-resource-monitor.service` の `ExecStart` |
| `/opt/deepbot-host-resource-monitor/.env.host-resource-monitor` | 設定ファイル | alert endpoint、閾値、state 保存先を定義 | `host-resource-monitor.service` の `EnvironmentFile` |
| `/opt/deepbot-host-resource-monitor/host-resource-monitor.service` | テンプレート | `systemd` oneshot service の元ファイル | `/etc/systemd/system/host-resource-monitor.service` にコピーして使用 |
| `/opt/deepbot-host-resource-monitor/host-resource-monitor.timer` | テンプレート | `systemd` timer の元ファイル | `/etc/systemd/system/host-resource-monitor.timer` にコピーして使用 |
| `/var/log/deepbot-host-resource-monitor/` | ディレクトリ | monitor ログの保存先 | `host-resource-monitor.service` の出力先 |
| `/var/log/deepbot-host-resource-monitor/host-resource-monitor.log` | ログファイル | 実行ログ | `host-resource-monitor.service` の `StandardOutput` / `StandardError` |
| `/var/lib/deepbot-host-resource-monitor/` | ディレクトリ | state ファイルの保存先 | `HOST_RESOURCE_MONITOR_STATE_PATH` の親ディレクトリ |
| `/var/lib/deepbot-host-resource-monitor/state.json` | state ファイル | 圧迫状態の継続を覚えて重複通知を抑止 | `host_resource_monitor.py` 本体 |
| `/etc/systemd/system/host-resource-monitor.service` | 配置先 | `systemctl` が読む service 定義 | `systemctl start/status host-resource-monitor.service` |
| `/etc/systemd/system/host-resource-monitor.timer` | 配置先 | `systemctl` が読む timer 定義 | `systemctl enable/status host-resource-monitor.timer` |

## 配置手順

```bash
sudo mkdir -p /opt/deepbot-host-resource-monitor
sudo mkdir -p /var/log/deepbot-host-resource-monitor
sudo mkdir -p /var/lib/deepbot-host-resource-monitor
sudo cp host-resource-monitor/host_resource_monitor.py /opt/deepbot-host-resource-monitor/
sudo cp host-resource-monitor/.env.host-resource-monitor.sample /opt/deepbot-host-resource-monitor/.env.host-resource-monitor
sudo cp host-resource-monitor/host-resource-monitor.service /opt/deepbot-host-resource-monitor/
sudo cp host-resource-monitor/host-resource-monitor.timer /opt/deepbot-host-resource-monitor/
```

## 設定

`/opt/deepbot-host-resource-monitor/.env.host-resource-monitor`

```env
DEEPBOT_SECURITY_ALERT_URL=http://127.0.0.1:8088/alerts
HOST_RESOURCE_MONITOR_STATE_PATH=/var/lib/deepbot-host-resource-monitor/state.json
HOST_RESOURCE_MONITOR_DISK_PATH=/

SECURITY_CPU_LOAD_PERCENT_THRESHOLD=85
SECURITY_MEMORY_PERCENT_THRESHOLD=90
SECURITY_DISK_PERCENT_THRESHOLD=90
```

## 手動実行

```bash
python3 /opt/deepbot-host-resource-monitor/host_resource_monitor.py --verbose
```

## systemd 登録

```bash
sudo cp /opt/deepbot-host-resource-monitor/host-resource-monitor.service /etc/systemd/system/host-resource-monitor.service
sudo cp /opt/deepbot-host-resource-monitor/host-resource-monitor.timer /etc/systemd/system/host-resource-monitor.timer
sudo systemctl daemon-reload
sudo systemctl enable --now host-resource-monitor.timer
```

## 確認

```bash
systemctl status host-resource-monitor.timer
systemctl status host-resource-monitor.service
journalctl -u host-resource-monitor.service -n 50
tail -f /var/log/deepbot-host-resource-monitor/host-resource-monitor.log
```

## deepbot 側の設定

`deepbot` を Docker で動かす場合は、コンテナ内 resource monitor は無効化しておく方が混乱しません。

`.env.deepbot`:

```env
SECURITY_RESOURCE_MONITOR_ENABLED=false
```

補足:

- `SECURITY_RESOURCE_MONITOR_INTERVAL_SECONDS`
- `SECURITY_CPU_LOAD_PERCENT_THRESHOLD`
- `SECURITY_MEMORY_PERCENT_THRESHOLD`
- `SECURITY_DISK_PERCENT_THRESHOLD`

これらは `deepbot` 内蔵 resource monitor を有効にした場合だけ使われます。standalone monitor を使う構成では、同等の値を `.env.host-resource-monitor` 側で設定してください。

## 備考

- `systemd timer` は起動 2 分後に初回実行、その後 10 分ごとに再実行します
- 圧迫状態が解消されるまで同じ通知は再送しません
- しきい値超過の判定自体は `deepbot` ではなく、このホスト monitor 側で行います
