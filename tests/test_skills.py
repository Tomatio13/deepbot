from __future__ import annotations

from pathlib import Path

from deepbot.skills import (
    build_selected_skill_prompt,
    extract_selected_skill,
    list_skills,
)


def _write_skill(root: Path, name: str, description: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n",
        encoding="utf-8",
    )


def test_list_skills_from_config_dir(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    skills_dir = config_dir / "skills"
    _write_skill(skills_dir, "writer", "write better text")
    _write_skill(skills_dir, "reviewer", "review docs")

    monkeypatch.setenv("DEEPBOT_CONFIG_DIR", str(config_dir))

    skills = list_skills()
    names = [s.name for s in skills]
    assert names == ["reviewer", "writer"]


def test_extract_selected_skill_by_prefix(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    skills_dir = config_dir / "skills"
    _write_skill(skills_dir, "writer", "write better text")

    monkeypatch.setenv("DEEPBOT_CONFIG_DIR", str(config_dir))
    skills = list_skills()

    selected, cleaned = extract_selected_skill("$writer make this concise", skills)
    assert selected is not None
    assert selected.name == "writer"
    assert cleaned == "make this concise"

    prompt = build_selected_skill_prompt(selected)
    assert "Selected Skill" in prompt
    assert "writer" in prompt

