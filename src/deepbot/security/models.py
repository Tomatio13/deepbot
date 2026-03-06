from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class LogEvent:
    timestamp: datetime
    category: str
    severity: str
    src_ip: str | None
    username: str | None
    service: str | None
    hostname: str | None
    message: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any], default_severity: str = "medium") -> "LogEvent":
        timestamp = parse_timestamp(record.get("date") or record.get("timestamp"))
        return cls(
            timestamp=timestamp,
            category=str(record.get("category") or "unknown"),
            severity=str(record.get("severity") or default_severity),
            src_ip=_optional_string(record.get("src_ip")),
            username=_optional_string(record.get("username")),
            service=_optional_string(record.get("service") or record.get("syslog_identifier")),
            hostname=_optional_string(record.get("hostname") or record.get("host")),
            message=str(record.get("message") or record.get("log") or ""),
            raw=dict(record),
        )


@dataclass(slots=True)
class DetectionRule:
    category: str
    severity: str
    threshold: int
    window_seconds: int
    dedupe_seconds: int
    description: str


@dataclass(slots=True)
class Incident:
    category: str
    severity: str
    description: str
    count: int
    window_seconds: int
    first_seen: datetime
    last_seen: datetime
    src_ip: str | None
    username: str | None
    service: str | None
    hostname: str | None
    message_samples: list[str]
    raw_context: list[dict[str, Any]]

    def fingerprint(self) -> str:
        src_ip = self.src_ip or "-"
        username = self.username or "-"
        service = self.service or "-"
        return f"{self.category}:{src_ip}:{username}:{service}"

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "count": self.count,
            "window_seconds": self.window_seconds,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "src_ip": self.src_ip,
            "username": self.username,
            "service": self.service,
            "hostname": self.hostname,
            "message_samples": self.message_samples,
            "raw_context": self.raw_context[:5],
        }


@dataclass(slots=True)
class Notification:
    title: str
    summary: str
    risk_level: str
    recommended_actions: list[str]


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return utc_now()
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=timezone.utc)
    return utc_now()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
