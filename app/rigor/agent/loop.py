"""에이전트 루프: 주장 하나를 저장소에서 검증한다 (ADR-0020). 명세는 tests/agent/test_loop.py.

모델이 다음 도구를 고르고, 코드는 실행해서 결과를 돌려준다. 예산은 호출 전에 검사하고,
판정은 verdict.py가 코드로 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rigor.agent.tools import SUBMIT, TOOLS, RepoWorkspace, execute_tool
from rigor.agent.verdict import CheckedVerdict, check_submission
from rigor.llm.client import LLMClient
from rigor.llm.types import AgentMessage, ToolResult
from rigor.prompts import data_block, load_prompt
from rigor.schemas.profile import Claim
from rigor.schemas.repo import ClaimResult

TASK = "claim_verify"


@dataclass(frozen=True)
class AgentBudget:
    """관례값. 평가에서 비용과 판정률을 재서 조정한다."""

    max_steps: int = 8  # 모델을 부르는 횟수
    max_prompt_tokens: int = 40_000  # 이번 주장에 쓸 누적 입력 토큰


@dataclass
class AgentOutcome:
    verdict: CheckedVerdict
    steps: int = 0
    prompt_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""  # submitted | max_steps | token_budget | no_tool_call
    files_opened: list[str] = field(default_factory=list)


def verify_claim(
    client: LLMClient, claim: Claim, ws: RepoWorkspace, budget: AgentBudget = AgentBudget()
) -> AgentOutcome:
    """모델이 도구를 고르고, 코드가 실행해 결과를 돌려주는 루프."""

    system = load_prompt(TASK).text
    messages = [AgentMessage("user", text=data_block("claim", claim.text))]
    steps, prompt_tokens, cost_usd = 0, 0, 0.0

    def finish(verdict, reason):
        return AgentOutcome(
            verdict=verdict,
            steps=steps,
            prompt_tokens=prompt_tokens,
            cost_usd=cost_usd,
            stop_reason=reason,
            files_opened=sorted(ws.opened),
        )

    while True:
        if steps >= budget.max_steps:
            return finish(
                CheckedVerdict(ClaimResult.UNVERIFIABLE, "조사 횟수 상한에 도달해 판단하지 못함"),
                "max_steps",
            )
        if prompt_tokens >= budget.max_prompt_tokens:
            return finish(
                CheckedVerdict(ClaimResult.UNVERIFIABLE, "프롬프트 토큰 예산 소모"), "token_budget"
            )

        turn = client.tool_turn(TASK, system=system, messages=messages, tools=TOOLS)

        steps += 1
        prompt_tokens += turn.usage.prompt_tokens
        cost_usd += turn.cost_usd

        if turn.tool_calls == ():
            return finish(
                CheckedVerdict(ClaimResult.UNVERIFIABLE, "도구 호출 없음"), "no_tool_call"
            )

        for call in turn.tool_calls:
            if call.name == SUBMIT:
                verdict = check_submission(ws, call.arguments)
                return finish(verdict, "submitted")

        messages.append(AgentMessage("assistant", text=turn.text, tool_calls=turn.tool_calls))

        results = []
        for call in turn.tool_calls:
            output = execute_tool(ws, call)
            results.append(ToolResult(call.id, call.name, output))

        messages.append(AgentMessage("tool", results=tuple(results)))
