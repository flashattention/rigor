"""직무 분류 체계 (configs/taxonomy.yaml, docs/taxonomy.md)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "configs" / "taxonomy.yaml"


@dataclass(frozen=True)
class Taxonomy:
    version: str
    competencies: dict[str, str]
    roles: dict[str, dict[str, float]]
    skill_competencies: dict[str, tuple[str, ...]]
    _alias: dict[str, str]

    def normalize_skill(self, raw: str) -> str:
        """정규 이름. 모르는 기술은 소문자·공백 정리한 원문을 돌려준다 (unmapped)."""
        key = raw.strip().lower()
        return self._alias.get(key, key.replace(" ", "_"))

    def aliases(self) -> dict[str, str]:
        """소문자 별칭 → 정규 이름."""
        return dict(self._alias)

    def is_known_skill(self, skill: str) -> bool:
        return skill in self.skill_competencies

    def core_competencies(self, role: str) -> set[str]:
        return {c for c, w in self.roles.get(role, {}).items() if w >= 2}


@lru_cache(maxsize=4)
def load_taxonomy(path: Path = DEFAULT_PATH) -> Taxonomy:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    alias: dict[str, str] = {}
    skill_comp: dict[str, tuple[str, ...]] = {}
    for name, spec in data["skills"].items():
        skill_comp[name] = tuple(spec.get("competencies", []))
        alias[name.lower()] = name
        for a in spec.get("aliases", []):
            alias[a.lower()] = name
    return Taxonomy(
        version=str(data["version"]),
        competencies={k: v["name"] for k, v in data["competencies"].items()},
        roles={k: dict(v["weights"]) for k, v in data["roles"].items()},
        skill_competencies=skill_comp,
        _alias=alias,
    )
