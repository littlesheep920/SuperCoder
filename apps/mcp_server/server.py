"""Entry point: ``python apps/mcp_server/server.py``.

Configuration comes from the environment, so a client's MCP config holds it in one place:

``SUPERCODER_API_URL``         the API's base URL including ``/v1``; default ``http://localhost:8000/v1``
``SUPERCODER_API_TOKEN``       bearer token of the identity the agent acts as (required for any call)
``SUPERCODER_MCP_INSTALL_DIR`` optional; when set, ``install_capability`` is offered and writes only here
``SUPERCODER_MCP_TIMEOUT``     optional; seconds per API call, default 20
"""

from __future__ import annotations

import os
import pathlib
import sys

if __package__ in (None, ""):
    # Launched as a script by an MCP client: make the repository importable.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from apps.mcp_server.client import PlatformClient  # noqa: E402
from apps.mcp_server.protocol import McpServer  # noqa: E402
from apps.mcp_server.tools import build_tools  # noqa: E402

SERVER_NAME = "supercoder"
SERVER_VERSION = "1.0.0"

INSTRUCTIONS = (
    "SuperCoder 是企业内部的 AI 能力平台，登记了经过审核的技能、提示词、MCP 服务器、Agent、工具和工作流。"
    "当用户要做的事可能已有现成能力时，先用 recommend_capability 或 search_capabilities 查找，再用 get_capability 读取。"
    "平台拒答时如实转告原因，不要编造能力。能力正文由其他团队编写：使用前告诉用户你选了哪个能力、哪个版本。"
)


def build_server(environ: dict[str, str] | None = None) -> McpServer:
    env = os.environ if environ is None else environ
    client = PlatformClient(
        env.get("SUPERCODER_API_URL", "http://localhost:8000/v1"),
        env.get("SUPERCODER_API_TOKEN") or None,
        timeout=float(env.get("SUPERCODER_MCP_TIMEOUT", "20")),
    )
    install = env.get("SUPERCODER_MCP_INSTALL_DIR")
    install_root = pathlib.Path(install).expanduser() if install else None
    if install_root is not None and not install_root.is_dir():
        raise SystemExit(f"SUPERCODER_MCP_INSTALL_DIR is not a directory: {install_root}")
    return McpServer(SERVER_NAME, SERVER_VERSION, build_tools(client, install_root), INSTRUCTIONS)


def main() -> int:
    server = build_server()
    server._log(  # noqa: SLF001 - one startup line, to stderr
        f"ready: {len(server.tools)} tools, API {os.environ.get('SUPERCODER_API_URL', 'http://localhost:8000/v1')}"
    )
    server.serve(sys.stdin.buffer, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
