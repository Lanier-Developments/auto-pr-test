"""Source-control providers. The gate needs five things from a PR host and nothing
else; everything provider-specific lives behind this protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    head_sha: str
    base_ref: str
    head_ref: str
    author: str
    url: str


class Provider(Protocol):
    name: str

    def get_pr(self, number: int) -> PullRequest: ...
    def get_diff(self, number: int) -> str: ...
    def post_comment(self, number: int, body: str) -> None: ...
    def set_status(self, sha: str, state: str, description: str, context: str = "auto-pr-test") -> None: ...
    def approve(self, number: int, body: str) -> None: ...
    def request_changes(self, number: int, body: str) -> None: ...


def load(name: str, **kw) -> Provider:
    if name == "github":
        from .github import GitHub
        return GitHub(**kw)
    if name in ("azure-devops", "ado"):
        from .azure_devops import AzureDevOps
        return AzureDevOps(**kw)
    raise ValueError(f"unknown provider {name!r}")
