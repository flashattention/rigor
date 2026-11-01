# 제품 분석 이벤트 스키마 (M0 초안)

> 퍼널·NSM(ADR-0007)·리텐션을 측정하기 위한 이벤트 정의. 실제 수집은 M3부터 한다. 분석 도구 선택은 M3에서 ADR로 정한다(Q27). 이 스키마는 도구와 무관하게 유지한다.

## 원칙
1. **이벤트에 개인정보와 내용을 넣지 않는다.** 이력서 텍스트, 공고 원문, 회사명, 저장소 이름·URL, 이메일, GitHub 로그인, IP를 넣지 않는다.
2. 사용자 식별자는 서비스 내부 ID의 **해시**(분석 전용 salt)만 쓴다. 계정을 삭제하면 매핑을 지워 재식별할 수 없게 한다.
3. 수량·범주·버전만 담는다(예: 저장소 개수, 등급, 프롬프트 버전 해시).
4. 쿠키·추적 동의 범위 안에서만 수집한다. 동의하지 않은 사용자는 서버 측 집계 지표(익명 카운트)만 남긴다.

## 공통 속성
| 속성 | 타입 | 설명 |
|---|---|---|
| `event` | str | 이벤트 이름 |
| `ts` | datetime (UTC) | |
| `uid_hash` | str | 내부 사용자 ID 해시 (가입 전 `null`) |
| `session_id` | str | 무작위 세션 ID |
| `app_version` | str | git SHA |
| `routing_hash` | str? | LLM 라우팅 설정 해시 (진단 관련 이벤트) |
| `env` | enum `prod` \| `staging` | |

## 이벤트 목록
| 이벤트 | 시점 | 추가 속성 | 쓰는 지표 |
|---|---|---|---|
| `landing_viewed` | 랜딩 방문 | `referrer_channel` (범주: community/search/direct/other) | 퍼널, 채널별 유입 |
| `signed_up` | 가입 완료 | `auth_provider` | 퍼널 |
| `github_connected` | GitHub App 설치 완료 | `repo_count_selected`, `private_selected: bool` | 퍼널 |
| `resume_uploaded` | 업로드 성공 | `format` (pdf_text/pdf_scan/docx/hwp/hwpx/image/paste), `pages` | 형식 분포, OCR 비중 |
| `resume_parse_failed` | 파싱 실패 | `format`, `error_type` | 실패 분류 |
| `facts_confirmed` | A4 완료 | `fact_count`, `edited_count`, `low_confidence_count` | 추출 품질, 입력 완료율 |
| `identity_confirmed` | A6 완료 | `alias_count` | 기여 귀속 |
| `unlinked_resolved` | B3 완료 | 사유별 개수(`no_repo`, `low_quality`, `forgot`, `private`), `candidate_accepted_count` | 후보 추천 정확도(실사용) |
| `reconciliation_completed` | D1 완료 | 불일치 유형별 개수, 선택별 개수(`revise_to_fact`, `implement_then_reaudit`, `add_evidence`, `keep_as_is`, `later`) | 정직성·입력 완료율 |
| `diagnosis_completed` | 리포트 생성 | `grade`, `requirement_count`, `latency_ms`, `cost_usd_est`, `verifier_retries` | 퍼널, 원가, 지연 |
| `diagnosis_failed` | 실패 | `stage` (A~F), `error_type` | 성공률 |
| `item_feedback` | 항목별 피드백 | `item_type` (verdict/gap/weakness/roadmap/card), `feedback` (agree/disagree/already_fixed) | "맞는 말" 비율 |
| `fact_dispute_reported` | "사실과 다름" 신고 | `item_type` | 인용 정확도(실사용) |
| `roadmap_accepted` | 로드맵 수락 | `accepted_count`, `kind_counts` | 퍼널 |
| `reaudit_requested` | 재감사 요청 | `repo_count` | NSM 입력 |
| `improvement_verified` | 완료 기준 충족 확인 | `item_kind`, `days_since_accept` | **NSM** |
| `application_logged` | 지원 기록(선택 동의) | `grade_at_apply` | 결과 회수 |
| `outcome_logged` | 결과 입력(선택 동의) | `outcome`, `days_since_apply` | 회수율, 등급별 합격률 |
| `account_deleted` | 계정 삭제 | — | 삭제 처리 시간 |

## NSM 계산
`WVI(주) = count_distinct(uid_hash) where event = improvement_verified and ts in [주 시작, 주 끝)`

## 보존
원시 이벤트 13개월, 이후 집계만 남긴다. 계정 삭제 시 `uid_hash` 매핑을 파기한다.
