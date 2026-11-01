"""LLM 전송 전 개인정보 처리 (design.md §6, ADR-0014).

1. 민감정보·보호 속성 줄을 통째로 버린다: 생년·나이, 성별, 병역, 장애·건강, 혼인, 종교, 가족, 주소.
2. 이름·이메일·전화·학교명·GitHub URL을 토큰으로 바꾼다. 원래 값은 매핑에만 남는다.

이렇게 하면 이름·성별·학교·나이만 다른 이력서는 LLM에 **같은 텍스트**로 전달된다.
반사실 공정성이 지시문이 아니라 구조로 보장된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SENSITIVE_LINE = re.compile(
    r"(\d{4}\s*년\s*생|만\s*\d{1,2}\s*세|생년월일|나이\s*[:：]|성별|병역|군필|미필|군\s*면제|"
    r"장애\s*(인|등급|여부|유무|정도)|장애\s*[:：]|보훈|혼인|기혼|미혼|종교|가족\s*관계|주소\s*[:：]|(^|[\s/|])(남|여|남성|여성)\s*$)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(\+82[\s-]?)?0?1[016789][\s.-]?\d{3,4}[\s.-]?\d{4}")
_GITHUB_REPO = re.compile(
    r"(?:https?://)?github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?=[\s)\]>,]|\.git|$)"
)
_GITHUB_PROFILE = re.compile(r"(?:https?://)?github\.com/([A-Za-z0-9-]+)(?![/A-Za-z0-9-])")
_SCHOOL = re.compile(
    r"[가-힣A-Za-z]+(?:대학교|대학원|대학|고등학교)"
    r"|[A-Z][A-Za-z.&\s]{1,40}?(?:University|College|Institute of Technology)"
)
_KOREAN_NAME_LINE = re.compile(r"^\s*([가-힣]{2,4})\s*$")
_LATIN_NAME_LINE = re.compile(r"^\s*([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})\s*$")


@dataclass
class MaskResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # 토큰 → 원래 값
    repo_urls: dict[str, str] = field(default_factory=dict)  # [REPO_n] → https://github.com/o/r
    discarded_sensitive_lines: int = 0

    def unmask(self, text: str) -> str:
        for token, original in self.mapping.items():
            text = text.replace(token, original)
        return text


def detect_name(text: str) -> str | None:
    """이력서 첫 줄이 이름 형태면 이름으로 본다 (계정 이름을 모를 때의 보조 수단)."""
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _KOREAN_NAME_LINE.match(line) or _LATIN_NAME_LINE.match(line)
        return m.group(1) if m else None
    return None


def mask_pii(text: str, known_names: list[str] | None = None) -> MaskResult:
    result = MaskResult(text="")
    kept = []
    for line in text.splitlines():
        if _SENSITIVE_LINE.search(line):
            result.discarded_sensitive_lines += 1
            continue
        kept.append(line)
    masked = "\n".join(kept)

    names = [n for n in (known_names or []) if n]
    if not names and (detected := detect_name(text)):
        names = [detected]
    for name in sorted(set(names), key=len, reverse=True):
        masked = masked.replace(name, "[NAME]")
        result.mapping["[NAME]"] = name

    counters: dict[str, int] = {}
    seen: dict[str, str] = {}

    def token_for(kind: str, value: str) -> str:
        if value in seen:
            return seen[value]
        counters[kind] = counters.get(kind, 0) + 1
        token = f"[{kind}_{counters[kind]}]"
        seen[value] = token
        result.mapping[token] = value
        return token

    def repo_sub(m: re.Match) -> str:
        url = f"https://github.com/{m.group(1)}/{m.group(2)}"
        token = token_for("REPO", url)
        result.repo_urls[token] = url
        return token

    masked = _GITHUB_REPO.sub(repo_sub, masked)
    masked = _GITHUB_PROFILE.sub(lambda m: token_for("GITHUB", m.group(0)), masked)
    masked = _EMAIL.sub(lambda m: token_for("EMAIL", m.group(0)), masked)
    masked = _PHONE.sub(lambda m: token_for("PHONE", m.group(0)), masked)
    masked = _SCHOOL.sub(lambda m: "[SCHOOL]", masked)
    result.text = masked
    return result
