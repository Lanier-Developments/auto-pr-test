"""Unified-diff parsing. Deliberately dependency-free; a PR diff is the one input
every provider can produce, so the gate's view of a change is built from it alone."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FILE_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    new_start: int
    lines: list[str] = field(default_factory=list)  # raw lines incl. +/-/space prefix

    @property
    def added(self) -> list[tuple[int, str]]:
        """(new-file line number, text) for every added line."""
        out, n = [], self.new_start
        for ln in self.lines:
            if ln.startswith("+"):
                out.append((n, ln[1:]))
                n += 1
            elif ln.startswith("-"):
                continue
            else:
                n += 1
        return out


@dataclass
class FileDiff:
    path: str
    old_path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        return [a for h in self.hunks for a in h.added]

    @property
    def added_count(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("+"))

    @property
    def removed_count(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.startswith("-"))


@dataclass
class Diff:
    files: list[FileDiff] = field(default_factory=list)
    raw: str = ""

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def changed_lines(self) -> int:
        return sum(f.added_count + f.removed_count for f in self.files)


def parse(text: str) -> Diff:
    diff = Diff(raw=text)
    current: FileDiff | None = None
    hunk: Hunk | None = None
    for raw in text.splitlines():
        m = _FILE_HEADER.match(raw)
        if m:
            current = FileDiff(path=m.group("b"), old_path=m.group("a"))
            diff.files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.is_new = True
        elif raw.startswith("deleted file mode"):
            current.is_deleted = True
        elif raw.startswith("Binary files"):
            current.is_binary = True
        elif raw.startswith("+++ ") or raw.startswith("--- ") or raw.startswith("index "):
            continue
        else:
            hm = _HUNK_HEADER.match(raw)
            if hm:
                hunk = Hunk(old_start=int(hm.group(1)), new_start=int(hm.group(3)))
                current.hunks.append(hunk)
            elif hunk is not None and raw[:1] in ("+", "-", " ", "\\"):
                hunk.lines.append(raw)
            elif hunk is not None and raw == "":
                # A blank context line whose leading space was stripped in transit.
                hunk.lines.append(" ")
    return diff
