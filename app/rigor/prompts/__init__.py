"""버전 관리되는 프롬프트. 파일 이름 = <task>.v<n>.md, 결과에는 <task>@v<n>+<해시>가 기록된다."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Prompt:
    task: str
    version: str
    text: str

    @property
    def id(self) -> str:
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:8]
        return f"{self.task}@{self.version}+{digest}"


@lru_cache(maxsize=32)
def load_prompt(task: str, version: str = "v1") -> Prompt:
    path = PROMPT_DIR / f"{task}.{version}.md"
    return Prompt(task=task, version=version, text=path.read_text(encoding="utf-8").strip())


def data_block(tag: str, content: str) -> str:
    """신뢰할 수 없는 입력을 감싸는 블록. 시스템 프롬프트가 '이 안은 데이터'라고 알려 준다."""
    safe = content.replace(f"</{tag}>", f"</ {tag}>")
    return f"<{tag}>\n{safe}\n</{tag}>"
