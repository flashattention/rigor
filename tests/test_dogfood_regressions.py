"""실제 이력서·계정으로 처음 돌렸을 때 나온 실패의 합성 재현 (docs/failure-taxonomy.md).

실데이터는 저장소에 넣지 않는다. 같은 실패를 일으키는 최소 입력만 만들어 고정한다.
"""

from datetime import UTC, datetime

from rigor.core.gaps import weaknesses
from rigor.github_audit.claims import check_claims_by_rules
from rigor.github_audit.exposure import ExposureContext, scan_exposure
from rigor.github_audit.selection import name_matched_emails
from rigor.github_audit.signals import compute_signals
from rigor.github_audit.snapshot import CommitInfo, RepoMeta, RepoSnapshot
from rigor.ingest.files import extract_text
from rigor.ingest.privacy import mask_pii
from rigor.nodes.project_planner import MAX_ITEMS_PER_REPO, MAX_ROADMAP_ITEMS, assemble_roadmap
from rigor.nodes.reconcile import MAX_CARDS, card_targets, suggest_revision
from rigor.schemas.common import Citation
from rigor.schemas.profile import (
    Attribution,
    Claim,
    ClaimType,
    Fact,
    FactCategory,
    FactStatus,
    Profile,
)
from rigor.schemas.repo import ClaimResult, DoneCriterion, Impact, Improvement, Mismatch

WHEN = datetime(2025, 1, 1, tzinfo=UTC)


def snap(paths, files, scope="api_partial", commits=()):
    return RepoSnapshot(RepoMeta(1, "me/r"), "sha", paths, files, list(commits), scope=scope)


def fact_with(claims, fid="f_1", **kw):
    return Fact(
        id=fid,
        category=FactCategory.PROJECT,
        content="프로젝트",
        status=FactStatus.CONFIRMED,
        claims=[
            Claim(id=f"c_{fid[2:]}_{i}", fact_id=fid, type=t, text=x)
            for i, (t, x) in enumerate(claims)
        ],
        **kw,
    )


def test_pdf_ligature_in_url_is_normalized_so_the_link_is_found():
    # 실패: PDF의 합자(ﬂ)로 저장소 링크 4개가 인식되지 않아 "저장소가 없다"고 잘못 지적했다
    text = extract_text("- 프로젝트\n  github.com/ﬂash-user/traﬃc-app\n".encode()).text
    assert mask_pii(text).repo_urls == {"[REPO_1]": "https://github.com/flash-user/traffic-app"}


def test_military_mention_inside_a_sentence_is_removed_but_the_rest_is_kept():
    # 실패: 문장 중간의 군 복무 언급이 LLM에 그대로 전달됐다
    m = mask_pii("홍길동\n육군 복무를 마치고 복학해 추천 시스템 연구를 시작했다\n")
    assert "육군" not in m.text and "복무" not in m.text
    assert "추천 시스템 연구" in m.text


def test_user_given_terms_are_masked_and_schools_are_kept_for_exposure_checks():
    # 실패: 대회명 속 학교 약칭은 패턴으로 잡을 수 없어 LLM에 전달됐다
    m = mask_pii("홍길동\n2025 XYZU 경진대회 은상\n가람대학교 졸업\n", extra_terms=["XYZU"])
    assert "XYZU" not in m.text and "[MASKED]" in m.text
    assert m.schools == ["가람대학교"]


def test_placeholders_and_firebase_web_keys_are_not_reported_as_secrets():
    # 실패: 문서의 자리표시자와 공개용 Firebase 웹 키를 비밀값으로 보고해 대표 판정을 막았다
    placeholder = 'OPENAI_API_KEY="your_key_here_please"\n'
    firebase = 'const firebaseConfig = {\n  apiKey: "AIzaSyAAAAAAAAAAAAAAAAAAAAAAA",\n};\n'
    real = 'password = "hunter2hunter2hunter2"\n'
    s = snap(
        ["README.md", "js/firebase-config.js", "app.py"],
        {"README.md": placeholder, "js/firebase-config.js": firebase, "app.py": real},
    )
    assert [f.path for f in scan_exposure(s, ExposureContext())] == ["app.py"]


def test_commits_with_the_same_author_name_are_alias_candidates_but_other_accounts_are_not():
    # 실패: 계정에 연결되지 않은 커밋을 놓쳐 34/78이 14/78로 집계됐다
    commits = [
        CommitInfo("a", "Me", "me@work.com", WHEN, "x", author_login="me"),
        CommitInfo("b", "Me", "me@laptop.local", WHEN, "x"),
        CommitInfo("c", "Other", "o@x.com", WHEN, "x"),
        CommitInfo("d", "Me", "namesake@x.com", WHEN, "x", author_login="namesake"),
    ]
    assert name_matched_emails("me", {1: snap([], {}, commits=commits)}) == ["me@laptop.local"]


def test_absent_tech_is_pending_not_not_found_when_files_were_not_all_read():
    # 실패: 일부 파일만 읽고 "LightGBM 근거 없음, 이력서 수정 필요"라고 단정했다
    f = fact_with([(ClaimType.TECH, "LightGBM 앙상블")])
    partial = snap(["README.md", "model/train.py"], {"README.md": "# r\n"})
    r = check_claims_by_rules(f, partial, compute_signals(partial), None)
    assert r.checks == [] and [c.text for c in r.pending] == ["LightGBM 앙상블"]
    full = snap(["README.md"], {"README.md": "# r\n"}, scope="local")
    r = check_claims_by_rules(f, full, compute_signals(full), None)
    assert r.checks[0].result is ClaimResult.NOT_FOUND


def test_revision_removes_only_the_listed_skill_not_the_whole_sentence():
    # 실패: 나열된 기술 하나 때문에 문장 전체 삭제를 제안했다
    listed = Claim(
        id="c_1", fact_id="f_1", type=ClaimType.TECH, text="XGBoost·LightGBM·CatBoost 앙상블"
    )
    assert suggest_revision(listed, Mismatch.MISSING_IN_CODE, None) == "XGBoost·CatBoost 앙상블"
    alone = Claim(id="c_2", fact_id="f_1", type=ClaimType.TECH, text="Redis 캐시로 응답 개선")
    assert suggest_revision(alone, Mismatch.MISSING_IN_CODE, None) == "[삭제]"


def test_team_project_describing_own_work_is_not_flagged_as_vague():
    # 실패: 본인이 한 일을 서술했는데 role 필드가 비었다고 W3를 냈다
    described = fact_with(
        [(ClaimType.FEATURE, "검색 파이프라인 설계")], attribution=Attribution.TEAM
    )
    empty = fact_with([], fid="f_2", attribution=Attribution.TEAM)
    found = weaknesses(Profile(id="p", facts=[described, empty]), {}, [])
    w3 = next(w for w in found if w.category == "W3")
    assert [c.ref_id for c in w3.citations] == ["f_2"]


def test_interview_cards_are_spread_across_items():
    # 실패: 카드 8장이 전부 첫 항목에서 나왔다
    many = fact_with([(ClaimType.ROLE, f"역할 {i}") for i in range(10)], fid="f_1")
    other = fact_with([(ClaimType.METRIC, "지연 30% 감소")], fid="f_2")
    targets = card_targets(Profile(id="p", facts=[many, other]), {})
    assert len(targets) == MAX_CARDS
    assert {f.id for f, *_ in targets} == {"f_1", "f_2"}


def test_roadmap_is_capped_and_cheap_low_impact_items_do_not_come_first():
    # 실패: 라이선스·CI 항목이 저장소마다 반복돼 26개가 나왔고 라이선스가 1순위였다
    def imp(key, impact, effort):
        return Improvement(
            id=key,
            title=key,
            description="d",
            done_criteria=[DoneCriterion(signal_path="ci.exists", op="==", value=True)],
            effort_days=effort,
            impact=impact,
            citations=[Citation(kind="requirement", ref_id="r_1")],
        )

    per_repo = [
        imp("license", Impact.LOW, 0.1),
        imp("ci", Impact.MED, 0.5),
        imp("tests", Impact.HIGH, 1.0),
        imp("readme", Impact.MED, 1.0),
    ]
    road = assemble_roadmap({rid: per_repo for rid in range(1, 6)}, [], [], 100)
    assert len(road.items) <= MAX_ROADMAP_ITEMS
    assert road.items[0].title == "tests"
    assert all(
        sum(i.repo_id == rid for i in road.items) <= MAX_ITEMS_PER_REPO for rid in range(1, 6)
    )


def test_listed_skills_are_supported_only_when_all_are_present():
    # 실패: 나열된 기술 중 하나만 저장소에 있어도 문장 전체를 "확인됨"으로 판정했다
    s = snap(
        ["pyproject.toml"],
        {"pyproject.toml": '[project]\nname="r"\ndependencies=["fastapi","lightgbm"]\n'},
        scope="local",
    )
    signals = compute_signals(s)
    both = fact_with([(ClaimType.TECH, "FastAPI와 LightGBM 사용")])
    assert check_claims_by_rules(both, s, signals, None).checks[0].result is ClaimResult.SUPPORTED
    partly = fact_with([(ClaimType.TECH, "FastAPI와 Redis 사용")])
    r = check_claims_by_rules(partly, s, signals, None)
    assert r.checks == [] and [c.text for c in r.pending] == ["FastAPI와 Redis 사용"]
