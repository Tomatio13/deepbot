from __future__ import annotations

import json
from pathlib import Path

from deepbot.agent.claude_hooks import ClaudeHooksManager


def _write_settings(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_user_prompt_submit_can_block_by_json_continue_false(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "cat >/dev/null; echo '{\"continue\":false,\"stopReason\":\"blocked by policy\"}'",
                            }
                        ]
                    }
                ]
            }
        },
    )

    manager = ClaudeHooksManager.from_settings_paths(
        (str(settings_path),),
        timeout_ms=1000,
        fail_mode="open",
    )

    result = manager.dispatch_user_prompt_submit(session_id="s1", prompt="hello")

    assert result.blocked is True
    assert "blocked by policy" in result.user_message


def test_pre_tool_use_can_block_by_exit_code_2(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "cat >/dev/null; echo 'deny bash' >&2; exit 2",
                            }
                        ],
                    }
                ]
            }
        },
    )

    manager = ClaudeHooksManager.from_settings_paths(
        (str(settings_path),),
        timeout_ms=1000,
        fail_mode="open",
    )

    result = manager.dispatch_pre_tool_use(
        session_id="s1",
        tool_name="shell",
        tool_input={"command": "echo hi"},
    )

    assert result.blocked is True
    assert "deny bash" in result.model_message


def test_user_prompt_submit_collects_additional_context_from_stdout(tmp_path: Path) -> None:
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_settings(
        settings_path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "cat >/dev/null; echo 'extra context from hook'",
                            }
                        ]
                    }
                ]
            }
        },
    )

    manager = ClaudeHooksManager.from_settings_paths(
        (str(settings_path),),
        timeout_ms=1000,
        fail_mode="open",
    )

    result = manager.dispatch_user_prompt_submit(session_id="s1", prompt="hello")

    assert result.blocked is False
    assert "extra context from hook" in result.additional_context
