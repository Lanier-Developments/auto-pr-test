"""Azure DevOps (Repos + Pipelines) adapter.

Interface-complete, read side implemented, write side stubbed. The ADO PR model
differs from GitHub's in two ways the gate has to respect:

  * Reviews are "votes" on a reviewer identity (10 approve, 5 approve-with-
    suggestions, 0 none, -5 wait, -10 reject). The gate's service identity must be
    added as a reviewer before it can vote. `approve` -> vote 10, `request_changes`
    -> vote -10.
  * Commit statuses are "PR statuses" posted against the PR, not the commit, with a
    genre/name context. `set_status` maps state -> succeeded | failed | pending | error.

Env: AZURE_DEVOPS_ORG_URL (https://dev.azure.com/<org>), AZURE_DEVOPS_PROJECT,
AZURE_DEVOPS_REPO, AZURE_DEVOPS_TOKEN (PAT with Code read/write + PR contribute).
Under Pipelines, SYSTEM_ACCESSTOKEN works as the token.
"""
from __future__ import annotations

import base64
import os

import httpx

from . import PullRequest

_API = "7.1"


class AzureDevOps:
    name = "azure-devops"

    def __init__(self, org_url: str | None = None, project: str | None = None,
                 repo: str | None = None, token: str | None = None):
        self.org_url = (org_url or os.environ.get("AZURE_DEVOPS_ORG_URL")
                        or os.environ.get("SYSTEM_COLLECTIONURI", "")).rstrip("/")
        self.project = project or os.environ.get("AZURE_DEVOPS_PROJECT") or os.environ.get("SYSTEM_TEAMPROJECT")
        self.repo = repo or os.environ.get("AZURE_DEVOPS_REPO") or os.environ.get("BUILD_REPOSITORY_NAME")
        tok = token or os.environ.get("AZURE_DEVOPS_TOKEN") or os.environ.get("SYSTEM_ACCESSTOKEN")
        if not (self.org_url and self.project and self.repo and tok):
            raise RuntimeError("AZURE_DEVOPS_ORG_URL, _PROJECT, _REPO and _TOKEN are required")
        auth = base64.b64encode(f":{tok}".encode()).decode()
        self._c = httpx.Client(base_url=f"{self.org_url}/{self.project}/_apis/git/repositories/{self.repo}",
                               timeout=60, headers={"Authorization": f"Basic {auth}",
                                                    "User-Agent": "auto-pr-test"})

    def get_pr(self, number: int) -> PullRequest:
        r = self._c.get(f"/pullRequests/{number}", params={"api-version": _API})
        r.raise_for_status()
        j = r.json()
        strip = lambda ref: ref.removeprefix("refs/heads/")
        return PullRequest(number=number, title=j["title"], body=j.get("description") or "",
                           head_sha=j["lastMergeSourceCommit"]["commitId"],
                           base_ref=strip(j["targetRefName"]), head_ref=strip(j["sourceRefName"]),
                           author=j["createdBy"]["uniqueName"],
                           url=f"{self.org_url}/{self.project}/_git/{self.repo}/pullrequest/{number}")

    def get_diff(self, number: int) -> str:
        # ADO has no "give me the unified diff" endpoint. Under Pipelines the checkout
        # has both refs, so the cheapest correct path is `git diff` locally; the gate's
        # CLI accepts --diff-file for exactly this. Left as a clear TODO rather than a
        # half-implementation that reassembles hunks from the iterations/changes API.
        raise NotImplementedError(
            "Azure DevOps: produce the diff in the pipeline "
            "(git fetch origin $(System.PullRequest.TargetBranch); git diff origin/<target>...HEAD) "
            "and pass it with --diff-file")

    def post_comment(self, number: int, body: str) -> None:
        r = self._c.post(f"/pullRequests/{number}/threads", params={"api-version": _API},
                         json={"comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
                               "status": 1})
        r.raise_for_status()

    def set_status(self, sha: str, state: str, description: str, context: str = "auto-pr-test") -> None:
        raise NotImplementedError("Azure DevOps: PR status API not wired yet "
                                  "(POST /pullRequests/{id}/statuses with context genre 'auto-pr-test')")

    def approve(self, number: int, body: str) -> None:
        raise NotImplementedError("Azure DevOps: vote 10 via PUT /pullRequests/{id}/reviewers/{reviewerId}")

    def request_changes(self, number: int, body: str) -> None:
        raise NotImplementedError("Azure DevOps: vote -10 via PUT /pullRequests/{id}/reviewers/{reviewerId}")
