"""프롬프트 인젝션 의심 문장 탐지 (규칙 기반, 위협 모델 design.md §7).

탐지는 1차 방어일 뿐이다. 주 방어는 구조다: 입력은 데이터 블록으로만 전달되고, 등급은
LLM이 아니라 규칙이 계산한다 (ADR-0008). 여기서 잡힌 문장은 요건·fact로 쓰지 않고 경고로 남긴다.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"(평가자|심사자|채점자|AI|모델|시스템|어시스턴트)\s*(는|은|에게)?\s*.{0,20}(반드시|무조건|꼭)",
    r"(강력\s*추천|합격)\s*(등급)?\s*을?\s*(부여|주|매기)",
    r"(이전|위의?|앞의?)\s*(지시|명령|지침)\s*(을|를)?\s*(무시|잊)",
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)",
    r"(you\s+must|always)\s+(rate|recommend|score)",
    r"system\s*prompt",
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)


def find_injection_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _REGEX.search(line)]


def is_injection(text: str) -> bool:
    return bool(_REGEX.search(text))
