from __future__ import annotations

from .detector import IncidentDetector, normalize_record
from .models import DetectionRule, Incident, LogEvent, Notification
from .monitors import (
    PortMonitor,
    ResourceMonitor,
    ResourceSnapshot,
    SecurityMonitorConfig,
    collect_resource_snapshot,
    detect_resource_pressure,
)
from .policy import DefenderDecision, DefenderSettings, PromptInjectionDefender
from .store import LocalEventStore

__all__ = [
    "DetectionRule",
    "DefenderDecision",
    "DefenderSettings",
    "Incident",
    "IncidentDetector",
    "IncidentSummarizer",
    "LocalEventStore",
    "LogEvent",
    "Notification",
    "PortMonitor",
    "PromptInjectionDefender",
    "ResourceMonitor",
    "ResourceSnapshot",
    "collect_resource_snapshot",
    "detect_resource_pressure",
    "SecurityAlertService",
    "SecurityMonitorConfig",
    "load_rules",
    "normalize_record",
    "render_notification_markdown",
]


def __getattr__(name: str):
    if name == "IncidentSummarizer":
        from .summarizer import IncidentSummarizer

        return IncidentSummarizer
    if name in {"SecurityAlertService", "load_rules", "render_notification_markdown"}:
        from .service import SecurityAlertService, load_rules, render_notification_markdown

        mapping = {
            "SecurityAlertService": SecurityAlertService,
            "load_rules": load_rules,
            "render_notification_markdown": render_notification_markdown,
        }
        return mapping[name]
    raise AttributeError(name)
