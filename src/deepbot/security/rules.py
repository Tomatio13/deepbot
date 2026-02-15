from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    severity: int
    pattern: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


RULES: tuple[Rule, ...] = (
    Rule("system_tag", "structural_injection", 3, _compile(r"</?\s*system\s*>")),
    Rule("role_hijack", "structural_injection", 3, _compile(r"</user>\s*<system>|\[from:\s*system\]")),
    Rule("system_update", "structural_injection", 3, _compile(r"\[\s*system\s+update")),
    Rule("ignore_previous", "instruction_override", 3, _compile(r"ignore (all )?(previous|prior)")),
    Rule("new_rules", "instruction_override", 3, _compile(r"(new|override)\s+(rule|instruction)")),
    Rule("dan_jailbreak", "instruction_override", 3, _compile(r"\byou are now dan\b|no restrictions")),
    Rule("jailbreak", "instruction_override", 3, _compile(r"jailbreak|bypass (all )?safety")),
    Rule("tool_injection", "indirect_injection", 2, _compile(r"(tool|search) result:?.*ignore")),
    Rule("boundary_spoof", "indirect_injection", 2, _compile(r"begin (system|developer) message")),
    Rule("developer_mode", "social_engineering", 2, _compile(r"developer mode|i'?m (the )?(admin|owner|developer)")),
    Rule("urgency_manipulation", "social_engineering", 2, _compile(r"urgent|emergency|you will be fired|immediately")),
    Rule("prompt_leak", "payload_patterns", 3, _compile(r"reveal.*(system prompt|api key|token|secrets?)")),
    Rule("dangerous_command", "payload_patterns", 3, _compile(r"\b(rm -rf|curl.+\|.+sh|chmod 777|sudo)\b")),
    Rule("base64_payload", "payload_patterns", 2, _compile(r"base64|decode this payload")),
    Rule("jp_ignore", "multilingual", 3, _compile(r"(すべて|全て|全部|今まで).{0,8}(指示|命令|ルール).{0,8}(無視|忘れ|破棄)")),
    Rule("jp_role_change", "multilingual", 3, _compile(r"(あなたは|君は).{0,8}(今から|これから).{0,20}(管理者|開発者|dan)")),
    Rule("zh_ignore", "multilingual", 3, _compile(r"(忽略|无视).{0,10}(之前|以上).{0,10}(指令|规则)")),
    Rule("ko_ignore", "multilingual", 3, _compile(r"(이전|모든).{0,8}(지시|규칙).{0,8}(무시|잊어)")),
    Rule("es_ignore", "multilingual", 3, _compile(r"ignora(r)? todas? las instrucciones anteriores")),
    Rule("fr_ignore", "multilingual", 3, _compile(r"ignore(r)? toutes? les instructions précédentes")),
    Rule("de_ignore", "multilingual", 3, _compile(r"ignoriere alle vorherigen anweisungen")),
    Rule("ru_ignore", "multilingual", 3, _compile(r"игнорируй(те)? все предыдущие инструкции")),
    Rule("pt_ignore", "multilingual", 3, _compile(r"ignore todas as instruções anteriores")),
    Rule("ar_ignore", "multilingual", 3, _compile(r"تجاهل كل التعليمات السابقة")),
    Rule("reverse_injection", "reverse_injection", 2, _compile(r"(防御|セキュリティ).{0,20}(キーワード|ルール|閾値|回避)")),
)
