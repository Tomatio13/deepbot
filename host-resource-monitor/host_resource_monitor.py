from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("host_resource_monitor")

DEFAULT_STATE_PATH = "/var/lib/deepbot-host-resource-monitor/state.json"


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    cpu_load_percent: float
    memory_percent: float
    disk_percent: float


@dataclass(frozen=True, slots=True)
class HostResourceMonitorConfig:
    alert_url: str
    state_path: Path
    disk_path: str
    cpu_threshold: int
    memory_threshold: int
    disk_threshold: int
    category: str = "host_resource_pressure"
    severity: str = "high"
    service: str = "host-resource-monitor"

    @classmethod
    def from_env(cls) -> "HostResourceMonitorConfig":
        return cls(
            alert_url=(os.environ.get("DEEPBOT_SECURITY_ALERT_URL", "http://127.0.0.1:8088/alerts").strip()
            or "http://127.0.0.1:8088/alerts"),
            state_path=Path(
                os.environ.get(
                    "HOST_RESOURCE_MONITOR_STATE_PATH",
                    DEFAULT_STATE_PATH,
                ).strip()
                or DEFAULT_STATE_PATH
            ).expanduser(),
            disk_path=os.environ.get("HOST_RESOURCE_MONITOR_DISK_PATH", "/").strip() or "/",
            cpu_threshold=int(os.environ.get("SECURITY_CPU_LOAD_PERCENT_THRESHOLD", "85")),
            memory_threshold=int(os.environ.get("SECURITY_MEMORY_PERCENT_THRESHOLD", "90")),
            disk_threshold=int(os.environ.get("SECURITY_DISK_PERCENT_THRESHOLD", "90")),
        )


@dataclass(frozen=True, slots=True)
class HostResourceMonitorResult:
    pressure_active: bool
    notified: bool
    accepted_incidents: int
    issues: tuple[str, ...]
    snapshot: ResourceSnapshot


def collect_resource_snapshot(*, disk_path: str = "/") -> ResourceSnapshot:
    load1, _, _ = os.getloadavg()
    cpu_count = os.cpu_count() or 1
    return ResourceSnapshot(
        cpu_load_percent=(load1 / cpu_count) * 100.0,
        memory_percent=read_memory_percent(),
        disk_percent=read_disk_percent(disk_path),
    )


def detect_resource_pressure(
    snapshot: ResourceSnapshot,
    *,
    cpu_threshold: int,
    memory_threshold: int,
    disk_threshold: int,
    disk_path: str = "/",
) -> list[str]:
    issues: list[str] = []
    if snapshot.cpu_load_percent >= cpu_threshold:
        issues.append(f"CPU load {snapshot.cpu_load_percent:.1f}% >= {cpu_threshold}%")
    if snapshot.memory_percent >= memory_threshold:
        issues.append(f"Memory {snapshot.memory_percent:.1f}% >= {memory_threshold}%")
    if snapshot.disk_percent >= disk_threshold:
        issues.append(f"Disk {disk_path} {snapshot.disk_percent:.1f}% >= {disk_threshold}%")
    return issues


def read_memory_percent() -> float:
    try:
        total = 0.0
        available = 0.0
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = float(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available = float(line.split()[1])
                if total and available:
                    break
        if total <= 0:
            return 0.0
        return ((total - available) / total) * 100.0
    except Exception:
        return 0.0


def read_disk_percent(path: str) -> float:
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0.0
    return (usage.used / usage.total) * 100.0


def build_record(
    config: HostResourceMonitorConfig,
    snapshot: ResourceSnapshot,
    issues: tuple[str, ...] | list[str],
) -> dict[str, object]:
    message = " / ".join(issues)
    return {
        "category": config.category,
        "severity": config.severity,
        "service": config.service,
        "hostname": socket.gethostname(),
        "message": f"Resource pressure detected: {message}",
        "cpu_load_percent": round(snapshot.cpu_load_percent, 1),
        "memory_percent": round(snapshot.memory_percent, 1),
        "disk_percent": round(snapshot.disk_percent, 1),
    }


def load_pressure_state(state_path: Path) -> bool:
    if not state_path.exists():
        return False
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(payload.get("pressure_active"))


def save_pressure_state(state_path: Path, pressure_active: bool) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"pressure_active": pressure_active}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def post_records(alert_url: str, records: list[dict[str, object]]) -> int:
    payload = json.dumps(records, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=alert_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"deepbot alert endpoint returned {exc.code}: {body}") from exc
    data = json.loads(body)
    return int(data.get("accepted_incidents", 0))


def run_once(
    config: HostResourceMonitorConfig,
    *,
    snapshot: ResourceSnapshot | None = None,
    post_records_func=None,
) -> HostResourceMonitorResult:
    current_snapshot = snapshot or collect_resource_snapshot(disk_path=config.disk_path)
    issues = tuple(
        detect_resource_pressure(
            current_snapshot,
            cpu_threshold=config.cpu_threshold,
            memory_threshold=config.memory_threshold,
            disk_threshold=config.disk_threshold,
            disk_path=config.disk_path,
        )
    )
    previous_active = load_pressure_state(config.state_path)
    pressure_active = bool(issues)
    notified = False
    accepted_incidents = 0
    sender = post_records_func or post_records
    if pressure_active and not previous_active:
        accepted_incidents = sender(config.alert_url, [build_record(config, current_snapshot, issues)])
        notified = accepted_incidents > 0
        logger.info(
            "Host resource monitor emitted incident (accepted_incidents=%s): %s",
            accepted_incidents,
            " / ".join(issues),
        )
    elif pressure_active:
        logger.info("Host resource monitor still under pressure; notification suppressed: %s", " / ".join(issues))
    else:
        logger.info(
            "Host resource monitor healthy: cpu=%.1f memory=%.1f disk=%.1f",
            current_snapshot.cpu_load_percent,
            current_snapshot.memory_percent,
            current_snapshot.disk_percent,
        )
    save_pressure_state(config.state_path, pressure_active)
    return HostResourceMonitorResult(
        pressure_active=pressure_active,
        notified=notified,
        accepted_incidents=accepted_incidents,
        issues=issues,
        snapshot=current_snapshot,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emit host resource pressure events to deepbot.")
    parser.add_argument("--alert-url", default=None, help="deepbot security alert endpoint URL")
    parser.add_argument("--state-path", default=None, help="Path to persisted pressure state JSON")
    parser.add_argument("--disk-path", default=None, help="Disk path to monitor")
    parser.add_argument("--cpu-threshold", type=int, default=None, help="CPU load threshold percent")
    parser.add_argument("--memory-threshold", type=int, default=None, help="Memory threshold percent")
    parser.add_argument("--disk-threshold", type=int, default=None, help="Disk threshold percent")
    parser.add_argument("--verbose", action="store_true", help="Enable INFO logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    config = HostResourceMonitorConfig.from_env()
    overrides = {
        "alert_url": args.alert_url,
        "state_path": Path(args.state_path).expanduser() if args.state_path else None,
        "disk_path": args.disk_path,
        "cpu_threshold": args.cpu_threshold,
        "memory_threshold": args.memory_threshold,
        "disk_threshold": args.disk_threshold,
    }
    config = HostResourceMonitorConfig(
        alert_url=overrides["alert_url"] or config.alert_url,
        state_path=overrides["state_path"] or config.state_path,
        disk_path=overrides["disk_path"] or config.disk_path,
        cpu_threshold=overrides["cpu_threshold"] if overrides["cpu_threshold"] is not None else config.cpu_threshold,
        memory_threshold=overrides["memory_threshold"] if overrides["memory_threshold"] is not None else config.memory_threshold,
        disk_threshold=overrides["disk_threshold"] if overrides["disk_threshold"] is not None else config.disk_threshold,
    )
    result = run_once(config)
    if args.verbose:
        print(json.dumps({"result": asdict(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
