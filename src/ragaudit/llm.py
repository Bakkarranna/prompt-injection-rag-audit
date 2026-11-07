"""The LLM client.

Calls a local Ollama server so the measurements were produced without an API
key or a paid account. Answers are generated at temperature 0 with a fixed
seed, so a rerun of the same audit either reproduces the same answer or shows a
sampling effect that is itself worth reporting.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class Completion:
    """One LLM answer with its cost in tokens."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    model: str


class OllamaClient:
    """A minimal Ollama /api/generate client.

    A stalled answer raises a read timeout. The payload is fixed at temperature
    0 and seed 42, so re-asking the same question after a transport failure
    asks for the answer the ask would have produced: a retry costs time without
    changing the record. Three attempts are made before the error propagates.
    """

    attempts: int = 3

    def __init__(
        self,
        model: str = "qwen2.5:1.5b",
        url: str = "http://127.0.0.1:11434",
        timeout_s: float = 120.0,
    ) -> None:
        self.model = model
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def generate(self, prompt: str, system: str) -> Completion:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0, "seed": 42},
        }
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = requests.post(
                    f"{self.url}/api/generate", json=payload, timeout=self.timeout_s
                )
                response.raise_for_status()
                body = response.json()
            except (requests.Timeout, requests.ConnectionError) as error:
                # Transport failure rather than a bad request, so the same
                # payload is worth sending again.
                last_error = error
                if attempt == self.attempts:
                    break
                print(
                    f"  retry {attempt}/{self.attempts - 1} after {type(error).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(5.0)
                continue
            return Completion(
                text=str(body.get("response", "")).strip(),
                prompt_tokens=int(body.get("prompt_eval_count", 0)),
                completion_tokens=int(body.get("eval_count", 0)),
                latency_ms=float(body.get("total_duration", 0)) / 1e6,
                model=self.model,
            )
        raise last_error if last_error is not None else RuntimeError("no attempt")

    def available(self) -> bool:
        """Report whether the local server answers, so callers can skip it."""

        try:
            response = requests.get(f"{self.url}/api/tags", timeout=5.0)
            response.raise_for_status()
        except requests.RequestException:
            return False
        return True

    def unload(self) -> None:
        """Release the model from Ollama's memory before the embedding phase."""

        try:
            requests.post(
                f"{self.url}/api/generate",
                json={"model": self.model, "prompt": "", "keep_alive": 0},
                timeout=30.0,
            )
        except requests.RequestException:
            pass


def default_client(settings) -> OllamaClient | None:
    """Return a live client, or None when Ollama is not running."""

    if os.environ.get("RAGAUDIT_NO_LLM") == "1":
        return None
    client = OllamaClient(model=settings.ollama_model, url=settings.ollama_url)
    return client if client.available() else None
