# 데이터 스키마 (M0 초안)

> M1에서 `app/rigor/schemas/`의 Pydantic 모델로 구현한다. 이 문서가 단일 기준이며, 구현과 다르면 문서를 먼저 고친다.
> 표기: `?` = nullable, `[]` = 리스트. **보호** 열: 🔒 = 개인정보(암호화 저장), ⛔ = 매칭·진단 신호로 사용 금지(공정성 정책), 🗑 = 저장하지 않음.

---

## 1. 공통

### 1.1 Citation — 모든 지적·추천의 근거
| 필드 | 타입 | 설명 |
|---|---|---|
| kind | enum `requirement` \| `fact` \| `repo_evidence` | 근거 종류 |
| ref_id | str | `Requirement.id` / `Fact.id` / `RepoEvidence.id` |
| quote | str? | 원문 일부(span). 공고·저장소 근거일 때 권장 |

규칙: 진단·추천 항목은 `citations: Citation[]`(min 1). 비어 있으면 Renderer 이전에 제거되고 `dropped_uncited` 카운터가 증가한다(지표: 근거 없는 지적 비율).

### 1.2 Provenance — 재현용 메타데이터
`prompt_version, model_id, rubric_version, taxonomy_version, analyzer_version, created_at, trace_id`

---

## 2. 지원자 프로필 (사실 DB)

### 2.1 Profile
| 필드 | 타입 | 보호 | 설명 |
|---|---|---|---|
| id | uuid | | |
| user_id | uuid | | |
| display_name | str? | 🔒⛔ | 화면 표시·블라인드 노출 점검용. LLM에는 마스킹 토큰으로만 |
| contact | {email?, phone?, links[]} | 🔒⛔ | 노출 점검용 |
| target_roles | RoleId[] | | 사용자가 고른 목표 직무 (taxonomy) |
| preferences | {locations[], work_type[], company_size[]} | | 매칭(M4) 전까지는 저장만 |
| available_hours_per_week | int? | | 로드맵 범위 조정 |
| deadline | date? | | 목표 마감일 |
| tone | enum `direct` \| `gentle` | | 진단 강도. **판정에는 영향 없음** |
| facts | Fact[] | | |

### 2.2 Fact
| 필드 | 타입 | 보호 | 설명 |
|---|---|---|---|
| id | str (`f_` + 단축 uuid) | | 인용에 쓰인다 |
| category | enum `experience` \| `project` \| `education` \| `award` \| `certification` \| `language` \| `skill` | | |
| content | str | 🔒 | 사실 서술 1개(한 fact = 한 주장) |
| period | {start: date?, end: date?, ongoing: bool} | | 일 단위, 모르면 null |
| organization | str? | 🔒⛔(학교) | 경력은 회사명(판정 사용 가능), **학력의 학교명은 판정 금지** |
| education_normalized | {degree: enum `none`\|`associate`\|`bachelor`\|`master`\|`phd`, field: str?, status: enum `enrolled`\|`graduated`\|`expected`\|`dropped`}? | | 학위 요건 판정에는 이 값만 사용 |
| role | str? | | 맡은 역할 |
| attribution | enum `individual` \| `team` \| `mixed` | | 본인 기여 vs 팀 성과 |
| contribution_note | str? | | 기여도 — **사용자 확인값만**, 추정 금지 |
| implementation | enum `hand_written` \| `ai_assisted` \| `mostly_ai` \| `unknown` | | 자기 신고. 감점 신호 아님 — 면접 준비 안내에만 사용 |
| skills | SkillId[] | | taxonomy의 기술 정규화 |
| metrics | {name, value, unit, evidence_url?}[] | | 성과 수치. 근거 없으면 `evidence_url=null`로 두고 리포트에서 "근거 링크 필요" |
| evidence_links | url[] | | 저장소·배포·논문 등 |
| linked_repos | RepoRef[] | | Repo Auditor 대조 대상 |
| disclosable_in_blind | bool | | 블라인드 공고에서 노출 가능 여부 |
| status | enum `confirmed` \| `unconfirmed` \| `rejected` | | **판정에는 `confirmed`만 사용** |
| source | enum `resume_extract` \| `user_input` \| `repo_derived` | | |

### 2.3 추출 시 버리는 항목 (🗑, 저장·표시하지 않음)
사진, 생년월일·나이, 성별, 주소(시·도 이상 포함), 병역, 장애·건강, 가족관계, 종교, 혼인 여부, 출신 지역. 추출기 출력 스키마에 해당 필드 자체가 없고, 원문에서 발견 시 `discarded_sensitive_count`만 기록.

---

## 3. 공고

### 3.1 JobPosting
| 필드 | 타입 | 설명 |
|---|---|---|
| id | uuid | |
| owner_user_id | uuid | ADR-0005: 붙여넣은 공고는 사용자 범위 |
| source | {kind: enum `user_paste`\|`public_api`\|`partner`\|`ats_board`, name?, url?, collected_at, terms_version?} | |
| raw_text_hash | str | 파싱 캐시 키 |
| company | str | 판정 신호 아님(정책 검사에서 타사명 혼입 점검용) |
| title | str | 원문 직무명 |
| role_ids | {role: RoleId, confidence: float}[] | taxonomy 투영. 직무명과 실제 업무가 다를 수 있으므로 본문 기반 |
| language | enum `ko` \| `en` \| `other` | |
| blind | bool | 블라인드 채용 여부 |
| deadline | date? | |
| employment | {type?, location?, remote?: enum} | |
| experience_years | {min?: float, max?: float, strict: bool} | strict = 공고에 "필수"로 명시 |
| requirements | Requirement[] | |
| responsibilities | str[] | 직무 실체 파악용 |
| parse_warnings | str[] | 모호함·인젝션 의심 |

### 3.2 Requirement
| 필드 | 타입 | 설명 |
|---|---|---|
| id | str (`r_` + 순번) | |
| text | str | 원문 span (그대로 인용) |
| kind | enum `knockout` \| `required` \| `preferred` | knockout = 결격 조건(학위·자격증·근무지·비자·명시적 필수 연차) |
| knockout_type | enum `degree` \| `certification` \| `experience_years` \| `location` \| `work_authorization` \| `language_cert` \| `other`? | |
| competencies | CompetencyId[] | taxonomy 역량 축 |
| skills | SkillId[] | |
| min_depth | 0–3? | 필요한 숙련 깊이 (taxonomy §3) |
| legal_check | enum `ok` \| `flag` | 보호 속성 기반 요건(성별·나이 등)이면 `flag` → 판정에서 무시하고 리포트에 안내 |

---

## 4. GitHub 감사

### 4.1 RepoRef / RepoAudit
| 필드 | 타입 | 설명 |
|---|---|---|
| repo_id | int | GitHub 저장소 id (이름 변경에 안전) |
| full_name | str | |
| visibility | enum `public` \| `private` | |
| head_sha | str | 캐시 키 |
| is_fork | bool | 포크 + 변경 없음 → 제외 후보 |
| user_commit_share | float? | 사용자 커밋 비중 — **판정 아님**, `contribution_caveat` 항상 동반 |
| signals | RepoSignals | 결정론 신호 |
| evidence | RepoEvidence[] | 인용 가능한 근거 |
| claim_checks | ClaimCheck[] | 이력서 ↔ 저장소 대조 |
| exposure_findings | ExposureFinding[] | 비밀값·실명·연락처·기관 데이터 |
| rubric_scores | {item_id: score}[] | `docs/rubric.md` §4 |
| verdict_for_jd | enum `showcase` \| `improve` \| `drop` | (저장소, 공고) 쌍 기준 |
| improvements | Improvement[] | 보완 항목 |
| scan_scope | enum `api_partial` \| `full_clone` | 리포트에 명시 |
| provenance | Provenance | |

### 4.2 RepoSignals (결정론)
`readme: {exists, length, sections: [problem|design|setup|results|...]}, tests: {exists, frameworks[], file_count}, ci: {exists, providers[]}, license: str?, deploy: {config_files[], homepage_url?}, languages: {lang: bytes}, dependencies: SkillId[], commits: {total, user, first_at, last_at, active_weeks}, size_kb, injection_suspects: int`

### 4.3 ClaimCheck
| 필드 | 타입 | 설명 |
|---|---|---|
| fact_id | str | 대조한 이력서 fact |
| claim | str | fact 안의 개별 주장 (예: "LangGraph 사용") |
| result | enum `supported` \| `not_found` \| `contradicted` \| `unverifiable` | `unverifiable` = 저장소로 확인할 수 없는 성격(예: 사내 성과) |
| evidence | Citation[] | supported/contradicted면 필수 |
| action | str | "이력서 수정 필요: ..." / "근거 링크 추가" |

### 4.4 Improvement
`id, title, description, done_criteria: DoneCriterion[], effort_days: float, impact: enum low|med|high, citations: Citation[] (min 1, 공고 요건 포함 권장)`
`DoneCriterion = {signal_path, op, value}` — 재감사의 결정론 신호로 자동 확인 가능한 형태 (예: `signals.tests.exists == true`, `signals.readme.sections contains "results"`). NSM(ADR-0007) 측정에 쓰인다.

---

## 5. 진단·로드맵 출력

### 5.1 RequirementVerdict
`requirement_id, status: enum met|partial|unmet|unknown, citations: Citation[], rationale: str`
- `unknown` = 판단 근거 부족 → 사용자에게 질문 생성(지어내지 않음).

### 5.2 Diagnosis
| 필드 | 타입 | 설명 |
|---|---|---|
| grade | enum `strong` \| `apply` \| `gap_first` \| `not_recommended` | 강력 추천 / 지원 가능 / 갭 보완 후 / 비추천. **규칙 계산** |
| grade_trace | {rule_id, inputs} | 어떤 규칙으로 등급이 나왔는지 |
| verdicts | RequirementVerdict[] | |
| gaps | Gap[] | |
| weaknesses | {category, text, citations[]}[] | 서류상 약점 (루브릭 §3) |
| redirect | {role_id, reason, citations[]}? | 방향 전환 제안 |
| alternatives | {text, citations[]}[] | `not_recommended`면 min 1 |
| questions_for_user | str[] | unknown 해소용 |
| provenance | Provenance | |

### 5.3 Gap
`id, requirement_id, type: enum (a) expression | (b) missing_fact | (c) knockout, severity: enum critical|major|minor, next_action: str, citations[]`

### 5.4 Roadmap
`items: RoadmapItem[]` — `RoadmapItem = {kind: enum new_project|improve_repo, title, problem, tech: SkillId[], done_criteria: DoneCriterion[], resume_line_example, effort_days, priority, addresses_requirements: requirement_id[] (min 1), repo_id?}`. 사용자 가용 시간 × 마감일로 총 `effort_days` 상한.

---

## 6. 결과 피드백 (M3+, 별도 선택 동의)
`Application = {id, user_id, job_posting_id, diagnosis_id, grade_at_apply, applied_at, outcome: enum pending|pass|fail|no_response|unknown|withdrawn, outcome_at?, consent_version}` 🔒
분석 시 회사명은 직무 유형으로 일반화하고 집계 후에만 사용.
