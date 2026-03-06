from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_PROCESS_RE = re.compile(r'users:\(\("([^"]+)"')
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


@dataclass(frozen=True, slots=True)
class SecurityMonitorConfig:
    state_dir: Path
    port_monitor_enabled: bool
    port_monitor_interval_seconds: int
    port_monitor_protocols: set[str]
    port_monitor_exclude_ports: set[int]
    resource_monitor_enabled: bool
    resource_monitor_interval_seconds: int
    cpu_load_percent_threshold: int
    memory_percent_threshold: int
    disk_percent_threshold: int


@dataclass(frozen=True, slots=True)
class ListeningPort:
    proto: str
    local: str
    port: int
    process: str | None

    def key(self) -> str:
        process = self.process or "-"
        return f"{self.proto}:{self.local}:{self.port}:{process}"

    def to_record(self) -> dict[str, object]:
        return {
            "category": "new_listen_port",
            "service": self.process or "unknown",
            "message": f"New listening port detected: {self.proto.upper()} {self.local}:{self.port} ({self.process or 'unknown'})",
            "port": self.port,
            "proto": self.proto,
            "listen_address": self.local,
            "severity": "high",
        }


class PortMonitor:
    def __init__(self, config: SecurityMonitorConfig, emit_records: Callable[[list[dict[str, object]]], int]) -> None:
        self._config = config
        self._emit_records = emit_records
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._known_path = config.state_dir / "known_listen_ports.json"
        self._initialized_from_disk = self._known_path.exists()
        self._known: set[str] = self._load_known()

    def start(self) -> None:
        if not self._config.port_monitor_enabled:
            return
        self._thread = threading.Thread(target=self._run, name="port-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # pragma: no cover
                logger.warning("Port monitor error: %s", exc)
            self._stop_event.wait(self._config.port_monitor_interval_seconds)

    def _poll_once(self) -> None:
        ports = self._collect_ports()
        current = {port.key() for port in ports}
        if not self._initialized_from_disk and not self._known:
            self._known = current
            self._save_known(self._known)
            self._initialized_from_disk = True
            logger.info("Port monitor baseline initialized with %s listening ports", len(current))
            return
        new_keys = current - self._known
        if new_keys:
            new_ports = [port for port in ports if port.key() in new_keys]
            accepted = self._emit_records([port.to_record() for port in new_ports])
            logger.info("Port monitor detected %s new listening ports (accepted_incidents=%s)", len(new_ports), accepted)
        self._known = current
        self._save_known(self._known)

    def _collect_ports(self) -> list[ListeningPort]:
        result = subprocess.run(["ss", "-tulpnH"], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning("Failed to run ss command: %s", result.stderr.strip())
            return []
        ports: list[ListeningPort] = []
        for line in result.stdout.splitlines():
            parsed = self._parse_ss_line(line)
            if parsed is not None:
                ports.append(parsed)
        return ports

    def _parse_ss_line(self, line: str) -> ListeningPort | None:
        parts = line.split(None, 6)
        if len(parts) < 6:
            return None
        proto = parts[0].lower()
        state = parts[1].upper()
        if proto not in self._config.port_monitor_protocols:
            return None
        if proto == "tcp" and state != "LISTEN":
            return None
        local = parts[4]
        process_field = parts[6] if len(parts) == 7 else ""
        host, port = self._split_host_port(local)
        if port is None:
            return None
        if port in self._config.port_monitor_exclude_ports:
            return None
        if self._is_loopback_or_local(host):
            return None
        return ListeningPort(proto=proto, local=host, port=port, process=self._extract_process(process_field))

    def _split_host_port(self, local: str) -> tuple[str, int | None]:
        value = local.strip()
        if value.startswith("[") and "]:" in value:
            host, _, port_text = value[1:].partition("]:")
        else:
            host, _, port_text = value.rpartition(":")
        if not port_text.isdigit():
            return value, None
        return host, int(port_text)

    def _is_loopback_or_local(self, host: str) -> bool:
        value = host.strip()
        if value in {"127.0.0.1", "::1", "localhost"}:
            return True
        if value.startswith("127."):
            return True
        if value in {"127.0.0.53", "127.0.0.54"}:
            return True
        if value == "*":
            return False
        if value == "::":
            return False
        if value.startswith("224.") or value.startswith("239."):
            return True
        if value.lower().startswith("ff"):
            return True
        if _IPV4_RE.match(value):
            return False
        return False

    def _extract_process(self, process_field: str) -> str | None:
        match = _PROCESS_RE.search(process_field)
        if not match:
            return None
        return match.group(1)

    def _load_known(self) -> set[str]:
        if not self._known_path.exists():
            return set()
        try:
            data = json.loads(self._known_path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover
            return set()
        if isinstance(data, list):
            return {str(item) for item in data}
        return set()

    def _save_known(self, keys: set[str]) -> None:
        self._known_path.parent.mkdir(parents=True, exist_ok=True)
        self._known_path.write_text(json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(slots=True)
class ResourceSnapshot:
    cpu_load_percent: float
    memory_percent: float
    disk_percent: float


class ResourceMonitor:
    def __init__(self, config: SecurityMonitorConfig, emit_records: Callable[[list[dict[str, object]]], int]) -> None:
        self._config = config
        self._emit_records = emit_records
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_pressure_state = False

    def start(self) -> None:
        if not self._config.resource_monitor_enabled:
            return
        self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # pragma: no cover
                logger.warning("Resource monitor error: %s", exc)
            self._stop_event.wait(self._config.resource_monitor_interval_seconds)

    def _poll_once(self) -> None:
        snapshot = self._collect_snapshot()
        issues = self._detect_issues(snapshot)
        if not issues:
            self._in_pressure_state = False
            return
        if self._in_pressure_state:
            return
        self._in_pressure_state = True
        message = " / ".join(issues)
        record = {
            "category": "host_resource_pressure",
            "severity": "high",
            "service": "resource-monitor",
            "hostname": os.uname().nodename,
            "message": f"Resource pressure detected: {message}",
            "cpu_load_percent": round(snapshot.cpu_load_percent, 1),
            "memory_percent": round(snapshot.memory_percent, 1),
            "disk_percent": round(snapshot.disk_percent, 1),
        }
        accepted = self._emit_records([record])
        logger.info("Resource monitor emitted incident (accepted_incidents=%s): %s", accepted, message)

    def _collect_snapshot(self) -> ResourceSnapshot:
        load1, _, _ = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        return ResourceSnapshot(
            cpu_load_percent=(load1 / cpu_count) * 100.0,
            memory_percent=_read_memory_percent(),
            disk_percent=_read_disk_percent("/"),
        )

    def _detect_issues(self, snapshot: ResourceSnapshot) -> list[str]:
        issues: list[str] = []
        if snapshot.cpu_load_percent >= self._config.cpu_load_percent_threshold:
            issues.append(f"CPU load {snapshot.cpu_load_percent:.1f}% >= {self._config.cpu_load_percent_threshold}%")
        if snapshot.memory_percent >= self._config.memory_percent_threshold:
            issues.append(f"Memory {snapshot.memory_percent:.1f}% >= {self._config.memory_percent_threshold}%")
        if snapshot.disk_percent >= self._config.disk_percent_threshold:
            issues.append(f"Disk / {snapshot.disk_percent:.1f}% >= {self._config.disk_percent_threshold}%")
        return issues


def _read_memory_percent() -> float:
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


def _read_disk_percent(path: str) -> float:
    usage = shutil.disk_usage(path)
    if usage.total <= 0:
        return 0.0
    return (usage.used / usage.total) * 100.0
