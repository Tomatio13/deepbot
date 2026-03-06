from datetime import datetime, timedelta, timezone

from deepbot.security import DetectionRule, IncidentDetector, LogEvent, normalize_record


def test_detector_aggregates_threshold_events() -> None:
    rules = {
        "auth_failure": DetectionRule(
            category="auth_failure",
            severity="medium",
            threshold=3,
            window_seconds=300,
            dedupe_seconds=600,
            description="Repeated failures",
        )
    }
    detector = IncidentDetector(rules)
    base = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)

    incidents = []
    for offset in range(3):
        record = normalize_record(
            {
                "date": (base + timedelta(seconds=offset)).isoformat(),
                "message": f"Failed password for root from 203.0.113.10 port 42{offset} ssh2",
            }
        )
        incidents.extend(detector.ingest([LogEvent.from_record(record, default_severity="medium")]))

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.category == "auth_failure"
    assert incident.count == 3
    assert incident.src_ip == "203.0.113.10"


def test_normalize_record_maps_sudo_auth_failure() -> None:
    record = normalize_record(
        {
            "date": "2026-03-06T12:00:00Z",
            "service": "sudo",
            "message": "pam_unix(sudo:auth): authentication failure; logname=masato uid=1000",
        }
    )

    assert record["category"] == "sudo_auth_failure"
