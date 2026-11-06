import json
import shutil
from pathlib import Path

import pytest

from auto_pr_test.models import Completion
from auto_pr_test.models.registry import Registry
from auto_pr_test.providers import PullRequest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_diff() -> str:
    return (FIXTURES / "sample.diff").read_text()


@pytest.fixture
def registry() -> Registry:
    return Registry.load(ROOT / "policy/models.yaml", ROOT / "policy/routing.yaml")


@pytest.fixture
def pr() -> PullRequest:
    return PullRequest(number=7, title="Add Hunk.is_empty", body="tiny helper", head_sha="abc123",
                       base_ref="main", head_ref="feat", author="mac", url="https://example/pr/7")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A throwaway repo dir with the default policy copied in and no ledger."""
    (tmp_path / ".auto-pr-test").mkdir()
    shutil.copy(ROOT / ".auto-pr-test/policy.yaml", tmp_path / ".auto-pr-test/policy.yaml")
    return tmp_path


def review_json(verdict="approve", findings=None, confidence=0.9, tests=None) -> str:
    return json.dumps({"verdict": verdict, "summary": "Looks fine.", "findings": findings or [],
                       "suggested_tests": tests or [], "confidence": confidence})


def fake_adapter(text_by_model: dict[str, str] | None = None, default: str | None = None):
    """Build an adapter that answers from a per-model table (or a default)."""
    def _fn(model, system, prompt):
        text = (text_by_model or {}).get(model.id, default if default is not None else review_json())
        if isinstance(text, Exception):
            raise text
        return Completion(text, model.id, 100, 50, 5, 0.0)
    return {"bedrock": _fn, "gemini": _fn, "ollama": _fn}
