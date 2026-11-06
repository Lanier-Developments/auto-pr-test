"""Repository policy: .auto-pr-test/policy.yaml, parsed and applied to a diff.

The policy is data checked into the repo it governs. Changing who may approve what
is a pull request, reviewed by the same gate — which is why `.auto-pr-test/**` is in the
`human_required` tier of the default policy.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from .diff import Diff

Classification = Literal["public", "internal", "confidential", "restricted"]
TierAction = Literal["auto_approve", "recommend", "human_required"]

TIER_ORDER = {"low": 0, "medium": 1, "high": 2}


class Tier(BaseModel):
    action: TierAction
    paths: list[str] = Field(default_factory=list)


class Testing(BaseModel):
    command: str | None = None
    coverage_file: str | None = None
    min_coverage_delta: float = 0.0
    generate_missing_tests: bool = False
    require_pass: bool = True


class Blocks(BaseModel):
    secrets: bool = True
    max_changed_lines: int = 2000
    forbidden_paths: list[str] = Field(default_factory=list)


class Ledger(BaseModel):
    path: str = ".auto-pr-test/ledger.jsonl"
    store_digests_only: bool = True


class Policy(BaseModel):
    version: int = 1
    classification: Classification = "internal"
    reviewers: list[str]
    min_families: int = 2
    tiers: dict[str, Tier]
    default_tier: str = "medium"
    fast_agreement_tiers: list[str] = Field(default_factory=lambda: ["high"])
    testing: Testing = Field(default_factory=Testing)
    blocks: Blocks = Field(default_factory=Blocks)
    ledger: Ledger = Field(default_factory=Ledger)
    # Set by load(); the policy's own hash goes in every ledger entry so a decision
    # can always be tied to the exact rules that produced it.
    policy_sha256: str = ""

    @field_validator("tiers")
    @classmethod
    def _known_tiers(cls, v: dict[str, Tier]) -> dict[str, Tier]:
        unknown = set(v) - set(TIER_ORDER)
        if unknown:
            raise ValueError(f"unknown tier names: {sorted(unknown)}; use {sorted(TIER_ORDER)}")
        return v

    @field_validator("min_families")
    @classmethod
    def _at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("min_families must be >= 1")
        return v


def load(path: str | Path = ".auto-pr-test/policy.yaml") -> Policy:
    text = Path(path).read_text()
    data = yaml.safe_load(text) or {}
    pol = Policy.model_validate(data)
    pol.policy_sha256 = hashlib.sha256(text.encode()).hexdigest()
    return pol


# --- applying policy to a change ----------------------------------------------------

def _match(path: str, pattern: str) -> bool:
    """fnmatch with `**` meaning any depth, including zero directories."""
    if "**" in pattern:
        # "docs/**" -> docs/<anything>; "**/*.md" -> <anything>/x.md or x.md
        regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
        regex = regex.replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        return re.fullmatch(regex, path) is not None
    return fnmatch.fnmatchcase(path, pattern)


def tier_for_path(policy: Policy, path: str) -> str:
    """Highest tier whose patterns match the path; default_tier if none."""
    best: str | None = None
    for name, tier in policy.tiers.items():
        if any(_match(path, p) for p in tier.paths):
            if best is None or TIER_ORDER[name] > TIER_ORDER[best]:
                best = name
    return best or policy.default_tier


class RiskAssessment(BaseModel):
    tier: str
    action: TierAction
    per_file: dict[str, str]
    blocked: list[str] = Field(default_factory=list)  # hard-block reasons

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked)


_SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("generic assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*['\"][^'\"]{12,}['\"]")),
]


def scan_secrets(diff: Diff) -> list[str]:
    hits = []
    for f in diff.files:
        for lineno, text in f.added_lines:
            for label, rx in _SECRET_PATTERNS:
                if rx.search(text):
                    hits.append(f"{f.path}:{lineno} looks like a {label}")
                    break
    return hits


def assess(policy: Policy, diff: Diff) -> RiskAssessment:
    per_file = {f.path: tier_for_path(policy, f.path) for f in diff.files}
    if per_file:
        top = max(per_file.values(), key=lambda t: TIER_ORDER[t])
    else:
        top = policy.default_tier
    action = policy.tiers[top].action if top in policy.tiers else "human_required"

    blocked: list[str] = []
    for f in diff.files:
        if any(_match(f.path, p) for p in policy.blocks.forbidden_paths):
            blocked.append(f"forbidden path changed: {f.path}")
    if diff.changed_lines > policy.blocks.max_changed_lines:
        blocked.append(
            f"{diff.changed_lines} changed lines exceeds max_changed_lines={policy.blocks.max_changed_lines}")
    if policy.blocks.secrets:
        blocked.extend(scan_secrets(diff))

    return RiskAssessment(tier=top, action=action, per_file=per_file, blocked=blocked)
