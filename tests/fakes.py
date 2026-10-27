"""
Test doubles shared by the review-engine and CLI tests.
"""

import math
from typing import Dict, List

import numpy as np


class FakeEmbedder:
    """Requirement i is the unit vector e_i. An endpoint text is registered with
    the cosine it should have against each requirement; the remainder goes into
    a private dimension so the vector stays unit length."""

    def __init__(self, n_requirements: int):
        self.n = n_requirements
        self.scores: Dict[str, Dict[int, float]] = {}
        self.calls: List[List[str]] = []
        self._extra = 0

    def set(self, text: str, **scores: float) -> None:
        self.scores[text] = {int(k[1:]): v for k, v in scores.items()}       # r0=0.8 -> {0: 0.8}

    def _vec(self, text: str) -> np.ndarray:
        dim = self.n + 64
        v = np.zeros(dim, dtype=np.float32)
        for i, s in self.scores.get(text, {}).items():
            v[i] = s
        rest = 1.0 - float(np.sum(v ** 2))
        self._extra = (self._extra + 1) % 64
        v[self.n + self._extra] = math.sqrt(max(rest, 0.0))
        return v

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.stack([self._vec(t) for t in texts])

    def encode_requirements(self, reqs):
        out = np.zeros((len(reqs), self.n + 64), dtype=np.float32)
        for i in range(len(reqs)):
            out[i, i] = 1.0
        return out


class FakeExplainer:
    provider_info = "fake / test"

    def __init__(self):
        self.calls = []

    def explain_drift(self, pair, v1_text, v2_text):
        self.calls.append(pair["requirement_id"])
        return f"explained {pair['requirement_id']}"


class FakeVisionClient:
    """Stands in for openai.OpenAI in src/srs/diagrams.py: returns `payload`
    (a dict, or a callable(kwargs) -> dict) as the model's JSON answer and
    records every call. `error` makes the call raise instead."""

    def __init__(self, payload=None, error=None):
        self.payload = payload if payload is not None else {"diagram_type": "other", "requirements": []}
        self.error = error
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        import json
        from types import SimpleNamespace

        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        payload = self.payload(kwargs) if callable(self.payload) else self.payload
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])
