"""The orchestrator. One PR in, one GateDecision and one ledger entry out.

Order is deliberate: policy and hard blocks before any model sees the code (no
point paying for a review of a PR that ships a private key), tests before review
(so reviewers are told which changed lines never executed), routing before each
reviewer (a refused model is logged, not silently skipped), concurrence last.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, diff as diffmod, policy as polmod, testing
from .concurrence import GateDecision, decide
from .ledger import Ledger, sha256
from .models import ModelClient
from .models.registry import Registry
from .providers import PullRequest
from .review import ReviewResult, run_review


@dataclass
class GateRun:
    pr: PullRequest
    decision: GateDecision
    risk: polmod.RiskAssessment
    tests: testing.TestReport
    reviews: list[ReviewResult]
    skipped: list[str]
    ledger_entry: dict = field(default_factory=dict)


def run(pr: PullRequest, diff_text: str, *, policy_path: str | Path = ".auto-pr-test/policy.yaml",
        registry: Registry | None = None, cwd: str | Path = ".", base_coverage: float | None = None,
        adapters: dict[str, ModelClient] | None = None, skip_tests: bool = False) -> GateRun:
    cwd = Path(cwd)
    policy = polmod.load(cwd / policy_path)
    registry = registry or Registry.load()
    diff = diffmod.parse(diff_text)
    risk = polmod.assess(policy, diff)

    reviews: list[ReviewResult] = []
    skipped: list[str] = []
    routing: list[dict] = []
    tests = testing.TestReport(ran=False, passed=True)

    if not risk.is_blocked:
        if not skip_tests:
            tests = testing.run(policy.testing, diff, cwd=cwd, base_coverage=base_coverage)
        want_tests = policy.testing.generate_missing_tests and bool(tests.uncovered)
        for mid in policy.reviewers:
            try:
                model = registry.get(mid)
            except KeyError as exc:
                skipped.append(str(exc)); routing.append({"model": mid, "allowed": False, "rule": "unknown-model"})
                continue
            dec = registry.evaluate(mid, policy.classification)
            routing.append({"model": mid, "allowed": dec.allowed, "rule": dec.rule_id, "reason": dec.reason})
            if not dec.allowed:
                skipped.append(f"{mid} ({dec.rule_id}: {dec.reason})")
                continue
            reviews.append(run_review(model, pr.title, pr.body, diff, risk.tier,
                                      uncovered=tests.uncovered or None, want_tests=want_tests,
                                      adapters=adapters))

    decision = decide(policy, risk, tests, reviews, skipped)

    ledger = Ledger(cwd / policy.ledger.path)
    entry = ledger.append(
        gate_version=__version__,
        pr={"number": pr.number, "head_sha": pr.head_sha, "base_ref": pr.base_ref,
            "author": pr.author, "url": pr.url},
        policy_sha256=policy.policy_sha256,
        classification=policy.classification,
        diff_sha256=sha256(diff.raw),
        files=risk.per_file,
        tier=risk.tier,
        blocked=risk.blocked,
        tests={"ran": tests.ran, "passed": tests.passed, "total_coverage": tests.total_coverage,
               "coverage_delta": tests.coverage_delta, "change_coverage": tests.change_coverage},
        routing=routing,
        reviews=[{"model": r.model_id, "family": r.family,
                  "verdict": r.review.verdict if r.review else None,
                  "findings": len(r.review.findings) if r.review else None,
                  "confidence": r.review.confidence if r.review else None,
                  "error": r.parse_error, "review_sha256": r.raw_sha256, **(r.completion or {})}
                 for r in reviews],
        outcome=decision.outcome,
        reasons=decision.reasons,
        warnings=decision.warnings,
    )
    return GateRun(pr=pr, decision=decision, risk=risk, tests=tests, reviews=reviews,
                   skipped=skipped, ledger_entry=entry)


# --- rendering ------------------------------------------------------------------------

_ICON = {"approve": "✅", "recommend": "🟢", "changes_requested": "🔴",
         "blocked": "⛔", "needs_human": "🟡"}


def render_markdown(g: GateRun) -> str:
    d, t = g.decision, g.tests
    out = [f"## {_ICON[d.outcome]} auto-pr-test: `{d.outcome}` (tier **{d.tier}**)", ""]
    out += [f"- {r}" for r in d.reasons]
    if d.warnings:
        out += ["", "**Warnings**"] + [f"- ⚠️ {w}" for w in d.warnings]
    if t.ran:
        cov = f"{t.total_coverage:.1f}%" if t.total_coverage is not None else "n/a"
        delta = f" ({t.coverage_delta:+.2f})" if t.coverage_delta is not None else ""
        cc = f", change coverage {t.change_coverage}%" if t.change_coverage is not None else ""
        out += ["", f"**Tests:** {'passed' if t.passed else 'FAILED'} · coverage {cov}{delta}{cc}"]
    for r in g.reviews:
        out += ["", f"### {r.model_id} ({r.family})"]
        if r.review is None:
            out.append(f"_{r.parse_error}_"); continue
        out.append(f"**{r.review.verdict}** · confidence {r.review.confidence:.2f}")
        out.append(r.review.summary)
        for f in r.review.findings:
            loc = f"`{f.path}`" + (f":{f.line}" if f.line else "")
            out.append(f"- **{f.severity}** {loc} — {f.title}: {f.detail}")
    if d.suggested_tests:
        out += ["", "<details><summary>Suggested tests for uncovered lines</summary>", ""]
        for s in d.suggested_tests:
            out += ["```python", s.strip(), "```", ""]
        out.append("</details>")
    e = g.ledger_entry
    out += ["", f"<sub>ledger seq {e.get('seq')} · `{e.get('entry_hash', '')[:12]}` · "
                f"policy `{e.get('policy_sha256', '')[:12]}` · auto-pr-test {__version__}</sub>"]
    return "\n".join(out)
