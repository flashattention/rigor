"""RIGOR CLI. M1에서 `rigor diagnose`가 추가된다."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from rigor import __version__
from rigor.llm.pricing import Pricing
from rigor.llm.routing import KNOWN_TASKS, Routing

app = typer.Typer(
    help="RIGOR — Resume Inspection with GitHub for Offer Readiness", no_args_is_help=True
)
routing_app = typer.Typer(help="LLM 라우팅 설정")
cost_app = typer.Typer(help="비용 추정")
synth_app = typer.Typer(help="합성 평가 데이터")
app.add_typer(routing_app, name="routing")
app.add_typer(cost_app, name="cost")
app.add_typer(synth_app, name="synth")

# docs/cost-estimate.md §1의 합성 케이스 1건 가정
CASE_INPUT_TOKENS = 110_000
CASE_OUTPUT_TOKENS = 16_000


@app.command()
def version() -> None:
    """버전을 출력한다."""
    typer.echo(__version__)


@routing_app.command("show")
def routing_show(profile: str | None = typer.Option(None, help="프로필 이름")) -> None:
    """task별로 어떤 제공자·모델이 쓰이는지 보여 준다."""
    routing = Routing.load(profile=profile)
    typer.echo(f"profile={routing.profile_name} hash={routing.hash}")
    for task in sorted(KNOWN_TASKS):
        r = routing.resolve(task)
        typer.echo(f"  {task:<20} {r.provider}/{r.model}")


@cost_app.command("estimate")
def cost_estimate(
    runs: float = typer.Option(..., help="합성 케이스 환산 실행 수 (전체 평가 ≈ 170)"),
    profile: str | None = typer.Option(None, help="프로필 이름"),
    batch: bool = typer.Option(False, help="Batch API 50% 할인 적용"),
) -> None:
    """실제 호출 전에 예상 비용을 보여 준다 (docs/cost-estimate.md와 같은 가정)."""
    routing = Routing.load(profile=profile)
    pricing = Pricing.load()
    route = routing.default
    usd = pricing.estimate_usd(
        route.provider,
        route.model,
        runs=runs,
        input_tokens_per_run=CASE_INPUT_TOKENS,
        output_tokens_per_run=CASE_OUTPUT_TOKENS,
        batch=batch,
    )
    verified = pricing.price(route.provider, route.model).verified
    typer.echo(
        f"{route.provider}/{route.model} runs={runs:g} batch={batch}: "
        f"${usd:.2f} ≈ ₩{pricing.to_krw(usd):,.0f}" + ("" if verified else "  (단가 미검증)")
    )


@synth_app.command("list")
def synth_list(variants: bool = typer.Option(False, help="변형 케이스도 포함")) -> None:
    """합성 케이스 목록과 정답 등급."""
    from rigor.synth.cases import base_cases
    from rigor.synth.variants import all_variants

    cases = base_cases()
    if variants:
        cases += all_variants(cases)
    for c in cases:
        typer.echo(f"{c.case_id:<45} grade={c.truth.grade.value:<16} {c.description[:40]}")


@synth_app.command("build")
def synth_build(
    case_id: str,
    out: Path = typer.Option(..., help="출력 디렉터리 (비어 있어야 함)"),
) -> None:
    """케이스 하나를 파일로 만든다: 이력서·공고 텍스트, 정답 JSON, 실제 git 저장소."""
    from rigor.synth.cases import base_cases
    from rigor.synth.gitrepo import materialize_all
    from rigor.synth.render import render_jd, render_resume
    from rigor.synth.variants import all_variants

    base = base_cases()
    case = next((c for c in base + all_variants(base) if c.case_id == case_id), None)
    if case is None:
        typer.echo(f"unknown case: {case_id}")
        raise typer.Exit(code=1)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        typer.echo(f"{out} is not empty")
        raise typer.Exit(code=1)
    (out / "resume.txt").write_text(render_resume(case), encoding="utf-8")
    (out / "jd.txt").write_text(render_jd(case), encoding="utf-8")
    (out / "truth.json").write_text(
        json.dumps(case.truth.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths = materialize_all(case.repos, out / "repos")
    typer.echo(f"built {case_id}: {len(paths)} repos → {out}")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """.env의 키를 환경 변수로 (이미 설정된 값은 덮어쓰지 않음). 값은 출력하지 않는다."""
    import os

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value:
            os.environ.setdefault(key, value.strip().strip('"'))


@app.command()
def prepare(
    resume: Path = typer.Option(..., exists=True, help="이력서 파일 (텍스트·PDF·DOCX)"),
    jd: Path = typer.Option(..., exists=True, help="공고 텍스트 파일"),
    out: Path = typer.Option(..., help="결과 JSON 경로"),
    repos: list[Path] = typer.Option([], help="본인 커밋 신원을 확인할 로컬 git 저장소"),
    login: str = typer.Option("", help="GitHub 로그인 (커밋 신원 기준)"),
    profile: str | None = typer.Option(None, help="라우팅 프로필"),
    mode: str = typer.Option("replay", help="live | record | replay"),
    max_usd: float = typer.Option(0.05, help="이번 실행의 LLM 비용 상한 (USD)"),
    yes: bool = typer.Option(False, "--yes", help="추출된 fact를 모두 확인 처리 (비대화형)"),
) -> None:
    """A단계: 이력서·공고를 구조화하고 사용자 확인을 받는다."""
    from rigor.ingest.files import IngestError, extract_text
    from rigor.ingest.privacy import mask_pii
    from rigor.llm.factory import build_client
    from rigor.nodes.identity import collect_authors, confirm_aliases, propose_aliases
    from rigor.nodes.jd_parser import parse_jd
    from rigor.nodes.profile_builder import confirm_facts, extract_profile
    from rigor.schemas.profile import CommitIdentity, FactStatus

    _load_dotenv()
    client = build_client(profile=profile, mode=mode, max_usd=max_usd)  # type: ignore[arg-type]
    try:
        extracted = extract_text(resume.read_bytes())
    except IngestError as exc:
        typer.echo(f"이력서를 읽을 수 없습니다: {exc}")
        raise typer.Exit(code=1) from exc
    if extracted.needs_ocr:
        typer.echo("OCR이 필요한 파일입니다 (M1b에서 지원). 텍스트 PDF나 텍스트로 올려 주세요.")
        raise typer.Exit(code=1)
    mask = mask_pii(extracted.text)
    typer.echo(
        f"민감정보 줄 {mask.discarded_sensitive_lines}개 제거, 저장소 링크 {len(mask.repo_urls)}개"
    )

    extraction = extract_profile(client, mask, profile_id="p_cli")
    for w in extraction.warnings:
        typer.echo(f"  경고: {w}")

    def decide(fact, uncertain):
        flag = f"  (모호: {', '.join(uncertain)})" if uncertain else ""
        typer.echo(f"[{fact.id}] {fact.category.value}: {fact.content}{flag}")
        for c in fact.claims:
            typer.echo(f"    - ({c.type.value}) {c.text}")
        if yes:
            return FactStatus.CONFIRMED
        ok = typer.confirm("  이 항목이 사실과 맞습니까?", default=True)
        return FactStatus.CONFIRMED if ok else FactStatus.REJECTED

    confirmed = confirm_facts(extraction, decide)
    parsed = parse_jd(client, jd.read_text(encoding="utf-8"), job_id="j_cli")
    for w in parsed.warnings:
        typer.echo(f"  공고 경고: {w}")
    for r in parsed.job.requirements:
        typer.echo(f"[{r.id}] {r.kind.value:<9} {r.text}")

    if repos:
        identity = CommitIdentity(github_login=login, emails=[], name_aliases=[])
        candidates = propose_aliases(identity, collect_authors(repos))
        for i, c in enumerate(candidates):
            typer.echo(f"  ({i}) {c.name} <{c.email}> 커밋 {c.commits}개 {'; '.join(c.reasons)}")

        def choose(cands):
            if yes:
                return [c.email for c in cands if c.score > 0]
            raw = typer.prompt("본인 커밋 신원 번호(쉼표로 구분, 없으면 빈칸)", default="")
            return [cands[int(x)].email for x in raw.split(",") if x.strip()]

        confirmed = confirmed.model_copy(
            update={"identity": confirm_aliases(identity, candidates, choose)}
        )

    out.write_text(
        json.dumps(
            {
                "profile": confirmed.model_dump(mode="json"),
                "job": parsed.job.model_dump(mode="json"),
                "prompts": [extraction.prompt_id, parsed.prompt_id],
                "routing_hash": client.routing.hash,
                "cost_usd": client.meter.total_usd,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo(f"저장: {out}  (LLM 비용 ${client.meter.total_usd:.4f})")


@app.command()
def diagnose(
    out: Path = typer.Option(..., help="리포트 Markdown 경로"),
    synth_case: str | None = typer.Option(None, help="합성 케이스로 시연 (예: A_agent_strong)"),
    use_truth_profile: bool = typer.Option(
        False, help="합성 케이스: A단계 대신 정답 프로필로 시작"
    ),
    resume: Path | None = typer.Option(None, exists=True, help="이력서 파일"),
    jd: Path | None = typer.Option(None, exists=True, help="공고 텍스트 파일"),
    repo: list[Path] = typer.Option([], help="로컬 git 저장소 경로 (여러 번 지정 가능)"),
    github_repo: list[str] = typer.Option([], help="공개 GitHub 저장소 owner/name (여러 번)"),
    github_login: str | None = typer.Option(
        None, help="GitHub 계정: 본인 소유 + 이력서에 링크된 기여 저장소를 자동 수집"
    ),
    mask_term: list[str] = typer.Option(
        [], help="LLM에 보내기 전에 가릴 식별어 (예: 대회명 속 학교 약칭)"
    ),
    use_gh_token: bool = typer.Option(
        False, help="gh CLI 토큰을 GitHub API 요청 한도 용도로만 사용 (읽기 요청만 보냄)"
    ),
    login: str = typer.Option("", help="GitHub 로그인 (커밋 신원 기준)"),
    email: list[str] = typer.Option([], help="본인 커밋 이메일 (여러 번)"),
    profile: str | None = typer.Option(None, help="라우팅 프로필"),
    mode: str = typer.Option("replay", help="live | record | replay"),
    max_usd: float = typer.Option(0.10, help="LLM 비용 상한 (USD)"),
    yes: bool = typer.Option(False, "--yes", help="모든 선택을 기본값으로 (비대화형)"),
) -> None:
    """핵심 흐름 전체: 공고 + 이력서 + GitHub → 적합도 등급 + 프로젝트 선택·개선 리포트."""
    import tempfile

    from rigor.github_audit.exposure import ExposureContext
    from rigor.github_audit.local_source import snapshot_local
    from rigor.github_audit.snapshot import RepoMeta
    from rigor.ingest.files import extract_text
    from rigor.ingest.privacy import mask_pii
    from rigor.llm.factory import build_client
    from rigor.nodes.jd_parser import parse_jd
    from rigor.nodes.profile_builder import confirm_facts, extract_profile
    from rigor.pipeline import Choosers, run_pipeline
    from rigor.report.markdown import render_markdown
    from rigor.schemas.profile import CommitIdentity, FactCategory, FactStatus, UnlinkedReason
    from rigor.schemas.repo import ReconcileChoice

    _load_dotenv()
    client = build_client(profile=profile, mode=mode, max_usd=max_usd)  # type: ignore[arg-type]
    tmp = Path(tempfile.mkdtemp(prefix="rigor-"))

    if synth_case:
        from rigor.synth.cases import base_cases
        from rigor.synth.harness import exposure_context, profile_after_stage_a, snapshots
        from rigor.synth.render import render_jd, render_resume
        from rigor.synth.variants import all_variants

        base = base_cases()
        case = next((c for c in base + all_variants(base) if c.case_id == synth_case), None)
        if case is None:
            typer.echo(f"unknown case: {synth_case}")
            raise typer.Exit(code=1)
        if client.routing.default.provider == "fake":
            from rigor.synth.oracle import oracle_adapter

            client.adapters["fake"] = oracle_adapter(case, mask_pii(render_resume(case)))
            typer.echo("fake 프로필: 합성 정답 오라클로 실행 (LLM 품질이 아니라 배선 시연)")
        snaps = snapshots(case, tmp)
        exposure = exposure_context(case)
        identity = case.profile.identity
        resume_text, jd_text = render_resume(case), render_jd(case)
        truth_profile = profile_after_stage_a(case) if use_truth_profile else None
    else:
        if not (resume and jd and (repo or github_repo or github_login)):
            typer.echo(
                "--resume, --jd, 그리고 --repo / --github-repo / --github-login 중 하나가 필요합니다."
            )
            raise typer.Exit(code=1)
        if use_gh_token:
            import os
            import subprocess

            os.environ.setdefault(
                "GITHUB_TOKEN",
                subprocess.run(
                    ["gh", "auth", "token"], capture_output=True, text=True, check=True
                ).stdout.strip(),
            )
        resume_text = extract_text(resume.read_bytes()).text
        jd_text = jd.read_text(encoding="utf-8")
        snaps = {}
        for i, path in enumerate(repo, start=1):
            # 로컬 저장소에는 GitHub id가 없다. 음수로 두어 실제 id와 겹치지 않게 한다.
            meta = RepoMeta(repo_id=-i, full_name=f"{login or 'local'}/{path.resolve().name}")
            snaps[meta.repo_id] = snapshot_local(path, meta)
        if github_repo:
            from rigor.github_audit.api_source import GitHubSource

            source = GitHubSource()
            for full in github_repo:
                snap = source.snapshot(full)
                snaps[snap.meta.repo_id] = snap
        identity = CommitIdentity(
            github_login=login, emails=[e.lower() for e in email], confirmed_by_user=True
        )
        truth_profile = None
        exposure = None

    mask = mask_pii(resume_text, extra_terms=mask_term)
    if exposure is None:
        exposure = ExposureContext(
            names=[v for k, v in mask.mapping.items() if k == "[NAME]"],
            phones=[v for k, v in mask.mapping.items() if k.startswith("[PHONE")],
            emails=[v for k, v in mask.mapping.items() if k.startswith("[EMAIL")],
            schools=[*mask.schools, *mask_term],
        )
    if truth_profile is not None:
        prof = truth_profile
    else:
        extraction = extract_profile(client, mask, "p_cli")
        for w in extraction.warnings:
            typer.echo(f"  추출 경고: {w}")
        prof = confirm_facts(
            extraction,
            lambda f, u: (
                FactStatus.CONFIRMED
                if yes or typer.confirm(f"[{f.id}] {f.content} — 사실과 맞습니까?", default=True)
                else FactStatus.REJECTED
            ),
        )
        prof = prof.model_copy(update={"identity": identity})
    if github_login and not synth_case:
        from rigor.github_audit.api_source import GitHubSource
        from rigor.github_audit.selection import (
            fetch_snapshots,
            identity_from_account,
            name_matched_emails,
            select_repos,
        )

        source = GitHubSource()
        owned = source.list_user_repos(github_login)
        sel = select_repos(prof, owned, github_login, source.is_contributor)
        for note in sel.notes:
            typer.echo(f"  {note}")
        for name in sel.skipped_not_mine:
            typer.echo(f"  본인 계정 커밋이 없는 저장소라 분석하지 않음: {name}")
        typer.echo(f"  수집할 저장소 {len(sel.full_names)}개: {', '.join(sel.full_names)}")
        fetched, failures = fetch_snapshots(source, sel.full_names)
        snaps.update(fetched)
        for full, reason in failures:
            typer.echo(f"  수집 실패로 건너뜀: {full} ({reason})")
        aliases = name_matched_emails(github_login, snaps)
        if aliases and not yes:
            typer.echo(
                f"  계정에 연결되지 않았지만 작성자 이름이 같은 커밋 이메일: {', '.join(aliases)}"
            )
            if not typer.confirm("  이 커밋들이 본인 것입니까?", default=True):
                aliases = []
        if aliases:
            typer.echo(f"  이름이 일치하는 별칭 이메일 {len(aliases)}개를 본인 커밋으로 집계")
        prof = prof.model_copy(
            update={"identity": identity_from_account(github_login, snaps, [*email, *aliases])}
        )
    parsed = parse_jd(client, jd_text, "j_cli")
    for w in parsed.warnings:
        typer.echo(f"  공고 경고: {w}")
    job = parsed.job

    def choose_unlinked(fact, item):
        top = item.candidates[0] if item.candidates else None
        if yes:
            if top and top.score >= 0.6:
                return UnlinkedReason.FORGOT, top.repo_id
            if fact.category is FactCategory.EXPERIENCE:
                return UnlinkedReason.PRIVATE_OR_COMPANY, None
            return UnlinkedReason.NO_REPO, None
        typer.echo(f"\n[{fact.id}] {fact.content}: 이력서에 저장소가 없습니다.")
        for i, c in enumerate(item.candidates):
            typer.echo(f"  후보 {i}: {c.full_name} (점수 {c.score}, {'; '.join(c.reasons)})")
        reasons = list(UnlinkedReason)
        for i, r in enumerate(reasons):
            typer.echo(f"  ({i}) {r.value}")
        reason = reasons[int(typer.prompt("사유 번호", default="0"))]
        repo_id = None
        if reason in (UnlinkedReason.FORGOT, UnlinkedReason.LOW_QUALITY) and item.candidates:
            repo_id = item.candidates[int(typer.prompt("후보 번호", default="0"))].repo_id
        return reason, repo_id

    def choose_reconcile(item):
        if yes:
            return ReconcileChoice.REVISE_TO_FACT, item.suggestion
        typer.echo(f"\n불일치: {item.claim.text} ({item.mismatch.value})")
        typer.echo(f"  수정 제안: {item.suggestion}")
        for i, o in enumerate(item.options):
            typer.echo(f"  ({i}) {o.value}")
        choice = item.options[int(typer.prompt("선택 번호", default="0"))]
        return choice, item.suggestion

    choosers = Choosers(
        unlinked=choose_unlinked,
        reconcile=choose_reconcile,
        can_explain=lambda f: (
            yes
            or typer.confirm(
                f"'{f.content}'의 설계 결정과 검증 방법을 면접에서 설명할 수 있습니까?",
                default=True,
            )
        ),
    )
    report = run_pipeline(prof, job, snaps, client, exposure, choosers)
    out.write_text(render_markdown(report), encoding="utf-8")
    typer.echo(
        f"등급: {report.diagnosis.grade.value} ({report.diagnosis.grade_trace.rule_id}) → {out}"
        f"  (LLM 비용 ${client.meter.total_usd:.4f})"
    )


def confirm_spend(usd: float, yes: bool) -> None:
    """실제 호출 평가 전 확인. --yes 없이 비용이 드는 실행은 하지 않는다."""
    if usd <= 0 or yes:
        return
    typer.echo(f"예상 비용 ${usd:.2f}. 실행하려면 --yes를 붙여 다시 실행하세요.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
