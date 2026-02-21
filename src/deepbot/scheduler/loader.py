from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from deepbot.mcp_tools import list_configured_mcp_servers
from deepbot.scheduler.models import JobDefinition
from deepbot.skills import list_skills

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_KEY_VALUE_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class JobFormatError(ValueError):
    pass


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except Exception:
            return raw
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise JobFormatError("frontmatter not found")
    block = match.group(1)
    body = text[match.end():]

    parsed: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_list_key is None:
                raise JobFormatError(f"invalid list item: {stripped}")
            if not isinstance(parsed.get(current_list_key), list):
                parsed[current_list_key] = []
            parsed[current_list_key].append(str(_parse_scalar(stripped[2:].strip())))
            continue

        m = _KEY_VALUE_RE.match(stripped)
        if m is None:
            raise JobFormatError(f"invalid frontmatter line: {stripped}")
        key = m.group(1).strip()
        value_text = m.group(2)
        if value_text.strip() == "":
            parsed[key] = []
            current_list_key = key
            continue
        parsed[key] = _parse_scalar(value_text)
        current_list_key = None

    return parsed, body


def _parse_sections(body: str) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    prompt = ""
    steps: list[str] = []
    output_constraints: list[str] = []
    extras: list[str] = []

    current: str | None = None
    section_lines: dict[str, list[str]] = {
        "prompt": [],
        "steps": [],
        "output": [],
    }
    extra_buffer: list[str] = []

    for raw_line in body.splitlines():
        line = raw_line.rstrip("\n")
        heading = line.strip().lower()
        if heading.startswith("# "):
            title = heading[2:].strip()
            if title == "prompt":
                if current not in {None, "prompt", "steps", "output"} and extra_buffer:
                    extras.append("\n".join(extra_buffer).strip())
                    extra_buffer = []
                current = "prompt"
                continue
            if title == "steps":
                if current not in {None, "prompt", "steps", "output"} and extra_buffer:
                    extras.append("\n".join(extra_buffer).strip())
                    extra_buffer = []
                current = "steps"
                continue
            if title == "output":
                if current not in {None, "prompt", "steps", "output"} and extra_buffer:
                    extras.append("\n".join(extra_buffer).strip())
                    extra_buffer = []
                current = "output"
                continue
            if extra_buffer:
                extras.append("\n".join(extra_buffer).strip())
                extra_buffer = []
            current = f"extra:{line.strip()}"
            extra_buffer.append(line.strip())
            continue

        if current == "prompt":
            section_lines["prompt"].append(line)
        elif current == "steps":
            section_lines["steps"].append(line)
        elif current == "output":
            section_lines["output"].append(line)
        elif current and current.startswith("extra:"):
            extra_buffer.append(line)

    if extra_buffer:
        extras.append("\n".join(extra_buffer).strip())

    prompt = "\n".join(section_lines["prompt"]).strip()
    for line in section_lines["steps"]:
        item = line.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        if item:
            steps.append(item)
    for line in section_lines["output"]:
        item = line.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        if item:
            output_constraints.append(item)
    return prompt, tuple(steps), tuple(output_constraints), tuple(part for part in extras if part)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_schedule_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip())
    return text


def validate_schedule_text(value: str) -> None:
    text = _normalize_schedule_text(value)
    if text == "毎時":
        return
    m_daily = re.match(r"^毎日\s*([01]?\d|2[0-3]):([0-5]\d)$", text)
    if m_daily:
        return
    m_weekday = re.match(r"^平日\s*([01]?\d|2[0-3]):([0-5]\d)$", text)
    if m_weekday:
        return
    raise JobFormatError("unsupported schedule format")


def compute_next_run_at(*, schedule: str, timezone_name: str, now_utc: datetime | None = None) -> datetime:
    validate_schedule_text(schedule)
    now = now_utc or datetime.now(timezone.utc)
    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)
    text = _normalize_schedule_text(schedule)

    if text == "毎時":
        candidate = local_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return candidate.astimezone(timezone.utc)

    m = re.match(r"^(毎日|平日)\s*([01]?\d|2[0-3]):([0-5]\d)$", text)
    if m is None:
        raise JobFormatError("unsupported schedule format")

    kind = m.group(1)
    hour = int(m.group(2))
    minute = int(m.group(3))

    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if kind == "毎日":
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    # 平日
    for day_offset in range(0, 8):
        maybe = candidate + timedelta(days=day_offset)
        if maybe.weekday() >= 5:
            continue
        if day_offset == 0 and maybe <= local_now:
            continue
        return maybe.astimezone(timezone.utc)

    # fallback (should not happen)
    return (candidate + timedelta(days=1)).astimezone(timezone.utc)


def _validate_job_references(job: JobDefinition) -> JobDefinition:
    available_skills = {skill.name for skill in list_skills()}
    configured_servers = set(list_configured_mcp_servers())

    for skill_name in job.skills:
        if skill_name not in available_skills:
            return replace(job, invalid_reason=f"unknown skill: {skill_name}")

    for server in job.mcp_servers:
        if server not in configured_servers:
            return replace(job, invalid_reason=f"unknown mcp server: {server}")

    for tool_name in job.mcp_tools:
        if "." not in tool_name:
            return replace(job, invalid_reason=f"invalid mcp tool format: {tool_name}")
        server_name, _ = tool_name.split(".", 1)
        if server_name not in configured_servers:
            return replace(job, invalid_reason=f"unknown mcp tool server: {server_name}")

    return replace(job, invalid_reason=None)


def parse_job_file(path: Path, *, default_timezone: str) -> JobDefinition:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    schedule = _normalize_schedule_text(str(frontmatter.get("schedule", "")).strip())
    if not name:
        raise JobFormatError("name is required")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise JobFormatError("name must match [a-z0-9-]+")
    if not description:
        raise JobFormatError("description is required")
    if not schedule:
        raise JobFormatError("schedule is required")
    validate_schedule_text(schedule)

    timezone_name = str(frontmatter.get("timezone", default_timezone)).strip() or default_timezone
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise JobFormatError(f"invalid timezone: {timezone_name}") from exc

    prompt, steps, output_constraints, extra_sections = _parse_sections(body)
    if not prompt:
        raise JobFormatError("# Prompt section is required")

    job = JobDefinition(
        path=path,
        name=name,
        description=description,
        schedule=schedule,
        timezone=timezone_name,
        enabled=bool(frontmatter.get("enabled", True)),
        delivery=str(frontmatter.get("delivery", "announce")).strip() or "announce",
        channel=str(frontmatter.get("channel", "auto")).strip() or "auto",
        mode=str(frontmatter.get("mode", "isolated")).strip() or "isolated",
        skills=tuple(str(x).strip() for x in frontmatter.get("skills", []) if str(x).strip()),
        mcp_servers=tuple(str(x).strip() for x in frontmatter.get("mcp_servers", []) if str(x).strip()),
        mcp_tools=tuple(str(x).strip() for x in frontmatter.get("mcp_tools", []) if str(x).strip()),
        timeout_seconds=int(frontmatter["timeout_seconds"]) if "timeout_seconds" in frontmatter else None,
        max_retries=max(0, int(frontmatter.get("max_retries", 0))),
        retry_backoff=str(frontmatter.get("retry_backoff", "none")).strip() or "none",
        created_by=str(frontmatter.get("created_by", "")).strip() or None,
        created_channel_id=str(frontmatter.get("created_channel_id", "")).strip() or None,
        next_run_at=_parse_iso_datetime(frontmatter.get("next_run_at")),
        last_run_at=_parse_iso_datetime(frontmatter.get("last_run_at")),
        retry_count=max(0, int(frontmatter.get("retry_count", 0))),
        prompt=prompt,
        steps=steps,
        output_constraints=output_constraints,
        extra_sections=extra_sections,
    )

    if job.delivery not in {"announce", "none"}:
        raise JobFormatError("delivery must be announce or none")
    if job.channel != "auto" and not re.fullmatch(r"\d+", job.channel):
        raise JobFormatError("channel must be auto or discord channel id")
    if job.mode not in {"isolated", "main"}:
        raise JobFormatError("mode must be isolated or main")
    if job.retry_backoff not in {"none", "exponential"}:
        raise JobFormatError("retry_backoff must be none or exponential")

    if job.next_run_at is None and job.enabled:
        job.next_run_at = compute_next_run_at(schedule=job.schedule, timezone_name=job.timezone)

    return _validate_job_references(job)


def load_jobs(jobs_dir: Path, *, default_timezone: str) -> tuple[list[JobDefinition], list[str]]:
    jobs: list[JobDefinition] = []
    errors: list[str] = []
    if not jobs_dir.exists() or not jobs_dir.is_dir():
        return jobs, errors

    for path in sorted(jobs_dir.glob("*.md"), key=lambda p: p.name):
        try:
            job = parse_job_file(path, default_timezone=default_timezone)
            jobs.append(job)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return jobs, errors


def _iso_text(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _frontmatter_text(job: JobDefinition) -> str:
    lines = [
        f"name: {job.name}",
        f"description: {job.description}",
        f"schedule: {job.schedule}",
        f"timezone: {job.timezone}",
        f"enabled: {'true' if job.enabled else 'false'}",
        f"delivery: {job.delivery}",
        f"channel: {job.channel}",
        f"mode: {job.mode}",
    ]
    if job.skills:
        lines.append("skills:")
        lines.extend([f"  - {item}" for item in job.skills])
    if job.mcp_servers:
        lines.append("mcp_servers:")
        lines.extend([f"  - {item}" for item in job.mcp_servers])
    if job.mcp_tools:
        lines.append("mcp_tools:")
        lines.extend([f"  - {item}" for item in job.mcp_tools])
    if job.timeout_seconds is not None:
        lines.append(f"timeout_seconds: {job.timeout_seconds}")
    lines.append(f"max_retries: {job.max_retries}")
    lines.append(f"retry_backoff: {job.retry_backoff}")
    if job.created_by:
        lines.append(f"created_by: {job.created_by}")
    if job.created_channel_id:
        lines.append(f"created_channel_id: {job.created_channel_id}")
    next_run = _iso_text(job.next_run_at)
    if next_run:
        lines.append(f"next_run_at: {next_run}")
    last_run = _iso_text(job.last_run_at)
    if last_run:
        lines.append(f"last_run_at: {last_run}")
    lines.append(f"retry_count: {job.retry_count}")
    return "\n".join(lines)


def serialize_job(job: JobDefinition) -> str:
    lines = ["---", _frontmatter_text(job), "---", "", "# Prompt", job.prompt.strip(), ""]
    if job.steps:
        lines.append("# Steps")
        lines.extend([f"- {item}" for item in job.steps])
        lines.append("")
    if job.output_constraints:
        lines.append("# Output")
        lines.extend([f"- {item}" for item in job.output_constraints])
        lines.append("")
    for section in job.extra_sections:
        if not section.strip():
            continue
        lines.append(section.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_job(job: JobDefinition) -> None:
    job.path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_job(job)
    tmp_path = job.path.with_suffix(".md.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(job.path)


def create_job_from_command(
    *,
    jobs_dir: Path,
    name: str,
    description: str,
    prompt: str,
    schedule: str,
    timezone_name: str,
    created_by: str,
    created_channel_id: str,
) -> JobDefinition:
    schedule_text = _normalize_schedule_text(schedule)
    validate_schedule_text(schedule_text)
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise JobFormatError(f"invalid timezone: {timezone_name}") from exc

    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise JobFormatError("name must match [a-z0-9-]+")

    path = jobs_dir / f"{name}.md"
    if path.exists():
        raise JobFormatError(f"job already exists: {name}")

    job = JobDefinition(
        path=path,
        name=name,
        description=description.strip() or name,
        schedule=schedule_text,
        timezone=timezone_name,
        enabled=True,
        delivery="announce",
        channel="auto",
        mode="isolated",
        created_by=created_by,
        created_channel_id=created_channel_id,
        next_run_at=compute_next_run_at(schedule=schedule_text, timezone_name=timezone_name),
        prompt=prompt.strip(),
        max_retries=0,
        retry_backoff="none",
    )
    return _validate_job_references(job)


def find_job(jobs: list[JobDefinition], name: str) -> JobDefinition | None:
    for job in jobs:
        if job.name == name:
            return job
    return None


def natural_schedule_help() -> str:
    return "対応する頻度: 毎時 / 毎日 HH:MM / 平日 HH:MM"


def compute_retry_next_run(*, retry_count: int, now_utc: datetime) -> datetime:
    # 30s -> 60s -> 300s -> 900s -> 3600s
    table = [30, 60, 300, 900, 3600]
    idx = min(max(retry_count - 1, 0), len(table) - 1)
    return now_utc + timedelta(seconds=table[idx])
