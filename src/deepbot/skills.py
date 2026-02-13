from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SKILL_PREFIX_RE = re.compile(
    r"^(?:<@!?\d+>\s*)*(?:[$/])(?P<name>[a-zA-Z0-9_-]+)(?:\s+(?P<rest>.*))?$",
    re.DOTALL,
)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    content: str


def get_skills_dir() -> Path:
    config_dir = os.environ.get("DEEPBOT_CONFIG_DIR", "/app/config").strip() or "/app/config"
    return Path(config_dir).expanduser() / "skills"


def list_skills() -> list[Skill]:
    skills_dir = get_skills_dir()
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []

    skills: list[Skill] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists() or not skill_md.is_file():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_frontmatter(content)
        if not meta:
            continue
        name, description = meta
        skills.append(
            Skill(
                name=name,
                description=description,
                path=skill_md,
                content=content.strip(),
            )
        )
    return skills


def extract_selected_skill(user_text: str, skills: list[Skill]) -> tuple[Skill | None, str]:
    match = SKILL_PREFIX_RE.match(user_text.strip())
    if not match:
        return None, user_text

    skill_name = match.group("name")
    rest = (match.group("rest") or "").strip()
    skill = next((s for s in skills if s.name == skill_name), None)
    if skill is None:
        return None, user_text
    return skill, rest


def build_skills_discovery_prompt(skills: list[Skill]) -> str | None:
    if not skills:
        return None
    lines = [
        "## Skills (Discovery)",
        "If a skill is relevant, follow its SKILL.md instructions.",
        "Available skills:",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description} (path: {skill.path})")
    return "\n".join(lines)


def build_selected_skill_prompt(skill: Skill) -> str:
    return (
        "## Selected Skill\n"
        f"Name: {skill.name}\n"
        f"Path: {skill.path}\n\n"
        "<skill_instructions>\n"
        f"{skill.content}\n"
        "</skill_instructions>"
    )


def _parse_frontmatter(content: str) -> tuple[str, str] | None:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return None
    frontmatter = match.group(1)
    values: dict[str, str] = {}
    for line in frontmatter.splitlines():
        m = re.match(r"^(\w+):\s*(.+)$", line.strip())
        if not m:
            continue
        values[m.group(1)] = m.group(2).strip()
    name = values.get("name", "").strip()
    description = values.get("description", "").strip()
    if not name or not description:
        return None
    return name, description
