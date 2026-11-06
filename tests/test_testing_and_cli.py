import json
import sys

import yaml

from auto_pr_test import cli, diff as diffmod, testing
from auto_pr_test.policy import Testing

from conftest import ROOT, fake_adapter


def test_change_coverage_from_pytest_cov_json(tmp_path, sample_diff):
    # The PR adds lines 13, 14 (code) and 15 (blank, not counted). Line 13 executed.
    (tmp_path / "coverage.json").write_text(json.dumps({
        "totals": {"percent_covered": 81.5},
        "files": {"src/auto_pr_test/diff.py": {"executed_lines": [1, 2, 13]}}}))
    cfg = Testing(command=f"{sys.executable} -c pass", coverage_file="coverage.json")
    rep = testing.run(cfg, diffmod.parse(sample_diff), cwd=tmp_path, base_coverage=80.0)
    assert rep.ran and rep.passed
    assert rep.total_coverage == 81.5 and rep.coverage_delta == 1.5
    assert rep.changed_lines == 2 and rep.changed_lines_covered == 1
    assert rep.uncovered == {"src/auto_pr_test/diff.py": [14]}
    assert rep.change_coverage == 50.0


def test_failing_command(tmp_path, sample_diff):
    cfg = Testing(command=f"{sys.executable} -c 'raise SystemExit(3)'")
    rep = testing.run(cfg, diffmod.parse(sample_diff), cwd=tmp_path)
    assert rep.ran and not rep.passed and rep.exit_code == 3


def test_cli_dry_run_and_verify(tmp_path, sample_diff, monkeypatch, capsys):
    (tmp_path / ".auto-pr-test").mkdir()
    pol = yaml.safe_load((ROOT / ".auto-pr-test/policy.yaml").read_text())
    pol["testing"] = {"command": None}
    (tmp_path / ".auto-pr-test/policy.yaml").write_text(yaml.safe_dump(pol))
    (tmp_path / "pr.diff").write_text(sample_diff)
    # Route the CLI's single model choke point through fakes.
    monkeypatch.setattr("auto_pr_test.models.ADAPTERS", fake_adapter())

    common = ["--cwd", str(tmp_path), "--models", str(ROOT / "policy/models.yaml"),
              "--routing", str(ROOT / "policy/routing.yaml")]
    code = cli.main(common + ["review", "--diff-file", str(tmp_path / "pr.diff"),
                              "--title", "t", "--dry-run", "--json", str(tmp_path / "out.json")])
    assert code == 0
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["decision"]["outcome"] == "recommend"
    assert "auto-pr-test: `recommend`" in capsys.readouterr().out

    assert cli.main(common + ["verify"]) == 0
    assert cli.main(common + ["policy-check"]) == 0


def test_policy_check_catches_impossible_min_families(tmp_path, capsys):
    (tmp_path / ".auto-pr-test").mkdir()
    pol = yaml.safe_load((ROOT / ".auto-pr-test/policy.yaml").read_text())
    pol["classification"] = "restricted"          # only ollama/qwen3 may read it
    pol["reviewers"] = ["bedrock/claude-sonnet-4-6", "gemini/gemini-2.5-pro"]
    (tmp_path / ".auto-pr-test/policy.yaml").write_text(yaml.safe_dump(pol))
    code = cli.main(["--cwd", str(tmp_path), "--models", str(ROOT / "policy/models.yaml"),
                     "--routing", str(ROOT / "policy/routing.yaml"), "policy-check"])
    assert code == 1
    assert "can never be met" in capsys.readouterr().out
