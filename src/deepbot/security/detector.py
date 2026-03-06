from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
import re
from typing import Iterable

from .models import DetectionRule, Incident, LogEvent

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class IncidentDetector:
    def __init__(self, rules: dict[str, DetectionRule], allowlist: set[str] | None = None) -> None:
        self.rules = rules
        self.allowlist = allowlist or set()
        self._events: dict[str, deque[LogEvent]] = defaultdict(deque)
        self._last_notified: dict[str, object] = {}

    def ingest(self, events: Iterable[LogEvent]) -> list[Incident]:
        incidents: list[Incident] = []
        for event in events:
            incident = self._ingest_one(event)
            if incident is not None:
                incidents.append(incident)
        return incidents

    def _ingest_one(self, event: LogEvent) -> Incident | None:
        rule = self.rules.get(event.category)
        if rule is None:
            return None
        if event.src_ip and event.src_ip in self.allowlist:
            return None

        key = self._aggregation_key(event)
        bucket = self._events[key]
        bucket.append(event)
        self._trim(bucket, rule.window_seconds, reference=event.timestamp)

        if len(bucket) < rule.threshold:
            return None
        if self._is_deduped(key, rule.dedupe_seconds, event.timestamp):
            return None

        self._last_notified[key] = event.timestamp
        return self._build_incident(rule, list(bucket))

    def _aggregation_key(self, event: LogEvent) -> str:
        src_ip = event.src_ip or "-"
        username = event.username or "-"
        service = event.service or "-"
        return f"{event.category}:{src_ip}:{username}:{service}"

    def _trim(self, bucket: deque[LogEvent], window_seconds: int, reference) -> None:
        cutoff = reference - timedelta(seconds=window_seconds)
        while bucket and bucket[0].timestamp < cutoff:
            bucket.popleft()

    def _is_deduped(self, key: str, dedupe_seconds: int, timestamp) -> bool:
        last_notified = self._last_notified.get(key)
        if last_notified is None:
            return False
        return (timestamp - last_notified).total_seconds() < dedupe_seconds

    def _build_incident(self, rule: DetectionRule, bucket: list[LogEvent]) -> Incident:
        first = bucket[0]
        last = bucket[-1]
        return Incident(
            category=rule.category,
            severity=rule.severity,
            description=rule.description,
            count=len(bucket),
            window_seconds=rule.window_seconds,
            first_seen=first.timestamp,
            last_seen=last.timestamp,
            src_ip=last.src_ip,
            username=last.username,
            service=last.service,
            hostname=last.hostname,
            message_samples=[event.message for event in bucket[-3:]],
            raw_context=[event.raw for event in bucket[-5:]],
        )


def extract_ip(message: str) -> str | None:
    match = _IP_RE.search(message)
    if match:
        return match.group(0)
    for token in message.replace("]", " ").replace("[", " ").split():
        parts = token.split(".")
        if token.count(".") == 3 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts if part):
            return token
    return None


def normalize_record(record: dict[str, object]) -> dict[str, object]:
    message = str(record.get("message") or record.get("log") or "")
    lowered = message.lower()
    category = record.get("category")
    if not category:
        if (
            "failed password" in lowered
            or "failed publickey" in lowered
            or "invalid user" in lowered
            or "connection closed by invalid user" in lowered
            or "connection closed by authenticating user" in lowered
            or "received disconnect from" in lowered
            or "maximum authentication attempts exceeded" in lowered
            or "not allowed because" in lowered
            or "permission denied" in lowered
        ):
            category = "auth_failure"
        elif "sudo:auth" in lowered and "authentication failure" in lowered:
            category = "sudo_auth_failure"
        elif "accepted password" in lowered:
            category = "auth_success"
        elif "[ufw block]" in lowered:
            category = "ufw_block"
        elif "sudo:" in lowered or " su:" in lowered or lowered.startswith("su:"):
            category = "privilege_escalation"
        else:
            category = "unknown"
    normalized = dict(record)
    normalized["category"] = category
    normalized.setdefault("src_ip", extract_ip(message))
    normalized.setdefault("message", message)
    return normalized
