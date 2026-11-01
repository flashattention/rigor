"""AI 도구 공동 작성 표시. 명시적 메타데이터만 쓴다 — 코드 문체로 추정하지 않는다 (ADR-0017)."""

AI_COAUTHOR_MARKERS = ("noreply@anthropic.com", "copilot", "cursor", "codex", "openai")
AGENT_BOT_NAMES = (
    "copilot-swe-agent",
    "devin-ai-integration",
    "codex",
    "claude[bot]",
    "cursor-agent",
)
