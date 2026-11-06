"""auto-pr-test CLI.

  auto-pr-test review --provider github --pr 42            # live, posts results
  auto-pr-test review --pr 42 --diff-file pr.diff --dry-run # no posting, no provider needed
  auto-pr-test verify                                       # walk the ledger chain
  auto-pr-test policy-check                                 # validate .auto-pr-test/policy.yaml + registry
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, gate, policy as polmod
from .ledger import Ledger
from .models.registry import Registry
from .providers import PullRequest, load as load_provider

_STATUS = {"approve": ("success", "approved by concurrence"),
           "recommend": ("success", "approval recommended; human merges"),
           "needs_human": ("pending", "human review required"),
           "changes_requested": ("failure", "changes requested"),
           "blocked": ("failure", "blocked by policy")}


def _registry(args) -> Registry:
    return Registry.load(args.models or os.environ.get("AUTO_PR_TEST_MODELS"),
                         args.routing or os.environ.get("AUTO_PR_TEST_ROUTING"))


def cmd_review(args) -> int:
    provider = None
    if args.diff_file:
        diff_text = Path(args.diff_file).read_text()
        pr = PullRequest(number=args.pr or 0, title=args.title or "(local diff)", body=args.body or "",
                         head_sha=args.sha or "local", base_ref=args.base or "main",
                         head_ref="local", author=os.environ.get("USER", "local"), url="")
    else:
        if not args.pr:
            print("--pr is required without --diff-file", file=sys.stderr); return 2
        provider = load_provider(args.provider)
        pr = provider.get_pr(args.pr)
        diff_text = provider.get_diff(args.pr)

    run = gate.run(pr, diff_text, policy_path=args.policy, registry=_registry(args), cwd=args.cwd,
                   base_coverage=args.base_coverage, skip_tests=args.skip_tests)
    md = gate.render_markdown(run)
    print(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"decision": run.decision.model_dump(),
                                               "ledger_entry": run.ledger_entry}, indent=2, default=str))

    if provider is not None and not args.dry_run:
        state, desc = _STATUS[run.decision.outcome]
        provider.set_status(pr.head_sha, state, desc)
        provider.post_comment(pr.number, md)
        if run.decision.outcome == "approve":
            provider.approve(pr.number, "auto-pr-test: cross-family concurrence reached; auto-approve tier.")
        elif run.decision.outcome == "changes_requested":
            provider.request_changes(pr.number, "auto-pr-test: see findings above.")

    # Exit code is the merge signal for CI: 0 only when the gate is satisfied.
    return 0 if run.decision.outcome in ("approve", "recommend") else 1


def cmd_verify(args) -> int:
    pol = polmod.load(Path(args.cwd) / args.policy)
    ok, msg = Ledger(Path(args.cwd) / pol.ledger.path).verify()
    print(("OK: " if ok else "TAMPERED: ") + msg)
    return 0 if ok else 1


def cmd_policy_check(args) -> int:
    pol = polmod.load(Path(args.cwd) / args.policy)
    reg = _registry(args)
    problems = []
    fams = set()
    for mid in pol.reviewers:
        try:
            m = reg.get(mid)
        except KeyError as exc:
            problems.append(str(exc)); continue
        dec = reg.evaluate(mid, pol.classification)
        print(f"{mid:32} {m.family:10} {'allow' if dec.allowed else 'DENY ':5} {dec.rule_id}: {dec.reason}")
        if dec.allowed:
            fams.add(m.family)
    if len(fams) < pol.min_families:
        problems.append(f"only {len(fams)} allowed famil{'y' if len(fams)==1 else 'ies'} "
                        f"({', '.join(sorted(fams)) or 'none'}) for classification "
                        f"{pol.classification}; min_families={pol.min_families} can never be met")
    for p in problems:
        print("PROBLEM:", p)
    print(f"policy sha256 {pol.policy_sha256[:16]}  tiers {list(pol.tiers)}  default {pol.default_tier}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="auto-pr-test")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--policy", default=".auto-pr-test/policy.yaml")
    ap.add_argument("--models", help="path to models.yaml (default: bundled policy/)")
    ap.add_argument("--routing", help="path to routing.yaml (default: bundled policy/)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review")
    r.add_argument("--provider", default="github", choices=["github", "azure-devops", "ado"])
    r.add_argument("--pr", type=int)
    r.add_argument("--diff-file")
    r.add_argument("--title"); r.add_argument("--body"); r.add_argument("--sha"); r.add_argument("--base")
    r.add_argument("--base-coverage", type=float, help="base branch total coverage %%, for the delta")
    r.add_argument("--skip-tests", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="do not post to the provider")
    r.add_argument("--json", help="write decision + ledger entry to this file")
    r.set_defaults(fn=cmd_review)

    v = sub.add_parser("verify"); v.set_defaults(fn=cmd_verify)
    p = sub.add_parser("policy-check"); p.set_defaults(fn=cmd_policy_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
