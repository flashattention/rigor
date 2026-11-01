# ADR-0004: 배포처·인프라

- 상태: 채택됨 (2026-11-01)
- 날짜: 2026-11-01
- 관련: ADR-0001, ADR-0006(격리 분석), `docs/design.md` §4

## 배경
필요한 구성: API 서비스, 워커(긴 그래프 실행), **격리된 일회성 분석 작업**(저장소 clone·정적 분석), 관리형 Postgres(+pgvector), 스케줄 작업, 시크릿 관리. 제약: 1인 운영, 사용자 수십 명 규모, 낮은 고정비, 신뢰할 수 없는 코드를 다루므로 **격리 수준**이 중요하다.

## 검토한 선택지 — 컴퓨트

| 기준 | Google Cloud Run (+Jobs) | Fly.io (Machines) | Railway | Render | AWS ECS Fargate |
|---|---|---|---|---|---|
| 격리 수준 | gVisor 샌드박스 기반 실행 환경 | Firecracker microVM | 컨테이너 | 컨테이너 | Firecracker 기반 |
| 일회성 작업 | **Cloud Run Jobs** 네이티브 | Machines API로 생성·삭제 | 크론·서비스 | 크론 잡 | RunTask |
| 0으로 축소 | O | O(자동 정지) | 제한적 | 유료 플랜 상시 | 작업 단위 과금 |
| 시크릿 | Secret Manager | fly secrets | 변수 | 변수 | Secrets Manager |
| 송신 네트워크 제한 | VPC egress 설정·방화벽 | 제한적 | 제한적 | 제한적 | 보안 그룹 |
| 운영 복잡도 | 중간 | 중간 | **낮음** | 낮음 | 높음 |
| IaC | Terraform 성숙 | flyctl/Terraform | 제한적 | Blueprint | Terraform 성숙 |
| 서울 리전 | 있음 | 있음(확인 필요) | 확인 필요 | 확인 필요 | 있음 |

## 검토한 선택지 — DB

| 기준 | Neon | Supabase | Cloud SQL | 컴퓨트와 같은 PaaS의 Postgres |
|---|---|---|---|---|
| pgvector | O | O | O | 제공처별 |
| 무료/저가 시작 | 무료 티어(축소 시 0) | 무료 티어(비활성 시 일시정지) | 최소 인스턴스 고정비 | 제공처별 |
| 브랜치(테스트 DB) | **DB 브랜치** | 제한적 | 없음 | 없음 |
| 부가 기능 | 순수 Postgres | 인증·스토리지 포함 | 백업·HA 성숙 | — |

(가격은 결정 시점에 공식 페이지로 확인해 기록한다.)

## 결정
- **컴퓨트: Cloud Run** — API·워커는 서비스, 저장소 분석은 **Cloud Run Jobs**(요청 단위 일회성 실행, 전용 최소 권한 서비스 계정, 송신은 GitHub만 허용).
- **DB: Neon** (서울 근접 리전 가용성 확인 필요) — Postgres 하나로 앱 데이터·작업 큐·LangGraph checkpointer·pgvector를 모두 처리.
- **프론트엔드**: M3에서 별도 결정(Q4).
- **IaC**: Terraform, `infra/`. CI는 GitHub Actions → Artifact Registry → Cloud Run.

## 이유
1. 저장소 분석 격리가 이 서비스에서 가장 위험한 부분이다. Cloud Run Jobs는 **일회성 실행 + gVisor 샌드박스 + 서비스 계정 최소 권한 + 송신 제한**을 별도 인프라 없이 조합할 수 있다.
2. 0으로 축소되어 베타 규모의 고정비가 낮다.
3. Neon의 DB 브랜치로 평가·마이그레이션 테스트용 DB를 쉽게 만든다.

## 결과·트레이드오프 (포기한 것)
- Railway/Render 대비 초기 설정(IAM, VPC egress, Terraform)이 무겁다 → M3에 1~2일 추가 소요 예상.
- 클라우드 2곳(GCP + Neon)에 걸친다 → DB 지연·장애 지점이 하나 늘어난다. 리전을 최대한 가깝게 맞춘다.
- Cloud Run 서비스는 요청 시간 상한이 있으므로 긴 그래프 실행은 워커(상시 최소 인스턴스 0~1)에서 처리한다.

## 재검토 조건
- GCP 설정 부담으로 M3 일정이 1주 이상 밀릴 때 → Fly.io(Machines로 격리 작업)로 전환 검토.
- 월 인프라 비용이 예산 상한(Q5)을 넘을 때.

## 승인 기록 (2026-11-01)
- 승인: `flashattention`. 저장소 분석 격리(Cloud Run Jobs)와 0으로 축소되는 구조를 이유로 채택.
- **비용 관점 — 목표는 인프라 월 0원**(LLM에 예산을 남기기 위해):
  - Cloud Run·Cloud Run Jobs·Artifact Registry·Secret Manager는 무료 사용량 안에서 운영. 최소 인스턴스 0, 최대 인스턴스 1~2로 제한.
  - Neon 무료 플랜. Cloud SQL(고정비) 사용 안 함.
  - 도메인 구매 안 함(제공 서브도메인 사용). 필요해지면 별도 승인.
  - **주의: GCP 예산 알림은 알림일 뿐 지출을 멈추지 않는다.** 따라서 ① 결제 계정 예산 알림(₩3,000 / ₩5,000) ② 최대 인스턴스·작업 동시성 상한 ③ 알림 시 서비스 축소 스크립트(런북)를 함께 둔다. 신규 계정 무료 체험 크레딧이 있으면 활용하되 만료일을 런북에 기록.
  - 무료 사용량 수치는 자주 바뀌므로 M3 배포 직전 공식 페이지로 확인해 이 ADR에 기록한다.
