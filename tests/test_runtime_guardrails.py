from __future__ import annotations

import pytest

from deepbot.agent.runtime import (
    _build_guarded_file_read_tool,
    _is_path_allowed,
    _validate_shell_command_with_srt,
)


def test_shell_command_validator_accepts_srt_wrapped_command() -> None:
    _validate_shell_command_with_srt(
        'srt --settings /app/config/srt-settings.json -c "ls -la"',
        "/app/config/srt-settings.json",
        ("/app/forbidden",),
    )


def test_shell_command_validator_rejects_non_srt_command() -> None:
    with pytest.raises(ValueError, match="must start"):
        _validate_shell_command_with_srt(
            "ls -la",
            "/app/config/srt-settings.json",
            ("/app",),
        )


def test_shell_command_validator_rejects_wrong_settings_path() -> None:
    with pytest.raises(ValueError, match="must start"):
        _validate_shell_command_with_srt(
            'srt --settings /tmp/custom.json -c "ls -la"',
            "/app/config/srt-settings.json",
            ("/app",),
        )


def test_shell_command_validator_rejects_denied_path_reference() -> None:
    with pytest.raises(ValueError, match="denied path prefixes"):
        _validate_shell_command_with_srt(
            'srt --settings /app/config/srt-settings.json -c "ls -la /app/config"',
            "/app/config/srt-settings.json",
            ("/app",),
        )


def test_is_path_allowed_accepts_child_path() -> None:
    assert _is_path_allowed("/workspace/docs/readme.md", ("/workspace",)) is True


def test_is_path_allowed_rejects_path_outside_roots() -> None:
    assert _is_path_allowed("/app/README.md", ("/workspace",)) is False


def test_guarded_file_read_rejects_path_outside_roots(tmp_path) -> None:
    guarded = _build_guarded_file_read_tool(allowed_roots=(str(tmp_path / "allowed"),))
    with pytest.raises(ValueError, match="file_read rejected"):
        guarded(path=str(tmp_path / "blocked.txt"))
