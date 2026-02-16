from __future__ import annotations

from deepbot.security.normalizer import normalize_input, sanitize_for_prompt


def test_normalize_input_removes_control_and_format_chars() -> None:
    text = "abc\u202e\u0007def\u200b"
    assert normalize_input(text) == "abcdef"


def test_sanitize_for_prompt_removes_control_and_format_chars() -> None:
    text = "path\u202e/evil\u0000name"
    assert sanitize_for_prompt(text) == "path/evilname"
