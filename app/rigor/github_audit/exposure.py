"""노출 위험 점검: 비밀값, 실명·연락처·학교 (블라인드 공고 대비). 규칙 기반, LLM 미사용.

발견 결과에는 값 자체를 담지 않는다 (리포트·로그로 다시 새지 않게).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rigor.github_audit.snapshot import RepoSnapshot
from rigor.schemas.repo import ExposureFinding, ExposureKind

_SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key 형식", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "AWS secret key 할당",
        re.compile(r"aws_secret_access_key\s*[=:]\s*['\"][A-Za-z0-9/+=]{40}['\"]", re.I),
    ),
    ("GitHub 토큰 형식", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("OpenAI 키 형식", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic 키 형식", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("개인 키 블록", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    (
        "하드코딩된 비밀번호·토큰",
        re.compile(
            r"(?:password|passwd|secret|api_?key|token)\s*[=:]\s*['\"][^'\"\s]{12,}['\"]", re.I
        ),
    ),
]
_SKIP_EXT = (".lock", ".min.js", ".svg", ".png", ".jpg")


@dataclass
class ExposureContext:
    """사용자 본인의 식별 정보 (마스킹 매핑에서 온다). 저장소에 있으면 블라인드 공고에서 위험."""

    names: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    schools: list[str] = field(default_factory=list)


def scan_exposure(snap: RepoSnapshot, ctx: ExposureContext) -> list[ExposureFinding]:
    findings: list[ExposureFinding] = []
    pii = [
        (ExposureKind.REAL_NAME, ctx.names, "실명"),
        (ExposureKind.CONTACT, ctx.phones + ctx.emails, "연락처"),
        (ExposureKind.SCHOOL, ctx.schools, "학교명"),
    ]
    for path, text in snap.files.items():
        if path.endswith(_SKIP_EXT):
            continue
        for label, pattern in _SECRETS:
            m = pattern.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                findings.append(
                    ExposureFinding(kind=ExposureKind.SECRET, path=path, line=line, detail=label)
                )
                break  # 파일당 비밀값 하나만 보고해도 조치는 같다
        for kind, values, label in pii:
            for value in values:
                if value and value in text:
                    line = text.count("\n", 0, text.index(value)) + 1
                    findings.append(
                        ExposureFinding(kind=kind, path=path, line=line, detail=f"{label} 노출")
                    )
                    break
    return findings
