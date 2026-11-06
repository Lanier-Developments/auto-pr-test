"""Run the repository's test command and measure coverage of the changed lines.

Coverage-of-change is the number that matters to a reviewer: a suite that is 90%
green overall tells you nothing about whether the forty lines in this PR were ever
executed. Reads pytest-cov's JSON report; other formats are a small adapter away.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .diff import Diff
from .policy import Testing


class TestReport(BaseModel):
    ran: bool
    passed: bool
    exit_code: int | None = None
    total_coverage: float | None = None
    base_coverage: float | None = None
    changed_lines: int = 0
    changed_lines_covered: int = 0
    uncovered: dict[str, list[int]] = Field(default_factory=dict)
    stdout_tail: str = ""

    @property
    def coverage_delta(self) -> float | None:
        if self.total_coverage is None or self.base_coverage is None:
            return None
        return round(self.total_coverage - self.base_coverage, 2)

    @property
    def change_coverage(self) -> float | None:
        if not self.changed_lines:
            return None
        return round(100 * self.changed_lines_covered / self.changed_lines, 1)


def _read_cov(path: Path) -> tuple[float | None, dict[str, set[int]]]:
    if not path.exists():
        return None, {}
    data = json.loads(path.read_text())
    total = data.get("totals", {}).get("percent_covered")
    executed: dict[str, set[int]] = {}
    for fname, info in data.get("files", {}).items():
        executed[_norm(fname)] = set(info.get("executed_lines", []))
    return total, executed


def _norm(p: str) -> str:
    p = p.replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def _lookup(executed: dict[str, set[int]], path: str) -> set[int] | None:
    """coverage.json keys may be relative to a src/ root; match on suffix."""
    if path in executed:
        return executed[path]
    for k, v in executed.items():
        if path.endswith(k) or k.endswith(path):
            return v
    return None


def run(cfg: Testing, diff: Diff, cwd: str | Path = ".", base_coverage: float | None = None,
        timeout: int = 1800) -> TestReport:
    if not cfg.command:
        return TestReport(ran=False, passed=True)
    proc = subprocess.run(shlex.split(cfg.command), cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    tail = (proc.stdout + proc.stderr)[-4000:]
    total, executed = (None, {})
    if cfg.coverage_file:
        total, executed = _read_cov(Path(cwd) / cfg.coverage_file)

    changed = covered = 0
    uncovered: dict[str, list[int]] = {}
    for f in diff.files:
        if f.is_deleted or f.is_binary or not f.path.endswith(".py"):
            continue
        hit = _lookup(executed, f.path)
        if hit is None:  # not measured at all (tests, docs); not held against the PR
            continue
        missing = []
        for lineno, text in f.added_lines:
            if not text.strip() or text.strip().startswith("#"):
                continue
            changed += 1
            if lineno in hit:
                covered += 1
            else:
                missing.append(lineno)
        if missing:
            uncovered[f.path] = missing

    return TestReport(ran=True, passed=proc.returncode == 0, exit_code=proc.returncode,
                      total_coverage=total, base_coverage=base_coverage,
                      changed_lines=changed, changed_lines_covered=covered,
                      uncovered=uncovered, stdout_tail=tail)
