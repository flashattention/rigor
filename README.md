# RIGOR — Resume Inspection with GitHub for Offer Readiness

**AI/IT 개발직군 지원자를 위한 솔직한 취업 컨설팅 에이전트: 진단 · GitHub 감사 · 프로젝트 로드맵**

채용공고·이력서·GitHub 저장소를 실제로 확인해서 두 가지를 말해 줍니다.
1. 그 공고에 대한 **직무 적합도**: 근거 기반 등급과 요건별 근거
2. 합격 가능성을 높이려면 **어떤 프로젝트를 골라 어떻게 개선해야 하는지**: 대표로 쓸 것 / 보완할 것 / 뺄 것 / 새로 할 것

> 상태: **M1 개발 중** (핵심 흐름 v0, CLI). 서비스는 아직 공개되지 않았습니다.

## 원칙
- **근거 없는 말은 하지 않습니다.** 모든 지적·추천은 공고 요건, 사용자가 확인한 이력 사실, 저장소 근거 중 하나를 인용합니다.
- **합격 확률 대신 등급**을 냅니다. 보정할 실제 결과 데이터가 없는 숫자는 사용자를 오도하기 때문입니다 ([ADR-0013](docs/adr/0013-grades-not-probabilities.md)).
- **사실부터 맞추고 판단합니다.** 이력서 주장을 코드와 커밋 이력에 대조하고, 맞춘 이력서로만 적합도를 판단합니다 ([ADR-0017](docs/adr/0017-core-graph-resume-repo-reconciliation.md)).
- **등급은 규칙으로 계산합니다.** 같은 근거면 같은 등급이 나오고, 압박해도 바뀌지 않습니다 ([ADR-0008](docs/adr/0008-grading-rules.md)).
- **보호 속성은 모델에 전달하지 않습니다.** 성별·나이·사진·학교명·출신 지역 등 ([ADR-0014](docs/adr/0014-exclude-protected-attributes.md)).
- **지원자 편입니다.** 기업용 평가 기능이나 유료 상위 노출은 없습니다 ([ADR-0015](docs/adr/0015-applicant-side-no-b2b.md)).

## 문서
- [설계](docs/design.md) · [ADR 목록](docs/adr/README.md) · [스키마](docs/schemas.md) · [진단 루브릭](docs/rubric.md) · [지표](docs/metrics.md)
- [공정성 정책](docs/ethics/fairness-policy.md) · [위험 등록부](docs/ethics/risk-register.md) · [규제 검토](docs/ethics/regulatory-review.md)
- [M0 보고와 M1 계획](docs/milestones/m0-report.md)

## 개발
```bash
uv sync --all-extras          # 의존성 설치
uv run pytest                 # 테스트 (네트워크·실제 LLM 없이 실행됨)
uv run ruff check . && uv run ruff format --check .
uv run rigor routing show --profile anthropic_haiku   # task별 모델 확인
uv run rigor cost estimate --runs 170 --batch --profile anthropic_haiku   # 평가 비용 추정
```
LLM 제공자·모델은 [configs/routing.yaml](configs/routing.yaml)에서 바꿉니다. 기본 프로필은 `fake`라서 비용이 들지 않습니다.

## 라이선스
[MIT](LICENSE)
