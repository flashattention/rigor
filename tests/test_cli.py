import re

from typer.testing import CliRunner

from rigor.cli import app

runner = CliRunner()


def test_routing_show_lists_tasks():
    result = runner.invoke(app, ["routing", "show", "--profile", "anthropic_haiku"])
    assert result.exit_code == 0
    assert "requirement_verdict" in result.output
    assert "anthropic/claude-haiku-5-5" in result.output


def test_cost_estimate_batch_halves_cost():
    full = runner.invoke(app, ["cost", "estimate", "--runs", "170", "--profile", "anthropic_haiku"])
    batch = runner.invoke(
        app, ["cost", "estimate", "--runs", "170", "--profile", "anthropic_haiku", "--batch"]
    )
    assert full.exit_code == batch.exit_code == 0
    krw = [int(re.search(r"₩([\d,]+)", r.output).group(1).replace(",", "")) for r in (full, batch)]
    assert 5_500 < krw[0] < 6_300  # docs/cost-estimate.md: 전체 평가 ≈ ₩6,000
    assert abs(krw[1] - krw[0] / 2) <= 1


def test_unverified_price_is_flagged():
    result = runner.invoke(app, ["cost", "estimate", "--runs", "1", "--profile", "openai_phase1"])
    assert "미검증" in result.output


def test_synth_list_and_build(tmp_path):
    listed = runner.invoke(app, ["synth", "list"])
    assert listed.exit_code == 0 and "A_agent_strong" in listed.output
    out = tmp_path / "case"
    built = runner.invoke(app, ["synth", "build", "E_ai_and_exposure", "--out", str(out)])
    assert built.exit_code == 0, built.output
    assert (out / "resume.txt").exists() and (out / "truth.json").exists()
    assert (out / "repos" / "synth-user-e" / "todo-api" / ".git").is_dir()


def test_diagnose_synth_case_with_oracle(tmp_path):
    out = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "diagnose",
            "--synth-case",
            "C_knockout_degree",
            "--use-truth-profile",
            "--yes",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "not_recommended (G1)" in result.output
    assert "비추천" in out.read_text()
