# Architecture Decision Records

형식: 배경 → 검토한 선택지(비교표) → 결정 → 이유 → 결과·트레이드오프(포기한 것) → 재검토 조건.
상태: `제안됨`(승인 대기) · `채택됨` · `대체됨(→ ADR-xxxx)` · `폐기됨`.
번호는 재사용하지 않는다. 결정을 바꿀 때는 새 ADR을 쓰고 기존 ADR을 `대체됨`으로 표시한다.

| # | 제목 | 상태 |
|---|---|---|
| [0001](0001-backend-framework.md) | 백엔드 프레임워크 | 제안됨 |
| [0002](0002-agent-framework.md) | 에이전트 프레임워크와 그래프 구조 | 제안됨 |
| [0003](0003-tracing.md) | LLM 트레이싱 도구 | 제안됨 |
| [0004](0004-deployment-infra.md) | 배포처·인프라 | 제안됨 |
| [0005](0005-job-posting-collection.md) | 공고 수집 방식 | 제안됨 |
| [0006](0006-github-analysis.md) | GitHub 분석 방식 (API vs clone, 격리, 캐시) | 제안됨 |
| [0007](0007-north-star-metric.md) | North Star Metric | 제안됨 |

후보(아직 작성 안 함): 확률 대신 등급 · 등급 산출의 결정론화 · 보호 속성 제외 · B2B 확장 금지 · 매칭 기능 이연 · LLM 제공자·모델 선택 · 프론트엔드.
