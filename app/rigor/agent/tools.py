"""에이전트의 읽기 전용 도구 (ADR-0020). 코드를 실행하지 않는다.

저장소 내용은 신뢰할 수 없는 입력이다:
- 경로는 스냅샷에 있는 것만 연다 (경로 조작 불가).
- 검색어는 정규식이 아니라 문자열이다 (악성 패턴으로 멈추지 않게).
- 모든 출력은 LLM에 가기 전에 비밀값과 사용자 식별 정보를 가린다.
- 커밋 작성자는 이름·이메일 대신 본인/타인만 표시한다.
- 출력 크기에 상한을 둔다 (토큰 예산).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from rigor.github_audit.exposure import _SECRETS
from rigor.github_audit.snapshot import RepoSnapshot
from rigor.llm.types import ToolCall, ToolSpec
from rigor.prompts import data_block
from rigor.schemas.repo import ClaimResult

MAX_TREE_ENTRIES = 200
MAX_READ_LINES = 200
MAX_LINE_CHARS = 300
MAX_GREP_HITS = 30
MAX_LOG_ENTRIES = 20
READABLE_EXT = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".rs", ".ipynb", ".md", ".txt",
    ".csv", ".json", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".html", ".vue", ".sql",
)  # fmt: skip

Reader = Callable[[str], str | None]

SUBMIT = "submit_verdict"
TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "tree",
        "저장소 파일 목록을 본다.",
        {"type": "object", "properties": {"prefix": {"type": "string"}}},
    ),
    ToolSpec(
        "read_file",
        "파일을 줄 번호와 함께 읽는다.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        "grep",
        "저장소에서 문자열을 찾는다 (대소문자 무시, 정규식 아님).",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}, "prefix": {"type": "string"}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        "git_log",
        "커밋 기록을 본다. path를 주면 그 파일의 기록만 본다.",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSpec(
        SUBMIT,
        "조사를 끝내고 판정을 제출한다.",
        {
            "type": "object",
            "properties": {
                "result": {"type": "string", "enum": [r.value for r in ClaimResult]},
                "reason": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "line": {"type": "integer"},
                            "quote": {"type": "string"},
                        },
                        "required": ["path", "line", "quote"],
                    },
                },
            },
            "required": ["result", "reason"],
        },
    ),
)


class RepoWorkspace:
    def __init__(
        self,
        snap: RepoSnapshot,
        reader: Reader | None = None,
        user_emails: Iterable[str] = (),
        redact_terms: Iterable[str] = (),
    ):
        self.snap = snap
        self.reader = reader
        self.user_emails = {e.lower() for e in user_emails}
        self.redact_terms = [t for t in redact_terms if t]
        self._files = {p: self._redact(t) for p, t in snap.files.items()}
        self.opened: set[str] = set()  # read_file로 실제 연 파일 (인용 검증 기준)
        self._paths = set(snap.paths)

    def _redact(self, text: str) -> str:
        for _, pattern in _SECRETS:
            text = pattern.sub("[SECRET]", text)
        for term in self.redact_terms:
            text = text.replace(term, "[MASKED]")
        return text

    def _load(self, path: str) -> str | None:
        if path not in self._files and self.reader is not None:
            body = self.reader(path)
            if body is not None:
                self._files[path] = self._redact(body)
        return self._files.get(path)

    @property
    def readable_paths(self) -> list[str]:
        return [p for p in self.snap.paths if p.lower().endswith(READABLE_EXT)]

    @property
    def searched_everything(self) -> bool:
        """읽을 수 있는 파일을 전부 메모리에 올렸는가. 아니면 "없다"고 단정할 수 없다."""
        return all(p in self._files for p in self.readable_paths)

    def file_text(self, path: str) -> str | None:
        return self._files.get(path)

    def tree(self, prefix: str = "") -> str:
        paths = [p for p in self.snap.paths if p.startswith(prefix)]
        shown = paths[:MAX_TREE_ENTRIES]
        more = f"\n... 외 {len(paths) - len(shown)}개" if len(paths) > len(shown) else ""
        return "\n".join(shown) + more if shown else "(파일 없음)"

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        if path not in self._paths:
            return f"오류: 저장소에 없는 경로입니다: {path}"
        text = self._load(path)
        if text is None:
            return f"오류: 읽을 수 없는 파일입니다 (바이너리이거나 너무 큽니다): {path}"
        self.opened.add(path)
        lines = text.splitlines()
        start = max(1, start)
        last = min(end or len(lines), start + MAX_READ_LINES - 1, len(lines))
        body = "\n".join(f"{i}: {lines[i - 1][:MAX_LINE_CHARS]}" for i in range(start, last + 1))
        note = f"\n... 전체 {len(lines)}줄 중 {last}줄까지" if last < len(lines) else ""
        return f"{path}\n{body}{note}"

    def grep(self, query: str, prefix: str = "") -> str:
        needle = query.casefold()
        if not needle:
            return "오류: 검색어가 비어 있습니다"
        hits: list[str] = []
        searched = [p for p in self.readable_paths if p.startswith(prefix) and p in self._files]
        total = sum(1 for p in self.readable_paths if p.startswith(prefix))
        for path in searched:
            for i, line in enumerate(self._files[path].splitlines(), start=1):
                if needle in line.casefold():
                    hits.append(f"{path}:{i}: {line.strip()[:MAX_LINE_CHARS]}")
                if len(hits) >= MAX_GREP_HITS:
                    break
            if len(hits) >= MAX_GREP_HITS:
                break
        scope = f"[검색 범위: 읽을 수 있는 파일 {total}개 중 {len(searched)}개]"
        return "\n".join([scope, *hits]) if hits else f"{scope}\n(결과 없음)"

    def git_log(self, path: str | None = None) -> str:
        commits = self.snap.commits
        if path:
            commits = [c for c in commits if any(ch.path == path for ch in c.changes)]
        rows = []
        for c in commits[:MAX_LOG_ENTRIES]:
            who = "본인" if c.author_email in self.user_emails else "타인"
            subject = self._redact(c.message.splitlines()[0] if c.message else "")[:MAX_LINE_CHARS]
            rows.append(f"{c.sha[:8]} {c.when.date()} {who}: {subject}")
        note = f"\n... 전체 {len(commits)}개" if len(commits) > len(rows) else ""
        return "\n".join(rows) + note if rows else "(커밋 없음)"


def execute_tool(ws: RepoWorkspace, call: ToolCall) -> str:
    """도구 하나를 실행해 LLM에 돌려줄 관찰을 만든다. 잘못된 호출도 오류 관찰로 돌려준다."""
    if call.invalid_arguments:
        return data_block("tool_output", "오류: 인자가 올바른 JSON이 아닙니다")
    a = call.arguments
    try:
        if call.name == "tree":
            out = ws.tree(a.get("prefix", ""))
        elif call.name == "read_file":
            out = ws.read_file(a["path"], int(a.get("start", 1)), a.get("end") and int(a["end"]))
        elif call.name == "grep":
            out = ws.grep(a["query"], a.get("prefix", ""))
        elif call.name == "git_log":
            out = ws.git_log(a.get("path"))
        else:
            out = f"오류: 없는 도구입니다: {call.name}"
    except (KeyError, TypeError, ValueError) as exc:
        out = f"오류: 인자가 잘못됐습니다: {type(exc).__name__}"
    return data_block("tool_output", out)
