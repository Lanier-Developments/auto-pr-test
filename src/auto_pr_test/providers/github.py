"""GitHub via the REST API. Only the endpoints the protocol needs."""
from __future__ import annotations

import os

import httpx

from . import PullRequest


class GitHub:
    name = "github"

    def __init__(self, repo: str | None = None, token: str | None = None,
                 api_url: str | None = None):
        self.repo = repo or os.environ["GITHUB_REPOSITORY"]
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            raise RuntimeError("GITHUB_TOKEN (or GH_TOKEN) is required")
        self.api = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        self._c = httpx.Client(
            base_url=self.api, timeout=60,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "auto-pr-test"})

    def _pr(self, n: int) -> str:
        return f"/repos/{self.repo}/pulls/{n}"

    def get_pr(self, number: int) -> PullRequest:
        r = self._c.get(self._pr(number)); r.raise_for_status()
        j = r.json()
        return PullRequest(number=number, title=j["title"], body=j.get("body") or "",
                           head_sha=j["head"]["sha"], base_ref=j["base"]["ref"],
                           head_ref=j["head"]["ref"], author=j["user"]["login"],
                           url=j["html_url"])

    def get_diff(self, number: int) -> str:
        r = self._c.get(self._pr(number), headers={"Accept": "application/vnd.github.diff"})
        r.raise_for_status()
        return r.text

    def post_comment(self, number: int, body: str) -> None:
        r = self._c.post(f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})
        r.raise_for_status()

    def set_status(self, sha: str, state: str, description: str, context: str = "auto-pr-test") -> None:
        # state: success | failure | pending | error
        r = self._c.post(f"/repos/{self.repo}/statuses/{sha}",
                         json={"state": state, "description": description[:140], "context": context})
        r.raise_for_status()

    def _review(self, number: int, event: str, body: str) -> None:
        r = self._c.post(f"{self._pr(number)}/reviews", json={"event": event, "body": body})
        r.raise_for_status()

    def approve(self, number: int, body: str) -> None:
        self._review(number, "APPROVE", body)

    def request_changes(self, number: int, body: str) -> None:
        self._review(number, "REQUEST_CHANGES", body)
