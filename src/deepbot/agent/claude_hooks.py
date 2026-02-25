from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookDecision:
    continue_run: bool = True
    stop_reason: str = ""
    suppress_output: bool = False
    system_message: str = ""
    permission_decision: str = ""
    permission_decision_reason: str = ""
    decision: str = ""
    additional_context: str = ""


@dataclass(frozen=True)
class HookExecution:
    event_name: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    json_decision: HookDecision | None


@dataclass(frozen=True)
class HookRunResult:
    blocked: bool
    user_message: str
    model_message: str
    additional_context: str = ""


@dataclass(frozen=True)
class _HookCommand:
    command: str


@dataclass(frozen=True)
class _HookMatcher:
    matcher: str
    hooks: tuple[_HookCommand, ...]


class ClaudeHooksManager:
    """Claude Code hooks compatibility runner for deepbot runtime."""

    _SUPPORTED_EVENTS = {
        "PreToolUse",
        "PostToolUse",
        "Notification",
        "UserPromptSubmit",
        "Stop",
        "SubagentStop",
        "PreCompact",
        "SessionStart",
        "SessionEnd",
    }

    _TOOL_NAME_MAP = {
        "shell": "Bash",
        "file_read": "Read",
        "file_write": "Write",
        "editor": "Edit",
        "http_request": "WebFetch",
    }

    def __init__(
        self,
        *,
        matchers_by_event: dict[str, tuple[_HookMatcher, ...]],
        timeout_ms: int,
        fail_mode: str,
        settings_files: tuple[str, ...],
    ) -> None:
        self._matchers_by_event = matchers_by_event
        self._timeout_seconds = timeout_ms / 1000.0
        self._fail_mode = fail_mode
        self._settings_files = settings_files
        self._seen_sessions: set[str] = set()

    @classmethod
    def from_settings_paths(
        cls,
        paths: tuple[str, ...],
        *,
        timeout_ms: int,
        fail_mode: str,
        cwd: Path | None = None,
    ) -> "ClaudeHooksManager":
        resolved_paths = cls._resolve_settings_paths(paths, cwd=cwd)
        matchers_by_event: dict[str, list[_HookMatcher]] = {}

        for path in resolved_paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to parse hooks settings file: %s (%s)", path, exc)
                continue
            hooks_root = raw.get("hooks")
            if not isinstance(hooks_root, dict):
                continue

            for event_name, entries in hooks_root.items():
                if event_name not in cls._SUPPORTED_EVENTS:
                    continue
                parsed = cls._parse_matchers(event_name, entries)
                if not parsed:
                    continue
                matchers_by_event.setdefault(event_name, []).extend(parsed)

        frozen = {
            event: tuple(items)
            for event, items in matchers_by_event.items()
        }
        return cls(
            matchers_by_event=frozen,
            timeout_ms=timeout_ms,
            fail_mode=fail_mode,
            settings_files=tuple(str(p) for p in resolved_paths if p.exists() and p.is_file()),
        )

    @staticmethod
    def _resolve_settings_paths(paths: tuple[str, ...], *, cwd: Path | None = None) -> tuple[Path, ...]:
        root = cwd or Path.cwd()
        resolved: list[Path] = []
        for raw in paths:
            text = raw.strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                path = (root / path).resolve()
            resolved.append(path)
        return tuple(resolved)

    @classmethod
    def _parse_matchers(cls, event_name: str, entries: Any) -> list[_HookMatcher]:
        if not isinstance(entries, list):
            return []

        parsed: list[_HookMatcher] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            matcher = "*"
            hooks_payload: Any = None

            if event_name in {"PreToolUse", "PostToolUse"}:
                matcher = str(entry.get("matcher", "*") or "*")
                hooks_payload = entry.get("hooks")
            else:
                hooks_payload = entry.get("hooks", entry)

            hook_commands = cls._parse_hook_commands(hooks_payload)
            if not hook_commands:
                continue
            parsed.append(_HookMatcher(matcher=matcher, hooks=tuple(hook_commands)))
        return parsed

    @staticmethod
    def _parse_hook_commands(payload: Any) -> list[_HookCommand]:
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []

        hooks: list[_HookCommand] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip().lower() != "command":
                continue
            command = str(item.get("command", "")).strip()
            if not command:
                continue
            hooks.append(_HookCommand(command=command))
        return hooks

    @property
    def loaded_files(self) -> tuple[str, ...]:
        return self._settings_files

    def has_any_hooks(self) -> bool:
        return any(self._matchers_by_event.values())

    def dispatch_session_start(self, *, session_id: str, prompt: str) -> HookRunResult:
        if session_id in self._seen_sessions:
            return HookRunResult(blocked=False, user_message="", model_message="")
        self._seen_sessions.add(session_id)
        payload = {
            "session_id": session_id,
            "cwd": str(Path.cwd()),
            "hook_event_name": "SessionStart",
            "source": "startup",
            "prompt": prompt,
        }
        return self._dispatch_event("SessionStart", payload=payload)

    def dispatch_user_prompt_submit(self, *, session_id: str, prompt: str) -> HookRunResult:
        payload = {
            "session_id": session_id,
            "cwd": str(Path.cwd()),
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
        return self._dispatch_event("UserPromptSubmit", payload=payload)

    def dispatch_pre_tool_use(self, *, session_id: str, tool_name: str, tool_input: Any) -> HookRunResult:
        mapped = self._TOOL_NAME_MAP.get(tool_name, tool_name)
        payload = {
            "session_id": session_id,
            "cwd": str(Path.cwd()),
            "hook_event_name": "PreToolUse",
            "tool_name": mapped,
            "tool_original_name": tool_name,
            "tool_input": tool_input,
        }
        return self._dispatch_event("PreToolUse", payload=payload, matcher_target=mapped)

    def dispatch_post_tool_use(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_input: Any,
        tool_response: Any,
    ) -> HookRunResult:
        mapped = self._TOOL_NAME_MAP.get(tool_name, tool_name)
        payload = {
            "session_id": session_id,
            "cwd": str(Path.cwd()),
            "hook_event_name": "PostToolUse",
            "tool_name": mapped,
            "tool_original_name": tool_name,
            "tool_input": tool_input,
            "tool_response": tool_response,
        }
        return self._dispatch_event("PostToolUse", payload=payload, matcher_target=mapped)

    def dispatch_stop(self, *, session_id: str, response_text: str) -> HookRunResult:
        payload = {
            "session_id": session_id,
            "cwd": str(Path.cwd()),
            "hook_event_name": "Stop",
            "stop_hook_active": True,
            "response": response_text,
        }
        return self._dispatch_event("Stop", payload=payload)

    def _dispatch_event(
        self,
        event_name: str,
        *,
        payload: dict[str, Any],
        matcher_target: str = "*",
    ) -> HookRunResult:
        matchers = self._matchers_by_event.get(event_name, ())
        if not matchers:
            return HookRunResult(blocked=False, user_message="", model_message="")

        blocked = False
        user_messages: list[str] = []
        model_messages: list[str] = []
        context_chunks: list[str] = []

        for matcher in matchers:
            if event_name in {"PreToolUse", "PostToolUse"} and not self._matches(matcher.matcher, matcher_target):
                continue
            for hook in matcher.hooks:
                execution = self._run_command(
                    event_name=event_name,
                    command=hook.command,
                    payload=payload,
                )
                decision = execution.json_decision

                if execution.stdout and event_name in {"UserPromptSubmit", "SessionStart"}:
                    context_chunks.append(execution.stdout)

                if decision is not None and decision.additional_context:
                    context_chunks.append(decision.additional_context)

                local_blocked, user_msg, model_msg = self._evaluate_outcome(
                    event_name=event_name,
                    execution=execution,
                )
                if user_msg:
                    user_messages.append(user_msg)
                if model_msg:
                    model_messages.append(model_msg)
                if local_blocked:
                    blocked = True

        return HookRunResult(
            blocked=blocked,
            user_message="\n".join(part for part in user_messages if part).strip(),
            model_message="\n".join(part for part in model_messages if part).strip(),
            additional_context="\n".join(part for part in context_chunks if part).strip(),
        )

    @staticmethod
    def _matches(matcher: str, target: str) -> bool:
        if matcher == "*":
            return True
        if fnmatchcase(target, matcher):
            return True
        try:
            if re.search(matcher, target):
                return True
        except re.error:
            return target == matcher
        return False

    def _run_command(self, *, event_name: str, command: str, payload: dict[str, Any]) -> HookExecution:
        stdin_text = json.dumps(payload, ensure_ascii=False)
        env = dict(os.environ)
        env["CLAUDE_HOOK_EVENT_NAME"] = event_name
        try:
            completed = subprocess.run(
                command,
                input=stdin_text,
                capture_output=True,
                text=True,
                shell=True,
                timeout=self._timeout_seconds,
                env=env,
                check=False,
            )
            stdout = (completed.stdout or "").strip()
            stderr = (completed.stderr or "").strip()
            decision = self._parse_json_decision(stdout)
            return HookExecution(
                event_name=event_name,
                command=command,
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                json_decision=decision,
            )
        except subprocess.TimeoutExpired:
            msg = f"Hook timed out after {self._timeout_seconds:.3f}s"
            if self._fail_mode == "closed":
                return HookExecution(
                    event_name=event_name,
                    command=command,
                    exit_code=2,
                    stdout="",
                    stderr=msg,
                    json_decision=None,
                )
            return HookExecution(
                event_name=event_name,
                command=command,
                exit_code=1,
                stdout="",
                stderr=msg,
                json_decision=None,
            )
        except Exception as exc:
            msg = f"Hook failed: {exc}"
            if self._fail_mode == "closed":
                return HookExecution(
                    event_name=event_name,
                    command=command,
                    exit_code=2,
                    stdout="",
                    stderr=msg,
                    json_decision=None,
                )
            return HookExecution(
                event_name=event_name,
                command=command,
                exit_code=1,
                stdout="",
                stderr=msg,
                json_decision=None,
            )

    @staticmethod
    def _parse_json_decision(stdout: str) -> HookDecision | None:
        if not stdout:
            return None
        text = stdout.strip()
        if not text.startswith("{"):
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return HookDecision(
            continue_run=bool(payload.get("continue", True)),
            stop_reason=str(payload.get("stopReason", "") or "").strip(),
            suppress_output=bool(payload.get("suppressOutput", False)),
            system_message=str(payload.get("systemMessage", "") or "").strip(),
            permission_decision=str(payload.get("permissionDecision", "") or "").strip().lower(),
            permission_decision_reason=str(payload.get("permissionDecisionReason", "") or "").strip(),
            decision=str(payload.get("decision", "") or "").strip().lower(),
            additional_context=str(payload.get("additionalContext", "") or "").strip(),
        )

    def _evaluate_outcome(
        self,
        *,
        event_name: str,
        execution: HookExecution,
    ) -> tuple[bool, str, str]:
        decision = execution.json_decision
        blocked = False
        user_message = ""
        model_message = ""

        if decision is not None:
            if not decision.continue_run:
                blocked = True
                user_message = decision.stop_reason or decision.system_message or execution.stderr
            elif event_name == "PreToolUse" and decision.permission_decision == "deny":
                blocked = True
                model_message = (
                    decision.permission_decision_reason
                    or decision.stop_reason
                    or decision.system_message
                    or execution.stderr
                )
            elif event_name == "PostToolUse" and decision.decision == "block":
                blocked = True
                model_message = decision.stop_reason or decision.system_message or execution.stderr

        if execution.exit_code == 2:
            blocked = True
            if event_name == "UserPromptSubmit":
                user_message = execution.stderr or user_message
            elif event_name == "PreToolUse":
                model_message = execution.stderr or model_message
            elif event_name == "PostToolUse":
                model_message = execution.stderr or model_message
            else:
                user_message = execution.stderr or user_message
        elif execution.exit_code != 0:
            if execution.stderr:
                user_message = user_message or execution.stderr

        return blocked, user_message.strip(), model_message.strip()
