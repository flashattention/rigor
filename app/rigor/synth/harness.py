"""합성 케이스를 파이프라인 단계에 흘려보내는 도우미 (테스트·평가 러너 공용)."""

from __future__ import annotations

from pathlib import Path

from rigor.github_audit.exposure import ExposureContext
from rigor.github_audit.local_source import snapshot_local
from rigor.github_audit.snapshot import RepoMeta, RepoSnapshot
from rigor.schemas.profile import Fact, Profile, RepoLink, UnlinkedReason
from rigor.schemas.repo import UnlinkedItem
from rigor.synth.gitrepo import materialize_all
from rigor.synth.spec import Case, RepoSpec


def meta_of(spec: RepoSpec) -> RepoMeta:
    return RepoMeta(
        repo_id=spec.repo_id,
        full_name=spec.full_name,
        description=spec.description,
        topics=tuple(spec.topics),
        is_fork=spec.is_fork,
    )


def snapshots(case: Case, root: Path) -> dict[int, RepoSnapshot]:
    paths = materialize_all(case.repos, root)
    return {rid: snapshot_local(path, meta_of(case.repo(rid))) for rid, path in paths.items()}


def profile_after_stage_a(case: Case) -> Profile:
    """A단계가 정확했다고 가정한 프로필: 이력서에 적힌 링크만 URL로 붙어 있다."""
    facts = []
    for f in case.profile.facts:
        if f.id in case.linked_in_resume:
            f = f.model_copy(
                update={"repo_link": RepoLink(url=case.repo(case.linked_in_resume[f.id]).url)}
            )
        facts.append(f)
    return case.profile.model_copy(update={"facts": facts})


def truth_chooser(case: Case):
    """B3에서 사용자가 정답대로 고른다고 가정."""
    truth = {u.fact_id: u for u in case.truth.unlinked}

    def choose(fact: Fact, item: UnlinkedItem) -> tuple[UnlinkedReason, int | None]:
        u = truth.get(fact.id)
        if u is None:
            return UnlinkedReason.NO_REPO, None
        return u.reason, u.correct_repo_id

    return choose


def exposure_context(case: Case) -> ExposureContext:
    h = case.header
    return ExposureContext(names=[h.name], phones=[h.phone], emails=[h.email], schools=[h.school])
