import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("select_repos", Path("evals/agent/select_repos.py"))
sel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)

META = {"name": "search-engine", "description": "벡터 검색 서비스", "owner": {"login": "someone"}}
PATHS = [f"src/m{i}.py" for i in range(30)]


def test_eligible_applies_every_rule():
    py = (".py",)
    assert sel.eligible(META, PATHS, py, "me")
    assert not sel.eligible({**META, "fork": True}, PATHS, py, "me")
    assert not sel.eligible({**META, "owner": {"login": "me"}}, PATHS, py, "me")
    assert not sel.eligible({**META, "name": "awesome-llm"}, PATHS, py, "me")
    assert not sel.eligible({**META, "description": "A tutorial"}, PATHS, py, "me")
    assert not sel.eligible(META, PATHS[:5], py, "me")
    assert not sel.eligible(META, [f"m{i}.py" for i in range(400)], py, "me")


def test_split_is_balanced_and_order_does_not_depend_on_content():
    assert [sel.split_of("x", i) for i in range(4)] == ["dev", "test", "dev", "test"]
    assert sel.order_key("a/b") == sel.order_key("a/b") != sel.order_key("a/c")
