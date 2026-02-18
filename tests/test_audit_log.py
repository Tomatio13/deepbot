from __future__ import annotations

import json

from deepbot.audit_log import create_audit_logger


def _read_jsonl(path):
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_create_audit_logger_writes_session_meta(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT", "1")
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT_DIR", str(tmp_path))

    logger = create_audit_logger()
    assert logger is not None
    records = _read_jsonl(logger.path)
    assert records[0]["type"] == "session_meta"
    assert records[0]["payload"]["originator"] == "deepbot"


def test_create_audit_logger_returns_none_when_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT", "0")
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT_DIR", str(tmp_path))
    assert create_audit_logger() is None


def test_audit_logger_appends_user_assistant_and_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT", "1")
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT_DIR", str(tmp_path))
    logger = create_audit_logger()
    assert logger is not None

    logger.log_user_message(
        session_id="s1",
        author_id="u1",
        message_id="m1",
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
        content="hello",
        attachment_count=0,
    )
    logger.log_assistant_message(
        session_id="s1",
        content="world",
        image_count=1,
        has_ui_intent=False,
        has_surface_directives=True,
    )
    logger.log_event(event="agent_execution_failed", session_id="s1", data={"reason": "timeout"})

    records = _read_jsonl(logger.path)
    assert [item["type"] for item in records] == [
        "session_meta",
        "response_item",
        "response_item",
        "event",
    ]
    assert records[1]["payload"]["role"] == "user"
    assert records[2]["payload"]["role"] == "assistant"
    assert records[3]["payload"]["event"] == "agent_execution_failed"


def test_audit_logger_appends_function_call_and_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT", "1")
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT_DIR", str(tmp_path))
    logger = create_audit_logger()
    assert logger is not None

    logger.log_function_call(
        session_id="s1",
        name="shell",
        arguments={"cmd": "ls -la"},
        call_id="c1",
    )
    logger.log_function_call_output(
        session_id="s1",
        call_id="c1",
        output={"stdout": "ok"},
        duration_ms=12,
        tool_name="shell",
    )

    records = _read_jsonl(logger.path)
    assert records[1]["payload"]["type"] == "function_call"
    assert records[1]["payload"]["call_id"] == "c1"
    assert records[2]["payload"]["type"] == "function_call_output"
    assert records[2]["payload"]["call_id"] == "c1"
    assert records[2]["payload"]["duration_ms"] == 12


def test_audit_logger_masks_auth_and_secret_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT", "1")
    monkeypatch.setenv("DEEPBOT_TRANSCRIPT_DIR", str(tmp_path))
    logger = create_audit_logger()
    assert logger is not None

    logger.log_user_message(
        session_id="s1",
        author_id="u1",
        message_id="m1",
        guild_id="g1",
        channel_id="c1",
        thread_id=None,
        content="/auth my-secret-passphrase",
        attachment_count=0,
    )
    logger.log_function_call(
        session_id="s1",
        name="http_request",
        arguments={"headers": {"Authorization": "Bearer abc123"}, "api_key": "raw-key"},
        call_id="c2",
    )

    records = _read_jsonl(logger.path)
    user_text = records[1]["payload"]["content"][0]["text"]
    assert "/auth [REDACTED]" in user_text
    call_args = records[2]["payload"]["arguments"]
    assert "[REDACTED]" in call_args
    assert "abc123" not in call_args
    assert "raw-key" not in call_args
