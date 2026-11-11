"""The platform's REST API, as the MCP server calls it. Standard library only.

Every operation here is one the frozen contract defines (`apps/api/openapi.stage0.yaml`); the
test suite checks each method and path against it, and checks the request bodies against the
contract's request schemas, which refuse unknown fields.

The bearer token is sent and never repeated: no message, log line or exception built here
contains it.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["CALLS", "PlatformClient", "PlatformError"]

#: (method, contract path) for every call below. Kept as data so the contract test can read it.
CALLS = {
    "search": ("POST", "/search/resources"),
    "recommend": ("POST", "/discovery/recommend"),
    "version": ("GET", "/resource-versions/{versionId}"),
    "body": ("GET", "/resource-versions/{versionId}/body"),
    "versions": ("GET", "/resources/{resourceId}/versions"),
    "export_targets": ("GET", "/export-targets"),
    "export": ("GET", "/resource-versions/{versionId}/export"),
}

MAX_ARCHIVE_BYTES = 5 * 1024 * 1024


class PlatformError(Exception):
    """A call that did not succeed, described for the person or model reading it."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _segment(value: str) -> str:
    # Identifiers go into the path; quote everything so a crafted id cannot change the route.
    return urllib.parse.quote(str(value), safe="")


class PlatformClient:
    def __init__(self, base_url: str, token: str | None, *, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ calls

    def search(self, query: str, *, kinds: list[str], limit: int) -> dict:
        body = {"query": query, "filters": {"kinds": kinds, "statuses": ["published"]}, "limit": limit}
        return self._call("POST", "/search/resources", body=body).json()

    def recommend(self, question: str, *, context: str, max_recommendations: int) -> dict:
        body = {"question": question, "context": context, "max_recommendations": max_recommendations}
        return self._call("POST", "/discovery/recommend", body=body).json()

    def version(self, version_id: str) -> dict:
        return self._call("GET", f"/resource-versions/{_segment(version_id)}").json()

    def body(self, version_id: str) -> dict:
        return self._call("GET", f"/resource-versions/{_segment(version_id)}/body").json()

    def versions(self, resource_id: str, *, status: str | None = None) -> dict:
        query = {"limit": "100", **({"status": status} if status else {})}
        return self._call("GET", f"/resources/{_segment(resource_id)}/versions", query=query).json()

    def export_targets(self) -> dict:
        return self._call("GET", "/export-targets").json()

    def export(self, version_id: str, target: str) -> Response:
        return self._call(
            "GET",
            f"/resource-versions/{_segment(version_id)}/export",
            query={"target": target},
            accept="application/zip",
            limit=MAX_ARCHIVE_BYTES,
        )

    # ------------------------------------------------------------------ transport

    def _call(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: Any = None,
        accept: str = "application/json",
        limit: int = MAX_ARCHIVE_BYTES,
    ) -> Response:
        if not self._token:
            raise PlatformError(
                "SUPERCODER_API_TOKEN is not set; the platform only answers authenticated requests."
            )
        url = self.base_url + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", accept)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read(limit + 1)
                if len(payload) > limit:
                    raise PlatformError(f"{method} {path}: response is larger than {limit} bytes")
                return Response(response.status, dict(response.headers.items()), payload)
        except urllib.error.HTTPError as error:
            raise self._describe(error, method, path) from None
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise PlatformError(f"cannot reach the platform at {self.base_url}: {reason}") from None

    def _describe(self, error: urllib.error.HTTPError, method: str, path: str) -> PlatformError:
        code = detail = None
        try:
            problem = json.loads(error.read(64 * 1024).decode("utf-8"))
            if isinstance(problem, dict):
                code = problem.get("code") or (problem.get("error") or {}).get("code")
                detail = problem.get("message") or problem.get("detail") or problem.get("title")
        except (ValueError, UnicodeDecodeError, OSError):
            pass
        if error.code == 401:
            text = "the platform rejected the token (401); check SUPERCODER_API_TOKEN"
        elif error.code == 403:
            text = "the token's identity may not do this (403)"
        elif error.code == 404:
            text = "not found, or not visible to this identity (404)"
        else:
            text = f"{method} {path} returned {error.code}"
        if code or detail:
            text += f": {code or ''}{' ' if code and detail else ''}{detail or ''}"
        if self._token and self._token in text:
            text = text.replace(self._token, "***")
        return PlatformError(text, status=error.code, code=code)
