from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class JobDefinition:
    path: Path
    name: str
    description: str
    schedule: str
    timezone: str
    enabled: bool = True
    delivery: str = "announce"
    channel: str = "auto"
    mode: str = "isolated"
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    timeout_seconds: int | None = None
    max_retries: int = 0
    retry_backoff: str = "none"
    created_by: str | None = None
    created_channel_id: str | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    retry_count: int = 0
    prompt: str = ""
    steps: tuple[str, ...] = ()
    output_constraints: tuple[str, ...] = ()
    extra_sections: tuple[str, ...] = ()
    invalid_reason: str | None = None
    parse_errors: tuple[str, ...] = field(default_factory=tuple)

    def build_execution_prompt(self) -> str:
        lines: list[str] = [self.prompt.strip()]
        if self.steps:
            lines.append("")
            lines.append("手順:")
            for step in self.steps:
                lines.append(f"- {step}")
        if self.output_constraints:
            lines.append("")
            lines.append("出力条件:")
            for item in self.output_constraints:
                lines.append(f"- {item}")
        for section in self.extra_sections:
            section_body = section.strip()
            if not section_body:
                continue
            lines.append("")
            lines.append(section_body)
        return "\n".join(part for part in lines if part is not None).strip()
