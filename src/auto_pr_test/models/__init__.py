"""Model clients. The gate calls exactly one function — `complete()` — and never
imports a provider SDK anywhere else. That is the choke point policy hangs on.

Adding a provider is a registry entry plus one adapter here."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from .registry import Model


@dataclass
class Completion:
    text: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost_usd: float


class ModelClient(Protocol):
    def __call__(self, model: Model, system: str, prompt: str) -> Completion: ...


def _estimate(text: str) -> int:
    return max(1, len(text) // 4)


def _price(model: Model, p: int, c: int) -> float:
    return round(p / 1000 * model.cost_per_1k_input + c / 1000 * model.cost_per_1k_output, 6)


# --- adapters -------------------------------------------------------------------------

def ollama(model: Model, system: str, prompt: str) -> Completion:
    import httpx
    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    t0 = time.monotonic()
    r = httpx.post(f"{base}/api/chat", timeout=600, json={
        "model": model.model, "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "options": {"temperature": 0},
    })
    r.raise_for_status()
    data = r.json()
    text = data["message"]["content"]
    p = data.get("prompt_eval_count") or _estimate(system + prompt)
    c = data.get("eval_count") or _estimate(text)
    return Completion(text, model.id, p, c, int((time.monotonic() - t0) * 1000), _price(model, p, c))


def bedrock(model: Model, system: str, prompt: str) -> Completion:
    import boto3  # optional dependency: pip install auto-pr-test[bedrock]
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    t0 = time.monotonic()
    resp = client.converse(
        modelId=model.model,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096},
    )
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    usage = resp.get("usage", {})
    p = usage.get("inputTokens") or _estimate(system + prompt)
    c = usage.get("outputTokens") or _estimate(text)
    return Completion(text, model.id, p, c, int((time.monotonic() - t0) * 1000), _price(model, p, c))


def gemini(model: Model, system: str, prompt: str) -> Completion:
    from google import genai  # optional dependency: pip install auto-pr-test[gemini]
    from google.genai import types
    client = genai.Client()  # GEMINI_API_KEY, or Vertex via GOOGLE_GENAI_USE_VERTEXAI=1
    t0 = time.monotonic()
    resp = client.models.generate_content(
        model=model.model, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0),
    )
    text = resp.text or ""
    um = getattr(resp, "usage_metadata", None)
    p = getattr(um, "prompt_token_count", None) or _estimate(system + prompt)
    c = getattr(um, "candidates_token_count", None) or _estimate(text)
    return Completion(text, model.id, p, c, int((time.monotonic() - t0) * 1000), _price(model, p, c))


def fake(model: Model, system: str, prompt: str) -> Completion:
    """Demo/CI adapter: no network. Approves with a single nit so the pipeline — tests,
    coverage, routing, concurrence, ledger — can be exercised end to end without
    credentials. Registry entries with `provider: fake` are for demos only."""
    import json
    text = json.dumps({
        "verdict": "approve",
        "summary": f"[fake {model.family}] Reviewed {len(prompt)} chars of prompt. No defects found.",
        "findings": [{"path": "(demo)", "line": None, "severity": "nit",
                      "title": "fake reviewer", "detail": "Replace with a real model in policy/models.yaml."}],
        "suggested_tests": [], "confidence": 0.5})
    return Completion(text, model.id, _estimate(system + prompt), _estimate(text), 1, 0.0)


ADAPTERS: dict[str, ModelClient] = {"ollama": ollama, "bedrock": bedrock, "gemini": gemini, "fake": fake}


def complete(model: Model, system: str, prompt: str,
             adapters: dict[str, ModelClient] | None = None) -> Completion:
    """The only place a model is called. Tests inject `adapters` with fakes."""
    table = adapters or ADAPTERS
    try:
        fn: Callable = table[model.provider]
    except KeyError:
        raise RuntimeError(f"no adapter for provider {model.provider!r}") from None
    return fn(model, system, prompt)
