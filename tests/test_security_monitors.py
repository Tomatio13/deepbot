from pathlib import Path

from deepbot.security import PortMonitor, ResourceMonitor, ResourceSnapshot, SecurityMonitorConfig


def _make_config() -> SecurityMonitorConfig:
    return SecurityMonitorConfig(
        state_dir=Path(".state/test"),
        port_monitor_enabled=True,
        port_monitor_interval_seconds=60,
        port_monitor_protocols={"tcp"},
        port_monitor_exclude_ports={22},
        resource_monitor_enabled=True,
        resource_monitor_interval_seconds=60,
        cpu_load_percent_threshold=85,
        memory_percent_threshold=90,
        disk_percent_threshold=90,
    )


def test_parse_ss_line_detects_public_tcp_listener() -> None:
    monitor = PortMonitor(_make_config(), emit_records=lambda records: len(records))
    line = 'tcp LISTEN 0 511 *:5001 *:* users:(("node",pid=4024061,fd=20))'
    parsed = monitor._parse_ss_line(line)  # noqa: SLF001

    assert parsed is not None
    assert parsed.proto == "tcp"
    assert parsed.port == 5001
    assert parsed.process == "node"


def test_parse_ss_line_ignores_loopback_listener() -> None:
    monitor = PortMonitor(_make_config(), emit_records=lambda records: len(records))
    line = 'tcp LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=1,fd=3))'
    parsed = monitor._parse_ss_line(line)  # noqa: SLF001

    assert parsed is None


def test_detect_issues_returns_expected_items() -> None:
    monitor = ResourceMonitor(_make_config(), emit_records=lambda records: len(records))
    issues = monitor._detect_issues(  # noqa: SLF001
        ResourceSnapshot(cpu_load_percent=92.0, memory_percent=91.0, disk_percent=30.0)
    )

    assert len(issues) == 2
    assert "CPU load" in issues[0]
    assert "Memory" in issues[1]
