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


def confirm_spend(usd: float, yes: bool) -> None:
    """실제 호출 평가 전 확인. --yes 없이 비용이 드는 실행은 하지 않는다."""
    if usd <= 0 or yes:
        return
    typer.echo(f"예상 비용 ${usd:.2f}. 실행하려면 --yes를 붙여 다시 실행하세요.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
