from __future__ import annotations

import os
from dataclasses import dataclass

from .normalizer import normalize_input
from .rules import RULES


@dataclass(frozen=True)
class DefenderSettings:
    enabled: bool = True
    default_mode: str = "warn"
    block_threshold: float = 0.95
    warn_threshold: float = 0.35
    sanitize_mode: str = "full-redact"

    @classmethod
    def from_env(cls) -> "DefenderSettings":
        enabled = os.environ.get("DEFENDER_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        default_mode = os.environ.get("DEFENDER_DEFAULT_MODE", "warn").strip().lower() or "warn"
        block_threshold = float(os.environ.get("DEFENDER_BLOCK_THRESHOLD", "0.95"))
        warn_threshold = float(os.environ.get("DEFENDER_WARN_THRESHOLD", "0.35"))
        sanitize_mode = os.environ.get("DEFENDER_SANITIZE_MODE", "full-redact").strip().lower() or "full-redact"
        return cls(
            enabled=enabled,
            default_mode=default_mode,
            block_threshold=block_threshold,
            warn_threshold=warn_threshold,
            sanitize_mode=sanitize_mode,
        )


@dataclass(frozen=True)
class DefenderDecision:
    action: str
    score: float
    categories: tuple[str, ...]
    normalized_text: str
    redacted_text: str | None


class PromptInjectionDefender:
    FULL_REDACT_TEXT = "[REDACTED_BY_SECURITY_POLICY]"

    def __init__(self, settings: DefenderSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def evaluate(self, text: str) -> DefenderDecision:
        normalized = normalize_input(text)
        matched = [rule for rule in RULES if rule.pattern.search(normalized)]
        if not matched:
            return DefenderDecision(
                action="pass",
                score=0.0,
                categories=(),
                normalized_text=normalized,
                redacted_text=None,
            )

        max_severity = max(rule.severity for rule in matched)
        score = min(1.0, (sum(rule.severity for rule in matched) / 9.0))
        categories = tuple(dict.fromkeys(rule.category for rule in matched))

        action = "log"
        if score >= self._settings.block_threshold or max_severity >= 3:
            if self._settings.default_mode == "block":
                action = "block"
            elif self._settings.default_mode == "sanitize":
                action = "sanitize"
            else:
                action = "warn"
        elif score >= self._settings.warn_threshold:
            action = "warn"

        redacted: str | None = None
        if action == "sanitize" and self._settings.sanitize_mode == "full-redact":
            redacted = self.FULL_REDACT_TEXT

        return DefenderDecision(
            action=action,
            score=score,
            categories=categories,
            normalized_text=normalized,
            redacted_text=redacted,
        )
