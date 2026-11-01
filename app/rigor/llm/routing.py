"""task → (provider, model, params) 해석. ADR-0009 결정 2.

노드는 모델 이름을 모른다. 제공자 교체 = configs/routing.yaml 변경 + 재측정.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

KNOWN_TASKS = frozenset(
    {
        "fact_extract",
        "jd_parse",
        "repo_summarize",
        "claim_verify",
        "requirement_verdict",
        "review_write",
        "roadmap_write",
        "resume_update",
        "interview_card",
        "policy_classify",
        "judge",
    }
)

PROFILE_ENV = "RIGOR_ROUTING_PROFILE"
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "configs" / "routing.yaml"


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)


class Routing:
    def __init__(self, profile_name: str, default: Route, tasks: dict[str, Route]):
        unknown = set(tasks) - KNOWN_TASKS
        if unknown:
            raise ValueError(f"unknown tasks in routing profile {profile_name}: {sorted(unknown)}")
        self.profile_name = profile_name
        self.default = default
        self.tasks = tasks

    def resolve(self, task: str) -> Route:
        """task 전용 경로가 없으면 default. KNOWN_TASKS에 없는 task는 KeyError."""
        if task not in KNOWN_TASKS:
            raise KeyError(f"unknown task: {task}")
        return self.tasks.get(task, self.default)

    @property
    def hash(self) -> str:
        """해석된 라우팅 전체의 해시. 결과·녹화·평가 보고서를 이 단위로 나눈다."""
        resolved = {t: self.resolve(t).__dict__ for t in sorted(KNOWN_TASKS)}
        blob = json.dumps(resolved, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    @classmethod
    def load(cls, path: Path | None = None, profile: str | None = None) -> Routing:
        """프로필 우선순위: 인자 > RIGOR_ROUTING_PROFILE > 파일의 active.

        task 설정은 default 위에 얕게 병합한다. params를 적으면 default의 params를 통째로 대체한다.
        """
        data = yaml.safe_load((path or DEFAULT_PATH).read_text(encoding="utf-8"))
        name = profile or os.environ.get(PROFILE_ENV) or data["active"]
        try:
            prof = data["profiles"][name]
        except KeyError as exc:
            raise KeyError(f"routing profile not found: {name}") from exc
        default = Route(**prof["default"])
        tasks = {t: Route(**{**prof["default"], **r}) for t, r in (prof.get("tasks") or {}).items()}
        return cls(name, default, tasks)
