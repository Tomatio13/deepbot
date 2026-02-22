from __future__ import annotations

import json
import subprocess
import sys
from types import ModuleType

import pytest

from deepbot.agent.claude_subagent_tool import (
    ClaudeSubagentSettings,
    build_claude_subagent_tool,
)


@pytest.fixture(autouse=True)
def _fake_strands_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_strands = ModuleType("strands")
    fake_strands.tool = lambda fn: fn
    monkeypatch.setitem(sys.modules, "strands", fake_strands)


def test_claude_subagent_builds_expected_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):
        captured["args"] = args[0]
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "result": "done",
                    "session_id": "sess-1",
                    "duration_ms": 100,
                    "total_cost_usd": 0.01,
                    "is_error": False,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    tool = build_claude_subagent_tool(
        ClaudeSubagentSettings(
            command="claude",
            workdir="/workspace/bot-rw",
            timeout_seconds=30,
            model="sonnet",
            skip_permissions=True,
            transport="direct",
            sidecar_url="http://claude-runner:8787/v1/run",
            sidecar_token="",
        )
    )
    result = tool(task="fix bug", resume_session_id="session-xyz")

    command = captured["args"]
    assert isinstance(command, list)
    assert command[0] == "claude"
    assert "--dangerously-skip-permissions" in command
    assert "--resume" in command
    assert "session-xyz" in command
    assert "--model" in command
    assert "sonnet" in command
    assert command[-1] == "fix bug"
    assert captured["cwd"] == "/workspace/bot-rw"

    assert result["status"] == "success"
    payload = json.loads(result["content"][0]["text"])
    assert payload["result"] == "done"
    assert payload["session_id"] == "sess-1"


def test_claude_subagent_raises_on_non_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="not-json",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)

    tool = build_claude_subagent_tool(
        ClaudeSubagentSettings(
            command="claude",
            workdir="/workspace/bot-rw",
            timeout_seconds=30,
            model=None,
            skip_permissions=False,
            transport="direct",
            sidecar_url="http://claude-runner:8787/v1/run",
            sidecar_token="",
        )
    )

    with pytest.raises(RuntimeError, match="non-JSON"):
        tool(task="hello")


def test_claude_subagent_sidecar_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        def __init__(self, payload: dict[str, object]) -> None:
            self._body = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["timeout"] = timeout
        return _Resp(
            {
                "result": "ok-from-sidecar",
                "session_id": "s-sidecar",
                "duration_ms": 10,
                "total_cost_usd": 0.0,
                "is_error": False,
            }
        )

    monkeypatch.setattr("deepbot.agent.claude_subagent_tool.urlrequest.urlopen", _fake_urlopen)

    tool = build_claude_subagent_tool(
        ClaudeSubagentSettings(
            command="claude",
            workdir="/workspace/bot-rw",
            timeout_seconds=30,
            model="sonnet",
            skip_permissions=False,
            transport="sidecar",
            sidecar_url="http://claude-runner:8787/v1/run",
            sidecar_token="secret-token",
        )
    )
    result = tool(task="do task", resume_session_id="session-1")
    payload = json.loads(result["content"][0]["text"])

    assert captured["url"] == "http://claude-runner:8787/v1/run"
    assert captured["auth"] == "Bearer secret-token"
    assert payload["result"] == "ok-from-sidecar"
