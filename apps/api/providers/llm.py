"""LLM access behind one narrow, fail-closed interface.

Security contract, the same one the search providers follow:

* **Disabled by default.** ``SUPERCODER_EXTERNAL_LLM_ENABLED`` must be true and a
  key must be present, or no network call happens and the secret is never read.
* **No secret or payload in errors.** Callers get a stable reason code, never the
  key, the prompt, or the response body.
* **Explicit failure.** This layer returns :class:`ProviderUnavailable` /
  :class:`ProviderError`; it never fabricates a plausible-looking answer.

The prompt-injection rule from the PRD applies here and is enforced by the
callers: resource bodies, user text, and model output are *data*. Nothing in this
module treats any of them as instructions. This client only transports text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..config import ExternalModelSettings

__all__ = [
    "LlmClient",
    "LlmError",
    "LlmResult",
    "LlmUnavailable",
    "OpenAiCompatibleClient",
    "build_llm_client",
]


class LlmError(RuntimeError):
    """An LLM call failed. Carries a stable reason, never payload or secret."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class LlmUnavailable(LlmError):
    """The LLM is disabled or not configured."""


@dataclass(frozen=True, slots=True)
class LlmResult:
    """One completion plus the facts needed to audit it."""

    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LlmClient(Protocol):
    @property
    def available(self) -> bool:
        """Whether a call can be attempted at all."""

    @property
    def name(self) -> str:
        """Provider/model identifier for diagnostics and reports."""

    def complete(self, *, system: str, user: str, json_mode: bool = False) -> LlmResult:
        """Return one completion.

        ``json_mode`` asks the provider to constrain output to a JSON object. It is
        a request, not a guarantee: callers must still validate the shape.
        """


class OpenAiCompatibleClient:
    """OpenAI-compatible chat completions over HTTP."""

    def __init__(self, settings: ExternalModelSettings, *, timeout: float | None = None) -> None:
        self._settings = settings
        self._timeout = timeout if timeout is not None else float(settings.request_timeout_seconds)
        self._available = bool(
            settings.enabled and settings.base_url and settings.model and bool(settings.api_key)
        )
        self._client: httpx.Client | None = None

    @property
    def available(self) -> bool:
        return self._available

    @property
    def name(self) -> str:
        if not self._settings.model:
            return "disabled"
        return f"{self._settings.provider or 'openai-compatible'}:{self._settings.model}"

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                # Connection reuse matters here for the same reason it did for
                # embeddings: the round trip dominates once the prompt is small.
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            )
        return self._client

    def warm_connection(self) -> None:
        """Open the TCP/TLS connection early so the first user request is not cold."""

        if not self._available:
            return
        try:
            self._http().get(
                self._settings.base_url.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {self._settings.api_key.expose()}"},
            )
        except Exception:  # noqa: BLE001 - warming must never break startup
            pass

    def complete(self, *, system: str, user: str, json_mode: bool = False) -> LlmResult:
        if not self._available:
            raise LlmUnavailable("llm_disabled")

        payload: dict[str, object] = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_output_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = self._http().post(
                self._settings.base_url.rstrip("/") + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.api_key.expose()}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except httpx.HTTPError as error:
            # Only the exception type crosses the boundary; URLs and bodies can
            # carry fragments of the prompt.
            raise LlmError("llm_transport_error") from type(error).__name__

        if response.status_code >= 400:
            raise LlmError("llm_http_error", status=response.status_code)

        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LlmError("llm_response_malformed") from type(error).__name__

        usage = body.get("usage") or {}
        return LlmResult(
            text=text,
            model=str(body.get("model") or self._settings.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


def build_llm_client(settings: ExternalModelSettings) -> LlmClient | None:
    """Return a client when enabled, otherwise ``None``.

    ``None`` rather than a stub is deliberate: a stub would make every caller
    check a flag, and one missed check would look like a working feature.
    """

    if not settings.enabled:
        return None
    client = OpenAiCompatibleClient(settings)
    if not client.available:
        return None
    return client
