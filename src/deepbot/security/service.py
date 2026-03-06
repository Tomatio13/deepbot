from __future__ import annotations

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable

from deepbot.config import AppConfig

from .detector import IncidentDetector, normalize_record
from .models import DetectionRule, Incident, LogEvent, Notification
from .monitors import PortMonitor, ResourceMonitor, SecurityMonitorConfig
from .store import LocalEventStore
from .summarizer import IncidentSummarizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityRuntimeConfig:
    enabled: bool
    bind_host: str
    bind_port: int
    rules_path: Path
    allowlist: set[str]
    state_dir: Path
    alert_channel_id: str
    monitor_config: SecurityMonitorConfig

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "SecurityRuntimeConfig":
        return cls(
            enabled=config.security_enabled,
            bind_host=config.security_alert_bind_host,
            bind_port=config.security_alert_bind_port,
            rules_path=config.security_rules_path,
            allowlist=set(config.security_allowlist),
            state_dir=config.security_state_dir,
            alert_channel_id=config.security_alert_channel_id,
            monitor_config=SecurityMonitorConfig(
                state_dir=config.security_state_dir,
                port_monitor_enabled=config.security_port_monitor_enabled,
                port_monitor_interval_seconds=config.security_port_monitor_interval_seconds,
                port_monitor_protocols=set(config.security_port_monitor_protocols),
                port_monitor_exclude_ports=set(config.security_port_monitor_exclude_ports),
                resource_monitor_enabled=config.security_resource_monitor_enabled,
                resource_monitor_interval_seconds=config.security_resource_monitor_interval_seconds,
                cpu_load_percent_threshold=config.security_cpu_load_percent_threshold,
                memory_percent_threshold=config.security_memory_percent_threshold,
                disk_percent_threshold=config.security_disk_percent_threshold,
            ),
        )


class SecurityAlertService:
    def __init__(self, app_config: AppConfig) -> None:
        self._app_config = app_config
        self._config = SecurityRuntimeConfig.from_app_config(app_config)
        self._rules = load_rules(self._config.rules_path)
        self._store = LocalEventStore(self._config.state_dir)
        self._detector = IncidentDetector(self._rules, allowlist=self._config.allowlist)
        self._summarizer = IncidentSummarizer(app_config)
        self._port_monitor = PortMonitor(self._config.monitor_config, self.handle_records)
        self._resource_monitor = ResourceMonitor(self._config.monitor_config, self.handle_records)
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()
        self._sender: Callable[[str, str, tuple[str, ...]], Awaitable[Any]] | None = None
        self._sender_loop: asyncio.AbstractEventLoop | None = None
        self._pending_notifications: list[tuple[str, Incident, Notification]] = []

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def configure_sender(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        sender: Callable[[str, str, tuple[str, ...]], Awaitable[Any]],
    ) -> None:
        self._sender_loop = loop
        self._sender = sender
        logger.info("Security alert sender configured for channel %s", self._config.alert_channel_id)
        self._flush_pending_notifications()

    def start(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._started:
                return
            self._server = ThreadingHTTPServer((self._config.bind_host, self._config.bind_port), _AlertHandler)
            self._server.service = self  # type: ignore[attr-defined]
            self._server_thread = threading.Thread(
                target=self._server.serve_forever,
                name="security-alert-server",
                daemon=True,
            )
            self._server_thread.start()
            self._port_monitor.start()
            self._resource_monitor.start()
            self._started = True
            logger.info(
                "Security alert service listening on %s:%s",
                self._config.bind_host,
                self._config.bind_port,
            )

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._resource_monitor.stop()
            self._port_monitor.stop()
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
            if self._server_thread is not None:
                self._server_thread.join(timeout=2.0)
            self._server = None
            self._server_thread = None
            self._started = False

    def handle_records(self, records: list[dict[str, Any]]) -> int:
        normalized = [normalize_record(record) for record in records]
        incidents = self._detector.ingest(
            [
                LogEvent.from_record(
                    record,
                    default_severity=self._rules.get(str(record["category"]), DetectionRule(
                        category=str(record["category"]),
                        severity="medium",
                        threshold=1,
                        window_seconds=60,
                        dedupe_seconds=60,
                        description=str(record["category"]),
                    )).severity,
                )
                for record in normalized
            ]
        )
        for incident in incidents:
            notification = self._summarizer.summarize(incident)
            envelope = {
                "incident": incident.to_prompt_payload(),
                "notification": asdict(notification),
                "markdown": render_notification_markdown(incident, notification),
            }
            self._store.append_incident(envelope)
            self._notify_discord(envelope["markdown"], incident=incident, notification=notification)
        return len(incidents)

    def _notify_discord(self, markdown: str, *, incident: Incident, notification: Notification) -> None:
        if self._config.alert_channel_id:
            if self._sender is not None and self._sender_loop is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self._sender(self._config.alert_channel_id, markdown, ()),
                    self._sender_loop,
                )
                try:
                    future.result(timeout=15)
                    return
                except Exception as exc:
                    logger.warning("Failed to send security notification through gateway sender: %s", exc)
            try:
                self._post_discord_channel_message(self._config.alert_channel_id, markdown)
                return
            except Exception as exc:
                logger.warning("Failed to send security notification via Discord REST API: %s", exc)
                self._store.append_dead_letter(
                    {
                        "reason": "discord_send_error",
                        "error": str(exc),
                        "incident": incident.to_prompt_payload(),
                        "notification": asdict(notification),
                    }
                )
                return

        self._pending_notifications.append((markdown, incident, notification))

    def _flush_pending_notifications(self) -> None:
        if self._sender is None or self._sender_loop is None or not self._pending_notifications:
            return
        pending = list(self._pending_notifications)
        self._pending_notifications.clear()
        for markdown, incident, notification in pending:
            self._notify_discord(markdown, incident=incident, notification=notification)

    def _post_discord_channel_message(self, channel_id: str, content: str) -> None:
        token = self._app_config.discord_bot_token.strip()
        if not token:
            raise RuntimeError("discord bot token is missing")
        payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=f"https://discord.com/api/v10/channels/{channel_id}/messages",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "deepbot-security-alert/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status < 200 or status >= 300:
                    raise RuntimeError(f"discord api returned status {status}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"discord api returned status {exc.code}: {body}") from exc


class _AlertHandler(BaseHTTPRequestHandler):
    server_version = "DeepbotSecurityAlert/0.1"

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/alerts":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown path")
            return
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            if isinstance(payload, dict):
                records = [payload]
            elif isinstance(payload, list):
                records = payload
            else:
                raise ValueError("Payload must be a JSON object or array")
            accepted = self.server.service.handle_records(records)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Invalid security alert payload: %s", exc)
            self.send_response(HTTPStatus.BAD_REQUEST)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        self.send_response(HTTPStatus.ACCEPTED)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"accepted_incidents": accepted}).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.info("%s - %s", self.address_string(), format % args)


def load_rules(path: Path) -> dict[str, DetectionRule]:
    data = _parse_rules_yaml(path.read_text(encoding="utf-8"))
    rules: dict[str, DetectionRule] = {}
    for item in data:
        rule = DetectionRule(
            category=str(item["category"]),
            severity=str(item["severity"]),
            threshold=int(item["threshold"]),
            window_seconds=int(item["window_seconds"]),
            dedupe_seconds=int(item["dedupe_seconds"]),
            description=str(item.get("description") or item["category"]),
        )
        rules[rule.category] = rule
    return rules


def _parse_rules_yaml(text: str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "rules:":
            continue
        if stripped.startswith("- "):
            if current:
                rules.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if stripped and ":" in stripped:
                key, value = stripped.split(":", 1)
                current[key.strip()] = value.strip()
            continue
        if current is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        current[key.strip()] = value.strip()
    if current:
        rules.append(current)
    return rules


def render_notification_markdown(incident: Incident, notification: Notification) -> str:
    lines = [
        f"## {notification.title}",
        f"- Severity: `{notification.risk_level}`",
        f"- Category: `{incident.category}`",
        f"- Count: `{incident.count}` in `{incident.window_seconds}s`",
    ]
    if incident.src_ip:
        lines.append(f"- Source IP: `{incident.src_ip}`")
    if incident.username:
        lines.append(f"- User: `{incident.username}`")
    if incident.service:
        lines.append(f"- Service: `{incident.service}`")
    lines.extend(
        [
            "",
            notification.summary,
            "",
            "### Recommended actions",
        ]
    )
    lines.extend(f"- {item}" for item in notification.recommended_actions)
    if incident.message_samples:
        lines.extend(["", "### Recent samples"])
        lines.extend(f"- {sample}" for sample in incident.message_samples[:3])
    return "\n".join(lines).strip()
