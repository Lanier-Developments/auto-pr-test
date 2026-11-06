"""Diff parsing, policy tiering, routing, ledger."""
import json

import pytest

from auto_pr_test import diff as diffmod, policy as polmod
from auto_pr_test.ledger import Ledger
from auto_pr_test.review import parse_review

from conftest import ROOT, review_json


def test_diff_parse(sample_diff):
    d = diffmod.parse(sample_diff)
    assert d.paths == ["src/auto_pr_test/diff.py", "docs/notes.md"]
    assert d.files[1].is_new
    assert d.files[0].added_count == 3 and d.files[0].removed_count == 0
    # new-file line numbers for added lines
    assert [n for n, _ in d.files[0].added_lines] == [13, 14, 15]
    assert d.changed_lines == 5


def test_tier_takes_highest(sample_diff):
    pol = polmod.load(ROOT / ".auto-pr-test/policy.yaml")
    d = diffmod.parse(sample_diff)
    r = polmod.assess(pol, d)
    assert r.per_file == {"src/auto_pr_test/diff.py": "medium", "docs/notes.md": "low"}
    assert r.tier == "medium" and r.action == "recommend"
    assert not r.is_blocked


@pytest.mark.parametrize("path,tier", [
    ("README.md", "low"), ("docs/a/b/c.md", "low"), (".auto-pr-test/policy.yaml", "high"),
    ("policy/models.yaml", "high"), (".github/workflows/x.yml", "high"),
    ("src/x.py", "medium"), ("weird/thing.txt", "medium"), ("a/b/mcp.json", "high"),
])
def test_glob_matching(path, tier):
    pol = polmod.load(ROOT / ".auto-pr-test/policy.yaml")
    assert polmod.tier_for_path(pol, path) == tier


def test_secret_scan_blocks():
    pol = polmod.load(ROOT / ".auto-pr-test/policy.yaml")
    text = ("diff --git a/src/k.py b/src/k.py\n--- a/src/k.py\n+++ b/src/k.py\n@@ -0,0 +1,1 @@\n"
            '+AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    r = polmod.assess(pol, diffmod.parse(text))
    assert r.is_blocked and "AWS access key" in r.blocked[0]


def test_forbidden_path_blocks():
    pol = polmod.load(ROOT / ".auto-pr-test/policy.yaml")
    text = "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -0,0 +1,1 @@\n+x=1\n"
    assert polmod.assess(pol, diffmod.parse(text)).is_blocked


def test_routing(registry):
    assert registry.evaluate("bedrock/claude-sonnet-4-6", "public").allowed
    assert registry.evaluate("bedrock/claude-sonnet-4-6", "internal").allowed
    conf = registry.evaluate("bedrock/claude-sonnet-4-6", "confidential")
    assert not conf.allowed and conf.rule_id == "confidential-stays-local"
    assert registry.evaluate("ollama/qwen3:8b", "restricted").allowed
    retired = registry.evaluate("ollama/llama2:7b", "public")
    assert not retired.allowed and retired.rule_id == "retired-model-refused"
    with pytest.raises(KeyError):
        registry.get("nope/model")


def test_ledger_chain_and_tamper(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(a=1); led.append(a=2); e3 = led.append(a=3)
    assert e3["seq"] == 3
    assert led.verify() == (True, "chain intact")
    lines = (tmp_path / "l.jsonl").read_text().splitlines()
    row = json.loads(lines[1]); row["a"] = 99
    lines[1] = json.dumps(row, sort_keys=True)
    (tmp_path / "l.jsonl").write_text("\n".join(lines) + "\n")
    ok, msg = led.verify()
    assert not ok and "entry 2" in msg


def test_parse_review_forces_request_changes_on_blocker():
    r = parse_review("```json\n" + review_json("approve", findings=[
        {"path": "x.py", "line": 1, "severity": "blocker", "title": "t", "detail": "d"}]) + "\n```")
    assert r.verdict == "request_changes"


def test_parse_review_garbage():
    with pytest.raises(ValueError):
        parse_review("I approve this PR, looks great!")
