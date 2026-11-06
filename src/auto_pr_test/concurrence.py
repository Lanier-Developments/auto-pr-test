"""The concurrence rule — what makes this a gate rather than a comment bot.

Approval requires independent agreement from at least `min_families` distinct model
families. Two sizes of the same family are one opinion. A reviewer that policy
refused, that errored, or that returned something unparseable is a non-approval,
never a bye.

Fast agreement is a flag, not a pass: on tiers listed in `fast_agreement_tiers`,
unanimous approval with zero findings is recorded as a warning. Independent
reviewers finding nothing wrong with risky code is itself a finding — the discipline
borrowed from running a second model family specifically to catch the first one
agreeing too easily.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .policy import Policy, RiskAssessment
from .review import Finding, ReviewResult
from .testing import TestReport

Outcome = Literal["approve", "recommend", "changes_requested", "blocked", "needs_human"]


class GateDecision(BaseModel):
    outcome: Outcome
    tier: str
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    approving_families: list[str] = Field(default_factory=list)
    blockers: list[Finding] = Field(default_factory=list)
    suggested_tests: list[str] = Field(default_factory=list)

    @property
    def merge_allowed(self) -> bool:
        return self.outcome == "approve"


def decide(policy: Policy, risk: RiskAssessment, tests: TestReport,
           reviews: list[ReviewResult], skipped: list[str]) -> GateDecision:
    d = GateDecision(outcome="needs_human", tier=risk.tier)

    # 1. Hard blocks: nothing else matters.
    if risk.is_blocked:
        d.outcome = "blocked"
        d.reasons.extend(risk.blocked)
        return d

    # 2. Tests.
    t = policy.testing
    if tests.ran and not tests.passed and t.require_pass:
        d.outcome = "changes_requested"
        d.reasons.append(f"test command exited {tests.exit_code}")
    delta = tests.coverage_delta
    if delta is not None and delta < t.min_coverage_delta:
        d.outcome = "changes_requested"
        d.reasons.append(f"coverage moved {delta:+.2f} points; floor is {t.min_coverage_delta:+.2f}")
    if tests.uncovered:
        n = sum(len(v) for v in tests.uncovered.values())
        d.warnings.append(f"{n} changed line(s) not executed by the suite")

    # 3. Reviews.
    d.warnings.extend(f"reviewer skipped: {s}" for s in skipped)
    for r in reviews:
        if r.review is None:
            d.warnings.append(f"{r.model_id}: {r.parse_error}")
            continue
        d.blockers.extend(r.blockers)
        d.suggested_tests.extend(r.review.suggested_tests)
    approving = {r.family for r in reviews if r.approved}
    d.approving_families = sorted(approving)

    if d.blockers:
        d.outcome = "changes_requested"
        d.reasons.append(f"{len(d.blockers)} blocker finding(s)")
    if d.outcome == "changes_requested":
        return d

    if len(approving) < policy.min_families:
        d.outcome = "needs_human"
        d.reasons.append(
            f"{len(approving)} approving famil{'y' if len(approving) == 1 else 'ies'} "
            f"({', '.join(d.approving_families) or 'none'}); policy requires {policy.min_families}")
        return d

    # 4. Fast-agreement check.
    total_findings = sum(len(r.review.findings) for r in reviews if r.review)
    if risk.tier in policy.fast_agreement_tiers and total_findings == 0:
        d.warnings.append("fast_agreement: unanimous approval with zero findings on a "
                          f"{risk.tier}-tier change — treat as unreviewed, not as clean")
        d.outcome = "needs_human"
        d.reasons.append("fast agreement on a high-risk tier requires a human")
        return d

    # 5. Tier action.
    d.outcome = {"auto_approve": "approve", "recommend": "recommend",
                 "human_required": "needs_human"}[risk.action]
    d.reasons.append(f"tier {risk.tier} -> {risk.action}; "
                     f"approved by {', '.join(d.approving_families)}")
    return d
