from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

from deepbot.config import ConfigError


@dataclass(frozen=True)
class ClaudeSubagentSettings:
    command: str
    workdir: str
    timeout_seconds: int
    model: str | None
    skip_permissions: bool
    transport: str
    sidecar_url: str
    sidecar_token: str


def _run_via_direct(
    settings: ClaudeSubagentSettings,
    *,
    task: str,
    resume_session_id: str | None,
) -> dict[str, Any]:
    args: list[str] = [settings.command, "-p", "--output-format", "json"]
    if settings.skip_permissions:
        args.append("--dangerously-skip-permissions")
    if settings.model:
        args.extend(["--model", settings.model])
    if resume_session_id:
        session_id = resume_session_id.strip()
        if session_id:
            args.extend(["--resume", session_id])

    args.extend(
        [
            "--append-system-prompt",
            "You are a delegated sub-agent. Focus on executable implementation details and return concise results.",
            task,
        ]
    )

    try:
        completed = subprocess.run(
            args,
            cwd=settings.workdir,
            text=True,
            capture_output=True,
            timeout=settings.timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"claude_subagent failed: command not found: {settings.command}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"claude_subagent timed out after {settings.timeout_seconds}s"
        ) from exc

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    if completed.returncode != 0:
        detail = stderr or stdout or "unknown error"
        raise RuntimeError(
            f"claude_subagent failed with exit code {completed.returncode}: {detail}"
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude_subagent returned non-JSON output: {stdout[:4000]}") from exc


def _run_via_sidecar(
    settings: ClaudeSubagentSettings,
    *,
    task: str,
    resume_session_id: str | None,
) -> dict[str, Any]:
    body = {
        "task": task,
        "resume_session_id": resume_session_id or "",
        "model": settings.model or "",
        "skip_permissions": settings.skip_permissions,
    }
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if settings.sidecar_token:
        headers["Authorization"] = f"Bearer {settings.sidecar_token}"
    req = urlrequest.Request(settings.sidecar_url, data=data, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=settings.timeout_seconds + 5) as resp:
            raw = resp.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"claude_subagent sidecar HTTP {exc.code}: {detail[:1000]}"
        ) from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"claude_subagent sidecar unreachable: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude_subagent sidecar returned non-JSON output: {raw[:4000]}"
        ) from exc


def build_claude_subagent_tool(settings: ClaudeSubagentSettings) -> Callable[..., Any]:
    try:
        from strands import tool
    except Exception as exc:  # pragma: no cover
        raise ConfigError(f"Failed to load strands tool decorator for claude_subagent: {exc}") from exc

    @tool
    def claude_subagent(task: str, resume_session_id: str | None = None) -> dict[str, Any]:
        """
        Execute Claude Code CLI as a sub-agent for code-heavy tasks.

        Args:
            task: The concrete task/instruction for Claude Code.
            resume_session_id: Optional Claude session id to continue the same context.
        """
        text = task.strip()
        if not text:
            raise ValueError("claude_subagent rejected: task must not be empty")

        if settings.transport == "sidecar":
            payload = _run_via_sidecar(
                settings,
                task=text,
                resume_session_id=resume_session_id,
            )
        else:
            payload = _run_via_direct(
                settings,
                task=text,
                resume_session_id=resume_session_id,
            )

        is_error = bool(payload.get("is_error"))
        if is_error:
            raise RuntimeError(
                f"claude_subagent returned error: {str(payload.get('result', '')).strip()}"
            )

        result = {
            "result": str(payload.get("result", "")),
            "session_id": str(payload.get("session_id", "")),
            "duration_ms": payload.get("duration_ms"),
            "total_cost_usd": payload.get("total_cost_usd"),
        }
        return {
            "status": "success",
            "content": [{"text": json.dumps(result, ensure_ascii=False)}],
        }

    return claude_subagent
