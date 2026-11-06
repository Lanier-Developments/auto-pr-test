"""End-to-end through gate.run with fake model adapters. No network, no tests
command (the fixture policy is rewritten to drop it)."""
import re

import yaml

from auto_pr_test import gate
from auto_pr_test.ledger import Ledger

from conftest import fake_adapter, review_json


def _set_policy(repo, **changes):
    p = repo / ".auto-pr-test/policy.yaml"
    data = yaml.safe_load(p.read_text())
    data["testing"] = {"command": None}
    data.update(changes)
    p.write_text(yaml.safe_dump(data))


def test_recommend_on_medium_tier(repo, pr, sample_diff, registry):
    _set_policy(repo)
    g = gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=fake_adapter())
    assert g.decision.outcome == "recommend"
    assert g.decision.approving_families == ["anthropic", "google", "qwen"]
    assert g.ledger_entry["seq"] == 1 and g.ledger_entry["outcome"] == "recommend"
    assert Ledger(repo / ".auto-pr-test/ledger.jsonl").verify()[0]
    md = gate.render_markdown(g)
    assert "recommend" in md and "ledger seq 1" in md


def test_auto_approve_on_low_tier(repo, pr, registry):
    _set_policy(repo)
    diff = "diff --git a/docs/x.md b/docs/x.md\n--- a/docs/x.md\n+++ b/docs/x.md\n@@ -0,0 +1,1 @@\n+hi\n"
    g = gate.run(pr, diff, cwd=repo, registry=registry, adapters=fake_adapter())
    assert g.decision.outcome == "approve" and g.decision.merge_allowed


def test_same_family_is_one_opinion(repo, pr, sample_diff, registry):
    # Only the anthropic reviewer approves; others request changes -> 1 family < 2.
    _set_policy(repo)
    ad = fake_adapter({"gemini/gemini-2.5-pro": review_json("request_changes"),
                       "ollama/qwen3:8b": review_json("comment")})
    g = gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=ad)
    assert g.decision.outcome == "needs_human"
    assert g.decision.approving_families == ["anthropic"]
    assert any("requires 2" in r for r in g.decision.reasons)


def test_blocker_finding_requests_changes(repo, pr, sample_diff, registry):
    _set_policy(repo)
    bad = review_json("request_changes", findings=[
        {"path": "src/auto_pr_test/diff.py", "line": 13, "severity": "blocker",
         "title": "Unsafe", "detail": "no"}])
    ad = fake_adapter({"gemini/gemini-2.5-pro": bad})
    g = gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=ad)
    assert g.decision.outcome == "changes_requested"
    assert len(g.decision.blockers) == 1


def test_fast_agreement_on_high_tier_needs_human(repo, pr, registry):
    _set_policy(repo)
    diff = ("diff --git a/.auto-pr-test/policy.yaml b/.auto-pr-test/policy.yaml\n--- a/.auto-pr-test/policy.yaml\n"
            "+++ b/.auto-pr-test/policy.yaml\n@@ -1,1 +1,1 @@\n-min_families: 2\n+min_families: 1\n")
    g = gate.run(pr, diff, cwd=repo, registry=registry, adapters=fake_adapter())
    assert g.decision.tier == "high"
    assert g.decision.outcome == "needs_human"
    assert any(w.startswith("fast_agreement") for w in g.decision.warnings)


def test_confidential_repo_refuses_cloud_reviewers(repo, pr, sample_diff, registry):
    _set_policy(repo, classification="confidential", min_families=1)
    g = gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=fake_adapter())
    assert [r.model_id for r in g.reviews] == ["ollama/qwen3:8b"]
    assert len(g.skipped) == 2 and "confidential-stays-local" in g.skipped[0]
    assert g.decision.outcome == "recommend"
    routing = {r["model"]: r["allowed"] for r in g.ledger_entry["routing"]}
    assert routing == {"bedrock/claude-sonnet-4-6": False, "gemini/gemini-2.5-pro": False,
                       "ollama/qwen3:8b": True}


def test_provider_error_is_non_approval(repo, pr, sample_diff, registry):
    _set_policy(repo)
    ad = fake_adapter({"gemini/gemini-2.5-pro": RuntimeError("timeout"),
                       "ollama/qwen3:8b": "not json at all"})
    g = gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=ad)
    assert g.decision.outcome == "needs_human"
    assert any("timeout" in w for w in g.decision.warnings)
    assert any("unparseable" in w for w in g.decision.warnings)


def test_blocked_pr_never_calls_a_model(repo, pr, registry):
    _set_policy(repo)
    calls = []
    def spy(model, system, prompt):
        calls.append(model.id); raise AssertionError("should not be called")
    diff = "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -0,0 +1,1 @@\n+x=1\n"
    g = gate.run(pr, diff, cwd=repo, registry=registry,
                 adapters={"bedrock": spy, "gemini": spy, "ollama": spy})
    assert g.decision.outcome == "blocked" and calls == []


def test_ledger_stores_digests_not_text(repo, pr, sample_diff, registry):
    _set_policy(repo)
    gate.run(pr, sample_diff, cwd=repo, registry=registry, adapters=fake_adapter())
    raw = (repo / ".auto-pr-test/ledger.jsonl").read_text()
    assert "is_empty" not in raw and "Looks fine" not in raw
    assert re.search(r'"diff_sha256": "[0-9a-f]{64}"', raw)
