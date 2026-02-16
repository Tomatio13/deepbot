from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH_RE = re.compile(
    "[\u200b\u200c\u200d\u2060\ufeff\u180e\u00ad\u034f\u061c\u2061\u2062\u2063]"
)

_FULLWIDTH_MAP = {
    ord("＜"): "<",
    ord("＞"): ">",
    ord("［"): "[",
    ord("］"): "]",
    ord("｛"): "{",
    ord("｝"): "}",
}


def strip_zero_width(text: str) -> str:
    return _ZERO_WIDTH_RE.sub("", text)


def fold_fullwidth(text: str) -> str:
    return text.translate(_FULLWIDTH_MAP)


def normalize_input(text: str) -> str:
    normalized = strip_zero_width(text)
    normalized = fold_fullwidth(normalized)
    normalized = unicodedata.normalize("NFKC", normalized)
    normalized = "".join(
        ch
        for ch in normalized
        if ch in {"\n", "\r", "\t"} or not unicodedata.category(ch).startswith("C")
    )
    return normalized


def sanitize_for_prompt(text: str) -> str:
    return "".join(
        ch
        for ch in text
        if ch in {"\n", "\r", "\t"} or not unicodedata.category(ch).startswith("C")
    )
