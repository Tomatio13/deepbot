from pathlib import Path
import importlib.util
import sys

from deepbot.security import (
    PortMonitor,
    ResourceMonitor,
    ResourceSnapshot,
    SecurityMonitorConfig,
    collect_resource_snapshot,
    detect_resource_pressure,
)


def _load_host_resource_monitor_module():
    module_path = Path(__file__).resolve().parents[1] / "host-resource-monitor" / "host_resource_monitor.py"
    spec = importlib.util.spec_from_file_location("host_resource_monitor", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_detect_resource_pressure_formats_disk_path() -> None:
    issues = detect_resource_pressure(
        ResourceSnapshot(cpu_load_percent=12.0, memory_percent=95.0, disk_percent=91.0),
        cpu_threshold=85,
        memory_threshold=90,
        disk_threshold=90,
        disk_path="/var",
    )

    assert issues == ["Memory 95.0% >= 90%", "Disk /var 91.0% >= 90%"]


def test_host_resource_monitor_notifies_only_on_state_transition(tmp_path: Path) -> None:
    module = _load_host_resource_monitor_module()
    config = module.HostResourceMonitorConfig(
        alert_url="http://127.0.0.1:8088/alerts",
        state_path=tmp_path / "state.json",
        disk_path="/",
        cpu_threshold=85,
        memory_threshold=90,
        disk_threshold=90,
    )
    sent_payloads: list[list[dict[str, object]]] = []

    def fake_post(_alert_url: str, records: list[dict[str, object]]) -> int:
        sent_payloads.append(records)
        return 1

    first = module.run_once(
        config,
        snapshot=ResourceSnapshot(cpu_load_percent=90.0, memory_percent=30.0, disk_percent=20.0),
        post_records_func=fake_post,
    )
    second = module.run_once(
        config,
        snapshot=ResourceSnapshot(cpu_load_percent=88.0, memory_percent=31.0, disk_percent=20.0),
        post_records_func=fake_post,
    )
    recovered = module.run_once(
        config,
        snapshot=ResourceSnapshot(cpu_load_percent=10.0, memory_percent=20.0, disk_percent=20.0),
        post_records_func=fake_post,
    )
    third = module.run_once(
        config,
        snapshot=ResourceSnapshot(cpu_load_percent=91.0, memory_percent=25.0, disk_percent=20.0),
        post_records_func=fake_post,
    )

    assert first.notified is True
    assert second.notified is False
    assert recovered.pressure_active is False
    assert third.notified is True
    assert len(sent_payloads) == 2
    assert sent_payloads[0][0]["category"] == "host_resource_pressure"
