"""규칙 / 키워드+LLM / 에이전트를 같은 데이터로 비교한다 (docs/evals-agent-design.md).

공개 저장소 이름과 주장은 출력하지 않는다. 사례별 결과는 git 제외 폴더에, 집계만 evals/reports/에 둔다.

    uv run python -m evals.agent.runner --split dev --profile openai_mini --max-usd 0.05 --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import Field

from evals.agent.dataset import (
    ROOT,
    ClaimCase,
    injection_cases,
    load_repo,
    load_split,
    transplant,
    with_injection,
)
from rigor.agent.loop import AgentBudget, verify_claim
from rigor.agent.tools import RepoWorkspace
from rigor.agent.verdict import CheckedVerdict, VerdictSubmission, check_submission
from rigor.github_audit.claims import check_claims_by_rules
from rigor.github_audit.signals import compute_signals
from rigor.github_audit.snapshot import RepoSnapshot
from rigor.llm.client import LLMClient
from rigor.llm.factory import build_client
from rigor.prompts import data_block, load_prompt
from rigor.schemas.common import DraftModel
from rigor.schemas.profile import Claim, ClaimType, Fact, FactCategory, FactStatus
from rigor.schemas.repo import ClaimResult

TASK = "claim_verify"
METHODS = ("rules", "keyword", "agent")
MAX_TERMS = 6
HITS_PER_TERM = 3
WINDOW = 8  # 검색 결과 앞뒤로 함께 보여 줄 줄 수 (관례값)


class SearchTerms(DraftModel):
    terms: list[str] = Field(default_factory=list)


@dataclass
class CaseResult:
    case_id: str
    type: str
    truth: str
    method: str
    result: str
    downgraded: bool = False
    rejected_citations: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    steps: int = 0
    stop_reason: str = ""
    error: str = ""


def _claim(case: ClaimCase) -> Claim:
    ctype = ClaimType.METRIC if case.type == "C" else ClaimType.FEATURE
    return Claim(id="c_eval", fact_id="f_eval", type=ctype, text=case.text)


def run_rules(case: ClaimCase, snap: RepoSnapshot) -> CheckedVerdict:
    """지금의 claims.py. 판단하지 못하면 보류(unverifiable)로 센다."""
    fact = Fact(
        id="f_eval",
        category=FactCategory.PROJECT,
        content="eval",
        status=FactStatus.CONFIRMED,
        claims=[_claim(case)],
    )
    checks = check_claims_by_rules(fact, snap, compute_signals(snap), None).checks
    if not checks:
        return CheckedVerdict(ClaimResult.UNVERIFIABLE, "규칙으로 판단 못 함")
    return CheckedVerdict(checks[0].result, checks[0].action or "")


def run_keyword(client: LLMClient, case: ClaimCase, ws: RepoWorkspace) -> CheckedVerdict:
    """검색어 생성 → 검색 → 결과 주변을 읽어 한 번에 판정. 다음 행동을 모델이 고르지 않는다."""
    claim_block = data_block("claim", case.text)
    terms = client.structured(
        TASK,
        system=load_prompt("claim_keywords").text,
        messages=[{"role": "user", "content": claim_block}],
        schema=SearchTerms,
    ).value.terms[:MAX_TERMS]
    sections: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for line in ws.grep(term).splitlines()[1 : HITS_PER_TERM + 1]:
            path, _, rest = line.partition(":")
            number = rest.partition(":")[0]
            if not number.isdigit():
                continue
            key = f"{path}:{int(number) // WINDOW}"
            if key in seen:
                continue
            seen.add(key)
            start = max(1, int(number) - WINDOW)
            sections.append(ws.read_file(path, start, int(number) + WINDOW))
    results = "\n\n".join(sections) or "(검색 결과 없음)"
    verdict = client.structured(
        TASK,
        system=load_prompt("claim_judge").text,
        messages=[
            {
                "role": "user",
                "content": claim_block + "\n\n" + data_block("search_results", results),
            }
        ],
        schema=VerdictSubmission,
    ).value
    return check_submission(ws, verdict.model_dump(mode="json"))


def evaluate_case(
    client: LLMClient, method: str, case: ClaimCase, snap: RepoSnapshot
) -> CaseResult:
    target = with_injection(snap) if case.type == "E" else snap
    ws = RepoWorkspace(target)
    before, started = client.meter.total_usd, time.monotonic()
    steps, stop, error = 0, "", ""
    try:
        if method == "rules":
            verdict = run_rules(case, target)
        elif method == "keyword":
            verdict = run_keyword(client, case, ws)
        else:
            outcome = verify_claim(client, _claim(case), ws, AgentBudget())
            verdict, steps, stop = outcome.verdict, outcome.steps, outcome.stop_reason
    except (
        Exception
    ) as exc:  # 사례 하나의 실패가 실험 전체를 멈추지 않게 한다. 예산 초과는 위에서 처리
        if type(exc).__name__ == "BudgetExceeded":
            raise
        verdict, error = CheckedVerdict(ClaimResult.UNVERIFIABLE, "실행 오류"), type(exc).__name__
    return CaseResult(
        case_id=case.id,
        type=case.type,
        truth=case.truth,
        method=method,
        result=verdict.result.value,
        downgraded=verdict.downgraded,
        rejected_citations=len(verdict.rejected),
        cost_usd=client.meter.total_usd - before,
        seconds=time.monotonic() - started,
        steps=steps,
        stop_reason=stop,
        error=error,
    )


def score(results: list[CaseResult]) -> dict:
    """설계 문서의 지표. 분모가 0이면 None (0%로 보이지 않게)."""

    def ratio(n: int, d: int) -> float | None:
        return round(n / d, 3) if d else None

    n = len(results)
    said_supported = [r for r in results if r.result == "supported"]
    false_claims = [r for r in results if r.truth != "supported"]
    true_claims = [r for r in results if r.truth == "supported"]
    injected = [r for r in results if r.type == "E"]
    accused = [r for r in true_claims if r.result in ("not_found", "contradicted")]
    return {
        "n": n,
        "supported_precision": ratio(
            sum(r.truth == "supported" for r in said_supported), len(said_supported)
        ),
        "false_claim_recall": ratio(
            sum(r.result != "supported" for r in false_claims), len(false_claims)
        ),
        "true_claim_recall": ratio(
            sum(r.result == "supported" for r in true_claims), len(true_claims)
        ),
        "false_accusation_rate": ratio(len(accused), len(true_claims)),
        "abstain_rate": ratio(sum(r.result == "unverifiable" for r in results), n),
        "exact_accuracy": ratio(sum(r.result == r.truth for r in results), n),
        "injection_defense": ratio(sum(r.result != "supported" for r in injected), len(injected)),
        "downgraded": sum(r.downgraded for r in results),
        "errors": sum(bool(r.error) for r in results),
        "mean_cost_usd": round(sum(r.cost_usd for r in results) / n, 5) if n else None,
        "total_cost_usd": round(sum(r.cost_usd for r in results), 4),
        "mean_seconds": round(sum(r.seconds for r in results) / n, 1) if n else None,
        "mean_steps": round(sum(r.steps for r in results) / n, 1) if n else None,
        "stop_reasons": dict(Counter(r.stop_reason for r in results if r.stop_reason)),
        "by_type_results": {
            t: dict(Counter(r.result for r in results if r.type == t))
            for t in sorted({r.type for r in results})
        },
    }


def build_cases(split: str) -> tuple[list[ClaimCase], dict[str, RepoSnapshot]]:
    repos, written = load_split(split)
    snaps = {r["full_name"]: load_repo(r) for r in repos}
    moved = transplant(written, snaps)
    return [*written, *moved, *injection_cases([*written, *moved])], snaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev")
    ap.add_argument("--profile", default="openai_mini")
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--max-usd", type=float, required=True, help="이번 실행 전체의 비용 상한")
    ap.add_argument("--limit", type=int, default=0, help="유형별로 앞에서 n개만 (첫 비용 확인용)")
    args = ap.parse_args()
    if Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and value and not key.startswith("#"):
                os.environ.setdefault(key, value.strip().strip('"'))
    cases, snaps = build_cases(args.split)
    if args.limit:
        counts: Counter[str] = Counter()
        kept = []
        for c in cases:
            if counts[c.type] < args.limit:
                kept.append(c)
                counts[c.type] += 1
        cases = kept
    client = build_client(profile=args.profile, mode="live", max_usd=args.max_usd)
    results: list[CaseResult] = []
    stopped = False
    for method in args.methods.split(","):
        for case in cases:
            try:
                results.append(evaluate_case(client, method, case, snaps[case.repo]))
            except Exception as exc:
                print(f"중단: {type(exc).__name__} (사례 {len(results)}개까지 집계)")
                stopped = True
                break
        if stopped:
            break
    out = ROOT / "runs" / f"{args.split}_{args.profile}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {m: score([r for r in results if r.method == m]) for m in args.methods.split(",")}
    print(
        json.dumps(
            {
                "routing": client.routing.hash,
                "total_usd": round(client.meter.total_usd, 4),
                "methods": summary,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
