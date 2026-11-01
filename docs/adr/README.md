# Architecture Decision Records

형식: 배경 → 검토한 선택지(비교표) → 결정 → 이유 → 결과·트레이드오프(포기한 것) → 재검토 조건.
상태: `제안됨`(승인 대기) · `채택됨` · `대체됨(→ ADR-xxxx)` · `폐기됨`.
번호는 재사용하지 않는다. 결정을 바꿀 때는 새 ADR을 쓰고 기존 ADR을 `대체됨`으로 표시한다.

| # | 제목 | 상태 |
|---|---|---|
| [0001](0001-backend-framework.md) | 백엔드 프레임워크 | 채택됨 |
| [0002](0002-agent-framework.md) | 에이전트 프레임워크와 그래프 구조 | 채택됨 |
| [0003](0003-tracing.md) | LLM 트레이싱 도구 | 채택됨 |
| [0004](0004-deployment-infra.md) | 배포처·인프라 | 채택됨 |
| [0005](0005-job-posting-collection.md) | 공고 수집 방식 | 채택됨 |
| [0006](0006-github-analysis.md) | GitHub 분석 방식 (API vs clone, 격리, 캐시) | 채택됨 |
| [0007](0007-north-star-metric.md) | North Star Metric | 채택됨 |
| [0008](0008-grading-rules.md) | 등급 산출 규칙 (결정론 + 근거 있는 기준값) | 채택됨 |
| [0009](0009-llm-provider-routing.md) | LLM 제공자·모델 라우팅 (Jev 검토 포함) | 채택됨 |
| [0010](0010-vector-db-rag-scope.md) | 벡터 DB·RAG 도입 범위 | 채택됨 |
| [0011](0011-resume-input-formats.md) | 이력서 입력 형식 (HWP·스캔 PDF 포함) | 채택됨 |
| [0012](0012-direct-tone-only.md) | 진단 어조를 직설로 고정 | 채택됨 |
| [0013](0013-grades-not-probabilities.md) | 합격 확률 대신 근거 기반 등급 | 채택됨 |
| [0014](0014-exclude-protected-attributes.md) | 보호 속성을 판정 신호에서 제외 (구조적 차단) | 채택됨 |
| [0015](0015-applicant-side-no-b2b.md) | 지원자 편 원칙 — B2B 채용 평가와 유료 노출 금지 | 채택됨 |
| [0016](0016-defer-matching.md) | 매칭 기능을 M4 이후로 이연 | 채택됨 |
| [0017](0017-core-graph-resume-repo-reconciliation.md) | 핵심 흐름 그래프 재구성 — 이력서–저장소 정합 우선 | 채택됨 |

후보(아직 작성 안 함): 프론트엔드(M3).
