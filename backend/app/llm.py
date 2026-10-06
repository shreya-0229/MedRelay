"""LLM provider abstraction, adapted from the v1 prototype.

The backend runs on fully deterministic, template-based logic
(DeterministicProvider) so it needs no API keys and behaves identically on
every run.

To use a real model, set MEDRELAY_LLM_PROVIDER=gemini and
MEDRELAY_LLM_KEY=<key> (GeminiProvider reads the key ONLY from the
environment — never hardcode it). Use get_provider() to pick the backend;
agents accept an optional `llm` argument.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LLMProvider:
    """Pluggable LLM interface. Implement complete() to use a real model."""

    real: bool = False  # True for providers that call a live model

    def complete(self, prompt: str) -> str:
        """Return the model's text completion for prompt."""
        raise NotImplementedError(
            "Override complete() to plug in a real LLM (OpenAI, Gemini, ...)")


class DeterministicProvider(LLMProvider):
    """Default: canned, template-based replies. No API keys, ever."""

    def complete(self, prompt: str) -> str:
        text = prompt.lower()
        if "triage" in text:
            return "TRIAGE: assess severity (1-5) and route to the matching care pathway."
        if "dispatch" in text:
            return "DISPATCH: choose the nearest available capable ambulance."
        if "hospital" in text:
            return "HOSPITAL: reserve a bed at the nearest hospital with the needed specialty."
        if "comms" in text:
            return "COMMS: notify family in their language and pre-alert the ER."
        return "ACK: processed deterministically for demo purposes."


class GeminiProvider(LLMProvider):
    """Real LLM via the Gemini REST API (no extra dependencies, stdlib only).

    The API key is read ONLY from the MEDRELAY_LLM_KEY environment variable.
    Get a free key at https://aistudio.google.com/ and never commit it.
    Model defaults to gemini-2.0-flash (override with MEDRELAY_GEMINI_MODEL).
    """

    real = True

    def __init__(self, api_key: str | None = None,
                 model: str | None = None, timeout: int = 15) -> None:
        self.api_key = api_key or os.environ.get("MEDRELAY_LLM_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "GeminiProvider needs an API key: set the MEDRELAY_LLM_KEY "
                "environment variable (free key at https://aistudio.google.com).")
        self.model = model or os.environ.get("MEDRELAY_GEMINI_MODEL",
                                             "gemini-2.0-flash")
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 300},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(
                f"Gemini API error {exc.code}: {detail} "
                f"(check that MEDRELAY_LLM_KEY is a valid AI Studio key)"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Gemini request failed: {exc}") from exc
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Unexpected Gemini response shape: {str(data)[:200]}") from exc


def get_provider() -> LLMProvider:
    """Pick the LLM backend from the environment.

    MEDRELAY_LLM_PROVIDER=gemini  -> GeminiProvider (needs MEDRELAY_LLM_KEY)
    anything else / unset         -> DeterministicProvider (offline default)
    """
    if os.environ.get("MEDRELAY_LLM_PROVIDER", "").strip().lower() == "gemini":
        return GeminiProvider()
    return DeterministicProvider()
