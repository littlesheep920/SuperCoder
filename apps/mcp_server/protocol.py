"""A Model Context Protocol server over stdio, in the standard library only.

Why not the official SDK: this server runs on an engineer's machine, launched by Claude Code or
Cursor, and should start with nothing but Python and a checkout. The part of MCP it needs is
small — JSON-RPC 2.0 over newline-delimited stdio, ``initialize``, ``ping``, ``tools/list`` and
``tools/call`` — and is written out here so it can be read in one sitting and tested without
installing anything.

Framing (MCP "stdio" transport): one JSON-RPC message per line, UTF-8, no embedded newlines.
Only protocol messages go to stdout; diagnostics go to stderr.

Error handling follows the MCP split:

* a malformed message, an unknown method or an unknown tool is a JSON-RPC **error**;
* arguments that do not match a tool's ``inputSchema``, and failures while the tool runs, are a
  tool **result** with ``isError: true``. The model sees those and can correct its call, which
  it cannot do with a protocol error.

Notifications (messages without ``id``) never get a response, not even an error.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, TextIO

__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "McpServer",
    "Tool",
    "ToolError",
    "ToolResult",
    "validate_arguments",
]

#: Newest first. A client asking for one of these gets it; anything else gets the newest.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """A failure the model should read: returned as a result with ``isError: true``."""


@dataclass(frozen=True, slots=True)
class ToolResult:
    text: str
    structured: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[[dict[str, Any]], ToolResult]
    title: str | None = None
    read_only: bool = True

    def listing(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "annotations": {"readOnlyHint": self.read_only, "openWorldHint": False},
        }
        if self.title:
            entry["title"] = self.title
        return entry


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_arguments(schema: Mapping[str, Any], arguments: Any, path: str = "arguments") -> list[str]:
    """The subset of JSON Schema the tools here use; returns every problem, not just the first."""

    problems: list[str] = []
    expected = schema.get("type")
    if expected:
        python_type = _JSON_TYPES[expected]
        # bool is an int in Python; JSON keeps them apart.
        if not isinstance(arguments, python_type) or (expected in ("integer", "number") and isinstance(arguments, bool)):
            return [f"{path} must be {expected}"]
    if "enum" in schema and arguments not in schema["enum"]:
        problems.append(f"{path} must be one of {list(schema['enum'])}")
    if isinstance(arguments, str):
        if len(arguments) < schema.get("minLength", 0):
            problems.append(f"{path} must not be empty" if schema.get("minLength") == 1 else f"{path} is too short")
        if "maxLength" in schema and len(arguments) > schema["maxLength"]:
            problems.append(f"{path} is longer than {schema['maxLength']} characters")
    if isinstance(arguments, (int, float)) and not isinstance(arguments, bool):
        if "minimum" in schema and arguments < schema["minimum"]:
            problems.append(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and arguments > schema["maximum"]:
            problems.append(f"{path} must be at most {schema['maximum']}")
    if isinstance(arguments, list):
        if "maxItems" in schema and len(arguments) > schema["maxItems"]:
            problems.append(f"{path} has more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in arguments}) != len(arguments):
            problems.append(f"{path} has duplicate items")
        for index, item in enumerate(arguments):
            problems.extend(validate_arguments(schema.get("items", {}), item, f"{path}[{index}]"))
    if isinstance(arguments, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in arguments:
                problems.append(f"{path}.{name} is required")
        for name, value in arguments.items():
            if name in properties:
                problems.extend(validate_arguments(properties[name], value, f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{name} is not a known argument")
    return problems


@dataclass
class McpServer:
    name: str
    version: str
    tools: list[Tool]
    instructions: str = ""
    log: TextIO = field(default_factory=lambda: sys.stderr)
    protocol_version: str | None = None

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate tool names: {names}")
        self._tools = {tool.name: tool for tool in self.tools}

    # -------------------------------------------------------------- transport

    def serve(self, stdin, stdout) -> None:
        """Read one message per line until end of input; write one response per request."""

        for raw in stdin:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            reply = self.handle_line(line)
            if reply is not None:
                data = reply + "\n"
                if hasattr(stdout, "buffer"):
                    stdout.buffer.write(data.encode("utf-8"))
                else:
                    stdout.write(data)
                stdout.flush()

    def handle_line(self, line: str) -> str | None:
        if not line.strip():
            return None
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            return _dump(_error(None, PARSE_ERROR, f"parse error: {error.msg}"))
        reply = self.handle(message)
        return None if reply is None else _dump(reply)

    # -------------------------------------------------------------- dispatch

    def handle(self, message: Any) -> dict[str, Any] | None:
        if isinstance(message, list):
            # Batching was removed from MCP in 2025-06-18; refuse it rather than half-support it.
            return _error(None, INVALID_REQUEST, "batched requests are not supported")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(_id_of(message), INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method = message.get("method")
        is_notification = "id" not in message
        if not isinstance(method, str):
            # A response from the client (we send no requests) or garbage: nothing to answer.
            return None if is_notification else _error(message.get("id"), INVALID_REQUEST, "method is missing")
        if is_notification:
            return None
        request_id = message["id"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": [tool.listing() for tool in self.tools]})
            if method == "tools/call":
                return self._call(request_id, params)
        except Exception as error:  # noqa: BLE001 - a server must answer, not crash the client
            self._log(f"internal error in {method}: {type(error).__name__}: {error}")
            return _error(request_id, INTERNAL_ERROR, "internal error")
        return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        self.protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        result: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _call(self, request_id: Any, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return _error(request_id, INVALID_PARAMS, f"unknown tool: {name!r}")
        arguments = params.get("arguments")
        arguments = {} if arguments is None else arguments
        problems = validate_arguments(tool.input_schema, arguments)
        if problems:
            return _result(request_id, _tool_error("invalid arguments: " + "; ".join(problems)))
        try:
            outcome = tool.handler(dict(arguments))
        except ToolError as error:
            return _result(request_id, _tool_error(str(error)))
        except Exception as error:  # noqa: BLE001
            self._log(f"tool {tool.name} failed: {type(error).__name__}: {error}")
            return _result(request_id, _tool_error(f"{tool.name} failed unexpectedly ({type(error).__name__})"))
        result: dict[str, Any] = {"content": [{"type": "text", "text": outcome.text}], "isError": False}
        if outcome.structured is not None:
            result["structuredContent"] = dict(outcome.structured)
        return _result(request_id, result)

    def _log(self, text: str) -> None:
        print(f"[supercoder-mcp] {text}", file=self.log, flush=True)


def _id_of(message: Any) -> Any:
    return message.get("id") if isinstance(message, dict) else None


def _result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _dump(message: Mapping[str, Any]) -> str:
    # ensure_ascii keeps every line pure ASCII, so no console code page can corrupt the framing.
    return json.dumps(message, ensure_ascii=True, separators=(",", ":"))
