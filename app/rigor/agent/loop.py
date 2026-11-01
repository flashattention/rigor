"""에이전트 루프: 주장 하나를 저장소에서 검증한다. **이 파일은 사용자가 직접 구현한다** (ADR-0020).

명세는 tests/agent/test_loop.py다. 구현하면 그 테스트들이 통과해야 한다.
도구(tools.py), 판정 검증(verdict.py), LLM 호출(client.tool_turn)은 이미 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rigor.agent.tools import RepoWorkspace
from rigor.agent.verdict import CheckedVerdict
from rigor.llm.client import LLMClient
from rigor.schemas.profile import Claim

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
    """모델이 도구를 고르고, 코드가 실행해 결과를 돌려주는 루프.

    구현할 내용:
    1. load_prompt(TASK)를 시스템 프롬프트로, 주장을 data_block("claim", ...)에 담아 첫 메시지로.
    2. 매 턴 client.tool_turn(TASK, system=..., messages=..., tools=TOOLS)를 부른다.
    3. 응답의 tool_calls 중 SUBMIT이 있으면 check_submission으로 검증하고 끝낸다.
    4. 아니면 execute_tool로 실행해 assistant 메시지와 tool 결과 메시지를 기록에 덧붙인다.
    5. 도구를 부르지 않고 글로만 답하면 끝낸다 (unverifiable, no_tool_call).
    6. max_steps나 max_prompt_tokens에 닿으면 끝낸다 (unverifiable).
    """
    raise NotImplementedError("사용자가 직접 구현: tests/agent/test_loop.py 참고")
