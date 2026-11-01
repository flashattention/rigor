"""합성 저장소 파일 생성기. 품질 신호(README 섹션, 테스트, CI, 라이선스)를 설계대로 심는다."""

from __future__ import annotations

import random
import string

SECTION_TITLES = {
    "problem": "문제 정의",
    "design": "설계",
    "setup": "실행 방법",
    "results": "결과",
}


def readme(title: str, sections: dict[str, str], extra: str = "") -> str:
    parts = [f"# {title}", ""]
    for key, body in sections.items():
        parts += [f"## {SECTION_TITLES.get(key, key)}", body, ""]
    if extra:
        parts += [extra, ""]
    return "\n".join(parts)


def pyproject(name: str, deps: list[str]) -> str:
    dep_lines = "\n".join(f'    "{d}",' for d in deps)
    return f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = [\n{dep_lines}\n]\n'


def requirements(deps: list[str]) -> str:
    return "\n".join(deps) + "\n"


def package_json(name: str, deps: list[str]) -> str:
    dep_lines = ",\n".join(f'    "{d}": "^1.0.0"' for d in deps)
    return f'{{\n  "name": "{name}",\n  "dependencies": {{\n{dep_lines}\n  }}\n}}\n'


def py_module(imports: list[str], body: str) -> str:
    head = "\n".join(f"import {i}" for i in imports)
    return f"{head}\n\n\n{body.strip()}\n"


def test_file(target: str) -> str:
    return f"from {target} import *  # noqa\n\n\ndef test_smoke():\n    assert True\n"


def ci_workflow() -> str:
    return (
        "name: ci\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v5\n      - run: pip install -e . pytest\n"
        "      - run: pytest\n"
    )


def mit_license(holder: str) -> str:
    return f"MIT License\n\nCopyright (c) 2025 {holder}\n\nPermission is hereby granted...\n"


def fake_aws_credentials(seed: int) -> tuple[str, str]:
    """AWS 키 형식의 가짜 값. 소스에 리터럴로 두지 않으려고 실행 시점에 만든다."""
    rng = random.Random(seed)
    key_id = "AK" + "IA" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=16))
    secret = "".join(rng.choices(string.ascii_letters + string.digits + "/+", k=40))
    return key_id, secret
