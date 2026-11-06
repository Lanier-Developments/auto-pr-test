"""Model registry and routing — may this model read this repository?

Same contract as ControlPlane: rules are data, evaluated top to bottom, first match
wins, no match is a deny. Every decision carries a rule id and a reason so the
ledger can say *why* a reviewer was used or refused, not just *that* it was.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent.parent.parent.parent / "policy"


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    model: str
    family: str
    tier: str
    region: str
    status: str
    max_classification: str
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule_id: str
    reason: str
    model_id: str


_CLASS_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class Registry:
    def __init__(self, models: list[Model], rules: list[dict[str, Any]]):
        self._models = {m.id: m for m in models}
        self._rules = rules

    @classmethod
    def load(cls, models_path: str | Path | None = None,
             routing_path: str | Path | None = None) -> "Registry":
        mp = Path(models_path) if models_path else _HERE / "models.yaml"
        rp = Path(routing_path) if routing_path else _HERE / "routing.yaml"
        mdata = yaml.safe_load(mp.read_text()) or {}
        rdata = yaml.safe_load(rp.read_text()) or {}
        known = set(Model.__dataclass_fields__) - {"extra"}
        models = []
        for entry in mdata.get("models", []):
            core = {k: v for k, v in entry.items() if k in known}
            extra = {k: v for k, v in entry.items() if k not in known}
            models.append(Model(**core, extra=extra or None))
        return cls(models, rdata.get("rules", []))

    def get(self, model_id: str) -> Model:
        try:
            return self._models[model_id]
        except KeyError:
            raise KeyError(f"model {model_id!r} is not in the registry") from None

    def families(self) -> set[str]:
        return {m.family for m in self._models.values()}

    def evaluate(self, model_id: str, classification: str) -> Decision:
        """Routing rules first; the registry ceiling is an extra deny on top."""
        model = self.get(model_id)
        facts = {
            "classification": classification,
            "tier": model.tier,
            "status": model.status,
            "region": model.region,
            "family": model.family,
        }
        for rule in self._rules:
            if _matches(rule.get("match") or {}, facts):
                allowed = rule.get("decision") == "allow"
                if allowed and _CLASS_ORDER[classification] > _CLASS_ORDER[model.max_classification]:
                    return Decision(False, "registry-ceiling",
                                    f"{classification} exceeds model ceiling {model.max_classification}",
                                    model_id)
                return Decision(allowed, rule.get("id", "?"), rule.get("reason", ""), model_id)
        return Decision(False, "default-deny", "No rule permits this combination", model_id)


def _matches(match: dict[str, Any], facts: dict[str, Any]) -> bool:
    for key, want in match.items():
        have = facts.get(key)
        if isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True
