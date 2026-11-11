"""The MCP server: what an agent (and the platform) can rely on.

* **Protocol.** JSON-RPC 2.0 framing, version negotiation, notifications never answered, protocol
  errors versus tool errors as MCP separates them (`ProtocolTests`).
* **Contract.** Every call the client makes is an operation of the frozen contract, and every
  request body satisfies the contract's request schema, which refuses unknown fields
  (`ContractTests`).
* **Behaviour against an API.** A local HTTP server answering in the contract's shapes: what each
  tool sends and what it says back, including refusals and errors, and that the token is never
  repeated (`ToolTests`).
* **Install safety.** Opt-in only; writes only under the install directory; refuses traversal,
  absolute paths, symlinks and overwrites, and writes nothing when it refuses (`InstallTests`).
* **Standalone.** Imports only the standard library, and speaks over real stdio (`StdioTests`).
"""

from __future__ import annotations

import ast
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from apps.mcp_server.client import CALLS, PlatformClient  # noqa: E402
from apps.mcp_server.protocol import (  # noqa: E402
    INVALID_PARAMS,
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    McpServer,
    Tool,
    ToolError,
    ToolResult,
)
from apps.mcp_server.server import build_server  # noqa: E402

TOKEN = "dev-owner-token-SECRET"
VERSION_ID = "01HZX0000000000000000000V1"
RESOURCE_ID = "01HZX0000000000000000000R1"
HASH = "sha256:" + "a" * 64


# ---------------------------------------------------------------------- stub API


class StubApi:
    """A local HTTP server that records requests and answers from a route table."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.routes: dict[tuple[str, str], tuple[int, dict, bytes]] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                path, _, query = self.path.partition("?")
                stub.requests.append({
                    "method": self.command, "path": path, "query": query,
                    "authorization": self.headers.get("Authorization"),
                    "json": json.loads(raw) if raw else None,
                })
                status, headers, body = stub.routes.get((self.command, path), (404, {}, b'{"code":"NOT_FOUND"}'))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

            def log_message(self, *args) -> None:  # keep test output clean
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def json(self, method: str, path: str, payload, status: int = 200) -> None:
        self.routes[(method, "/v1" + path)] = (status, {"Content-Type": "application/json"},
                                               json.dumps(payload).encode("utf-8"))

    def raw(self, method: str, path: str, body: bytes, headers: dict, status: int = 200) -> None:
        self.routes[(method, "/v1" + path)] = (status, headers, body)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def search_hit(rank: int, slug: str) -> dict:
    return {
        "rank": rank, "score": 1.0 / rank,
        "resource": {"resource_id": RESOURCE_ID, "kind": "skill", "slug": slug, "owner_team": "platform"},
        "version": {"version_id": VERSION_ID, "resource_id": RESOURCE_ID, "version": "1.2.0",
                    "name": "DBC Checker", "status": "published", "artifact_hash": HASH},
        "source_artifact_hash": HASH, "matched_fields": ["description"], "highlights": ["checks DBC signals"],
    }


def version_record(status: str = "published") -> dict:
    return {
        "version_id": VERSION_ID, "resource_id": RESOURCE_ID, "version": "1.2.0", "name": "DBC Checker",
        "status": status, "artifact_hash": HASH, "description": "Checks CAN signals against a DBC file.",
        "manifest": {"kind": "skill", "slug": "dbc-checker", "entrypoint": "SKILL.md",
                     "inputs": [{"name": "dbc", "type": "file", "description": "The DBC file."}],
                     "outputs": [], "permissions": ["repository:read"]},
    }


def zip_bytes(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


def call(server: McpServer, name: str, arguments: dict | None = None, request_id: int = 7) -> dict:
    reply = server.handle({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                           "params": {"name": name, "arguments": arguments or {}}})
    return reply


def text_of(reply: dict) -> str:
    return reply["result"]["content"][0]["text"]


# ---------------------------------------------------------------------- protocol


def echo_tool() -> Tool:
    schema = {"type": "object", "properties": {"word": {"type": "string", "minLength": 1},
                                               "times": {"type": "integer", "minimum": 1, "maximum": 3}},
              "required": ["word"], "additionalProperties": False}

    def handle(arguments: dict) -> ToolResult:
        if arguments["word"] == "fail":
            raise ToolError("asked to fail")
        if arguments["word"] == "crash":
            raise RuntimeError("boom")
        return ToolResult(arguments["word"] * arguments.get("times", 1), {"ok": True})

    return Tool("echo", "Echo a word.", schema, handle)


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = io.StringIO()
        self.server = McpServer("test", "0", [echo_tool()], "hello", log=self.log)

    def request(self, method: str, params=None, request_id=1) -> dict:
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        return self.server.handle(message)

    def test_initialize_accepts_a_supported_version(self) -> None:
        result = self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(result["serverInfo"], {"name": "test", "version": "0"})
        self.assertEqual(result["instructions"], "hello")

    def test_initialize_answers_an_unknown_version_with_the_latest(self) -> None:
        result = self.request("initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(result["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_tools_are_listed_with_their_schemas(self) -> None:
        (tool,) = self.request("tools/list")["result"]["tools"]
        self.assertEqual(tool["name"], "echo")
        self.assertEqual(tool["inputSchema"]["type"], "object")
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_a_call_returns_text_and_structured_content(self) -> None:
        result = self.request("tools/call", {"name": "echo", "arguments": {"word": "ab", "times": 2}})["result"]
        self.assertEqual(result, {"content": [{"type": "text", "text": "abab"}], "isError": False,
                                  "structuredContent": {"ok": True}})

    def test_request_ids_come_back_unchanged(self) -> None:
        for request_id in (0, 41, "req-1"):
            with self.subTest(request_id=request_id):
                self.assertEqual(self.request("ping", request_id=request_id), {"jsonrpc": "2.0", "id": request_id, "result": {}})

    def test_protocol_errors(self) -> None:
        cases = [
            ("unknown tool", self.request("tools/call", {"name": "nope"}), INVALID_PARAMS),
            ("unknown method", self.request("resources/list"), METHOD_NOT_FOUND),
            ("params not an object", self.request("tools/list", params=[1]), INVALID_PARAMS),
            ("batch", self.server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]), INVALID_REQUEST),
            ("not JSON-RPC 2.0", self.server.handle({"id": 1, "method": "ping"}), INVALID_REQUEST),
        ]
        for label, reply, code in cases:
            with self.subTest(label):
                self.assertEqual(reply["error"]["code"], code)

    def test_malformed_json_is_a_parse_error_with_a_null_id(self) -> None:
        reply = json.loads(self.server.handle_line("{not json"))
        self.assertEqual((reply["id"], reply["error"]["code"]), (None, PARSE_ERROR))

    def test_notifications_are_never_answered(self) -> None:
        for message in ({"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "nope"}},
                        {"jsonrpc": "2.0", "method": "no/such/method"}):
            with self.subTest(message["method"]):
                self.assertIsNone(self.server.handle(message))
        self.assertIsNone(self.server.handle_line("   \n"))

    def test_tool_failures_are_results_the_model_can_read(self) -> None:
        cases = {
            "bad arguments": ({"word": "", "times": 9, "extra": 1}, ["must not be empty", "at most 3", "extra"]),
            "missing argument": ({}, ["word is required"]),
            "wrong type": ({"word": "x", "times": True}, ["times must be integer"]),
            "tool error": ({"word": "fail"}, ["asked to fail"]),
            "unexpected exception": ({"word": "crash"}, ["failed unexpectedly (RuntimeError)"]),
        }
        for label, (arguments, fragments) in cases.items():
            with self.subTest(label):
                result = self.request("tools/call", {"name": "echo", "arguments": arguments})["result"]
                self.assertTrue(result["isError"])
                for fragment in fragments:
                    self.assertIn(fragment, result["content"][0]["text"])
        self.assertIn("boom", self.log.getvalue(), "the traceback detail goes to stderr, not to the model")

    def test_every_output_line_is_ascii_json(self) -> None:
        server = McpServer("t", "0", [Tool("zh", "中文", {"type": "object"}, lambda _: ToolResult("能力"))])
        line = server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "zh"}}))
        self.assertTrue(line.isascii())
        self.assertNotIn("\n", line)
        self.assertEqual(text_of(json.loads(line)), "能力")


# ---------------------------------------------------------------------- contract


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = yaml.safe_load((ROOT / "apps" / "api" / "openapi.stage0.yaml").read_text(encoding="utf-8"))
        cls.schemas = cls.contract["components"]["schemas"]

    def test_every_call_is_a_contract_operation(self) -> None:
        for name, (method, path) in CALLS.items():
            with self.subTest(name):
                self.assertIn(path, self.contract["paths"])
                self.assertIn(method.lower(), self.contract["paths"][path])

    def check_body(self, schema_name: str, body: dict) -> None:
        schema = self.schemas[schema_name]
        self.assertFalse(schema.get("additionalProperties", True), "the contract must refuse unknown fields")
        self.assertLessEqual(set(body), set(schema["properties"]))
        self.assertLessEqual(set(schema.get("required", [])), set(body))

    def test_request_bodies_satisfy_the_contract(self) -> None:
        api = StubApi()
        self.addCleanup(api.close)
        api.json("POST", "/search/resources", {"query_id": "q", "items": [], "page": {"limit": 5, "has_more": False}, "took_ms": 1})
        api.json("POST", "/discovery/recommend", {"answered": False})
        client = PlatformClient(api.base_url, TOKEN)
        client.search("dbc", kinds=["skill"], limit=5)
        client.recommend("check dbc", context="", max_recommendations=3)
        search, recommend = (request["json"] for request in api.requests)
        self.check_body("ResourceSearchRequest", search)
        self.assertLessEqual(set(search["filters"]), set(self.schemas["ResourceSearchFilters"]["properties"]))
        self.assertIn("published", self.schemas["LifecycleStatus"]["enum"])
        self.check_body("DiscoveryRequest", recommend)


# ---------------------------------------------------------------------- tools against the stub


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = StubApi()
        self.addCleanup(self.api.close)
        self.server = build_server({"SUPERCODER_API_URL": self.api.base_url, "SUPERCODER_API_TOKEN": TOKEN})

    def test_search_asks_for_published_versions_and_names_each_hit(self) -> None:
        self.api.json("POST", "/search/resources", {"query_id": "q", "items": [search_hit(1, "dbc-checker")],
                                                     "page": {"limit": 5, "has_more": False}, "took_ms": 3})
        reply = call(self.server, "search_capabilities", {"query": "检查 DBC", "kinds": ["skill"]})
        (sent,) = self.api.requests
        self.assertEqual(sent["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(sent["json"], {"query": "检查 DBC", "filters": {"kinds": ["skill"], "statuses": ["published"]}, "limit": 5})
        text = text_of(reply)
        self.assertIn("dbc-checker 1.2.0", text)
        self.assertIn(VERSION_ID, text)
        self.assertIn(HASH, text)
        self.assertEqual(reply["result"]["structuredContent"]["items"][0]["version_id"], VERSION_ID)

    def test_a_refusal_is_passed_on_with_its_reason_and_candidates(self) -> None:
        self.api.json("POST", "/discovery/recommend", {
            "answered": False, "recommendations": [], "refusal_reason": "no candidate covers OTA rollback",
            "clarifying_questions": ["Which ECU?"], "considered": ["skill/a", "tool/b"], "degraded": []})
        text = text_of(call(self.server, "recommend_capability", {"need": "OTA 回滚"}))
        for fragment in ("没有给出推荐", "no candidate covers OTA rollback", "Which ECU?", "skill/a"):
            self.assertIn(fragment, text)

    def test_a_recommendation_carries_its_reason_and_hash(self) -> None:
        self.api.json("POST", "/discovery/recommend", {"answered": True, "recommendations": [
            {"resource_id": RESOURCE_ID, "kind": "skill", "version": "1.2.0", "artifact_hash": HASH,
             "reason": "matches DBC validation", "score": 0.9}]})
        text = text_of(call(self.server, "recommend_capability", {"need": "check DBC", "max_recommendations": 1}))
        self.assertIn("matches DBC validation", text)
        self.assertIn(RESOURCE_ID, text)
        self.assertEqual(self.api.requests[0]["json"]["max_recommendations"], 1)

    def test_get_shows_the_body_and_says_it_was_not_verified(self) -> None:
        self.api.json("GET", f"/resource-versions/{VERSION_ID}", version_record())
        self.api.json("GET", f"/resource-versions/{VERSION_ID}/body",
                      {"version_id": VERSION_ID, "entrypoint": "SKILL.md", "body": "# DBC\n\nStep one.\n", "available": True})
        reply = call(self.server, "get_capability", {"version_id": VERSION_ID})
        text = text_of(reply)
        self.assertIn("Step one.", text)
        self.assertIn("尚未按内容哈希核对", text)
        self.assertIn("allow repository:read", text.replace("声明的权限：repository:read", "allow repository:read"))
        self.assertFalse(reply["result"]["structuredContent"]["body_verified"])
        self.assertNotIn("未经完整审核", text)

    def test_get_labels_an_unpublished_version(self) -> None:
        self.api.json("GET", f"/resource-versions/{VERSION_ID}", version_record("draft"))
        self.api.json("GET", f"/resource-versions/{VERSION_ID}/body",
                      {"version_id": VERSION_ID, "entrypoint": "SKILL.md", "body": "x", "available": True})
        self.assertIn("未经完整审核", text_of(call(self.server, "get_capability", {"version_id": VERSION_ID})))

    def test_get_by_resource_picks_the_highest_published_version(self) -> None:
        listing = {"items": [{"version_id": "V-1-9", "version": "1.9.0"}, {"version_id": "V-1-10", "version": "1.10.0"},
                             {"version_id": "V-2-0-RC", "version": "2.0.0-rc.1"}], "page": {"limit": 100, "has_more": False}}
        self.api.json("GET", f"/resources/{RESOURCE_ID}/versions", listing)
        self.api.json("GET", "/resource-versions/V-1-10", version_record())
        self.api.json("GET", "/resource-versions/V-1-10/body", {"version_id": "V-1-10", "entrypoint": "SKILL.md", "body": "b", "available": True})
        reply = call(self.server, "get_capability", {"resource_id": RESOURCE_ID})
        self.assertFalse(reply["result"]["isError"], text_of(reply))
        self.assertEqual(self.api.requests[0]["query"], "limit=100&status=published")

    def test_a_prerelease_is_chosen_only_by_name(self) -> None:
        listing = {"items": [{"version_id": "V-RC", "version": "2.0.0-rc.1"}], "page": {"limit": 100, "has_more": False}}
        self.api.json("GET", f"/resources/{RESOURCE_ID}/versions", listing)
        reply = call(self.server, "get_capability", {"resource_id": RESOURCE_ID})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("预发布版本需要用 version 指名", text_of(reply))

    def test_get_needs_an_identifier(self) -> None:
        reply = call(self.server, "get_capability", {})
        self.assertTrue(reply["result"]["isError"])
        self.assertEqual(self.api.requests, [])

    def test_identifiers_cannot_change_the_route(self) -> None:
        call(self.server, "get_capability", {"version_id": "../../audit"})
        self.assertEqual(self.api.requests[0]["path"], "/v1/resource-versions/..%2F..%2Faudit")

    def test_errors_explain_and_never_repeat_the_token(self) -> None:
        self.api.json("POST", "/search/resources", {"code": "UNAUTHORIZED", "message": f"bad token {TOKEN}"}, status=401)
        text = text_of(call(self.server, "search_capabilities", {"query": "x"}))
        self.assertIn("SUPERCODER_API_TOKEN", text)
        self.assertNotIn(TOKEN, text)
        self.api.json("POST", "/search/resources", {"code": "PROJECTION_UNAVAILABLE", "message": "index rebuilding"}, status=503)
        self.assertIn("PROJECTION_UNAVAILABLE index rebuilding", text_of(call(self.server, "search_capabilities", {"query": "x"})))

    def test_without_a_token_nothing_is_sent(self) -> None:
        server = build_server({"SUPERCODER_API_URL": self.api.base_url})
        reply = call(server, "search_capabilities", {"query": "x"})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("SUPERCODER_API_TOKEN is not set", text_of(reply))
        self.assertEqual(self.api.requests, [])

    def test_an_unreachable_platform_is_a_tool_error(self) -> None:
        self.api.close()
        reply = call(self.server, "search_capabilities", {"query": "x"})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("cannot reach the platform", text_of(reply))
        self.api = StubApi()  # so the cleanup has something to close


# ---------------------------------------------------------------------- install


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = StubApi()
        self.addCleanup(self.api.close)
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.server = build_server({"SUPERCODER_API_URL": self.api.base_url, "SUPERCODER_API_TOKEN": TOKEN,
                                    "SUPERCODER_MCP_INSTALL_DIR": str(self.root)})

    def serve_archive(self, data: bytes) -> None:
        self.api.raw("GET", f"/resource-versions/{VERSION_ID}/export", data,
                     {"Content-Type": "application/zip", "X-Export-Digest": "sha256:" + "b" * 64,
                      "X-Export-Warnings": "other_runtimes_not_exported"})

    def written(self) -> list[str]:
        return sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file())

    def test_install_is_offered_only_with_a_directory(self) -> None:
        names = lambda server: [t["name"] for t in server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
        self.assertIn("install_capability", names(self.server))
        self.assertNotIn("install_capability", names(build_server({"SUPERCODER_API_TOKEN": TOKEN})))
        tool = [t for t in self.server.tools if t.name == "install_capability"][0]
        self.assertFalse(tool.read_only)

    def test_a_published_version_lands_in_the_targets_layout(self) -> None:
        self.serve_archive(zip_bytes({".claude/skills/dbc-checker/SKILL.md": b"# DBC\n"}))
        reply = call(self.server, "install_capability", {"version_id": VERSION_ID})
        self.assertFalse(reply["result"]["isError"], text_of(reply))
        self.assertEqual(self.written(), [".claude/skills/dbc-checker/SKILL.md"])
        self.assertEqual(self.api.requests[0]["query"], "target=claude-code")
        self.assertIn("other_runtimes_not_exported", text_of(reply))

    def test_unsafe_archives_write_nothing(self) -> None:
        cases = {
            "parent traversal": {"ok.md": b"x", "../escape.md": b"x"},
            "absolute path": {"ok.md": b"x", "/tmp/escape.md": b"x"},
            "backslash path": {"ok.md": b"x", "a\\..\\..\\escape.md": b"x"},
            "drive letter": {"ok.md": b"x", "C:/escape.md": b"x"},
        }
        for label, entries in cases.items():
            with self.subTest(label):
                self.serve_archive(zip_bytes(entries))
                reply = call(self.server, "install_capability", {"version_id": VERSION_ID})
                self.assertTrue(reply["result"]["isError"])
                self.assertIn("未写入任何文件", text_of(reply))
                self.assertEqual(self.written(), [])
        self.serve_archive(zip_bytes({"ok.md": b"x"}, symlink="link"))
        self.assertIn("符号链接", text_of(call(self.server, "install_capability", {"version_id": VERSION_ID})))
        self.assertEqual(self.written(), [])

    def test_existing_files_are_kept_unless_overwrite_is_asked(self) -> None:
        target = self.root / ".claude/skills/dbc-checker/SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("mine\n", encoding="utf-8")
        self.serve_archive(zip_bytes({".claude/skills/dbc-checker/SKILL.md": b"# DBC\n",
                                      ".claude/skills/dbc-checker/notes.md": b"new\n"}))
        refused = call(self.server, "install_capability", {"version_id": VERSION_ID})
        self.assertTrue(refused["result"]["isError"])
        self.assertEqual(target.read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(self.written(), [".claude/skills/dbc-checker/SKILL.md"], "nothing else written either")
        call(self.server, "install_capability", {"version_id": VERSION_ID, "overwrite": True})
        self.assertEqual(target.read_text(encoding="utf-8"), "# DBC\n")

    def test_the_platforms_refusal_is_passed_on(self) -> None:
        self.api.json("GET", f"/resource-versions/{VERSION_ID}/export",
                      {"code": "CONTENT_HASH_MISMATCH", "message": "the checkout holds version 1.3.0"}, status=409)
        text = text_of(call(self.server, "install_capability", {"version_id": VERSION_ID}))
        self.assertIn("CONTENT_HASH_MISMATCH", text)
        self.assertEqual(self.written(), [])


# ---------------------------------------------------------------------- standalone


class StdioTests(unittest.TestCase):
    def test_the_package_imports_only_the_standard_library(self) -> None:
        foreign = []
        for path in (ROOT / "apps" / "mcp_server").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top not in sys.stdlib_module_names and not name.startswith("apps.mcp_server"):
                        foreign.append(f"{path.name}: {name}")
        self.assertEqual(foreign, [])

    def test_a_session_over_real_stdio(self) -> None:
        api = StubApi()
        self.addCleanup(api.close)
        api.json("POST", "/search/resources", {"query_id": "q", "items": [search_hit(1, "dbc-checker")],
                                                "page": {"limit": 5, "has_more": False}, "took_ms": 3})
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_capabilities", "arguments": {"query": "DBC 校验"}}},
        ]
        env = dict(os.environ, SUPERCODER_API_URL=api.base_url, SUPERCODER_API_TOKEN=TOKEN, PYTHONIOENCODING="utf-8")
        env.pop("SUPERCODER_MCP_INSTALL_DIR", None)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "apps" / "mcp_server" / "server.py")],
            input="".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages).encode("utf-8"),
            capture_output=True, env=env, cwd=tempfile.gettempdir(), timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        lines = completed.stdout.decode("ascii").splitlines()
        replies = [json.loads(line) for line in lines]
        self.assertEqual([reply["id"] for reply in replies], [1, 2, 3], "one reply per request, none for the notification")
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(replies[1]["result"]["tools"]), 3)
        self.assertIn("dbc-checker 1.2.0", text_of(replies[2]))
        self.assertIn("ready: 3 tools", completed.stderr.decode("utf-8"))
        self.assertNotIn(TOKEN, completed.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
