"""The tools a coding agent gets: find, recommend, read and (opt-in) install capabilities.

Three are read-only and always listed. The fourth, ``install_capability``, writes files and is
listed only when ``SUPERCODER_MCP_INSTALL_DIR`` names a directory: an agent that was not given a
place to write cannot be talked into writing.

What the tools promise, and do not:

* search and install only see **published** versions — reviewed content. A draft found by id
  through ``get_capability`` is labelled as unreviewed.
* install goes through the export route, which refuses unless the checkout's files reproduce the
  version's artifact hash (ADAPTERS.md §5). So what lands on disk is what was reviewed.
* ``get_capability`` reads the body through ``GET /resource-versions/{id}/body``, which returns the
  checkout's current file without that check (STAGE4 §7). The tool says so in its output.
"""

from __future__ import annotations

import io
import pathlib
import stat
import zipfile
from typing import Any, Callable

from .client import PlatformClient, PlatformError
from .protocol import Tool, ToolError, ToolResult

KINDS = ["skill", "mcp", "prompt", "agent", "workflow", "tool"]
MAX_FILES = 200


def _call(action: Callable[[], Any]) -> Any:
    try:
        return action()
    except PlatformError as error:
        raise ToolError(str(error)) from None


def _stable_key(text: str) -> tuple[int, ...] | None:
    """MAJOR.MINOR.PATCH as integers, or None for a prerelease or anything unparseable.

    RESOURCE_GOVERNANCE.md §7.1: a default choice is the highest *stable* version; a prerelease
    is only ever chosen by naming it. Build metadata does not affect precedence.
    """

    core = str(text).split("+", 1)[0]
    if "-" in core:
        return None
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


# ---------------------------------------------------------------------- search


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 500,
                  "description": "要找的能力，用业务语言描述即可，例如“检查 CAN 报文的 DBC 定义”。"},
        "kinds": {"type": "array", "items": {"type": "string", "enum": KINDS}, "uniqueItems": True, "maxItems": 6,
                  "description": "只看这些类型；省略则不限。"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回几条，默认 5。"},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def search_handler(client: PlatformClient) -> Callable[[dict], ToolResult]:
    def handle(arguments: dict) -> ToolResult:
        found = _call(lambda: client.search(arguments["query"], kinds=arguments.get("kinds", []),
                                            limit=arguments.get("limit", 5)))
        items = found.get("items", [])
        if not items:
            return ToolResult("没有找到已发布的匹配能力。可以换一种说法，或用 recommend_capability 描述完整需求。",
                              {"items": []})
        lines = [f"找到 {len(items)} 个已发布的能力（按相关度排序）："]
        summary = []
        for hit in items:
            resource, version = hit.get("resource", {}), hit.get("version", {})
            entry = {
                "rank": hit.get("rank"),
                "kind": resource.get("kind"),
                "slug": resource.get("slug"),
                "name": version.get("name"),
                "version": version.get("version"),
                "version_id": version.get("version_id"),
                "artifact_hash": hit.get("source_artifact_hash") or version.get("artifact_hash"),
                "matched_fields": hit.get("matched_fields", []),
            }
            summary.append(entry)
            lines.append(f"{entry['rank']}. [{entry['kind']}] {entry['slug']} {entry['version']} — {entry['name']}")
            lines.append(f"   version_id={entry['version_id']}  {entry['artifact_hash']}")
            for highlight in hit.get("highlights", [])[:2]:
                lines.append(f"   …{highlight}…")
        lines.append("用 get_capability(version_id) 读取说明与正文。")
        return ToolResult("\n".join(lines), {"items": summary})

    return handle


# ---------------------------------------------------------------------- recommend


RECOMMEND_SCHEMA = {
    "type": "object",
    "properties": {
        "need": {"type": "string", "minLength": 1, "maxLength": 2000,
                 "description": "工程师要完成的事，越具体越好：输入是什么、要产出什么、在哪个环境里用。"},
        "context": {"type": "string", "maxLength": 4000, "description": "可选的补充背景，例如项目、技术栈、约束。"},
        "max_recommendations": {"type": "integer", "minimum": 1, "maximum": 8, "description": "默认 3。"},
    },
    "required": ["need"],
    "additionalProperties": False,
}


def recommend_handler(client: PlatformClient) -> Callable[[dict], ToolResult]:
    def handle(arguments: dict) -> ToolResult:
        answer = _call(lambda: client.recommend(arguments["need"], context=arguments.get("context", ""),
                                                max_recommendations=arguments.get("max_recommendations", 3)))
        lines: list[str] = []
        if answer.get("answered"):
            lines.append("平台推荐：")
            for index, item in enumerate(answer.get("recommendations", []), 1):
                lines.append(f"{index}. [{item.get('kind')}] {item.get('version')}  resource_id={item.get('resource_id')}")
                lines.append(f"   理由：{item.get('reason')}")
                lines.append(f"   内容哈希：{item.get('artifact_hash')}")
            lines.append("用 get_capability(resource_id, version) 读取说明与正文。")
        else:
            lines.append("平台没有给出推荐。")
            if answer.get("refusal_reason"):
                lines.append(f"原因：{answer['refusal_reason']}")
            for question in answer.get("clarifying_questions", []):
                lines.append(f"需要补充：{question}")
            considered = answer.get("considered", [])
            if considered:
                lines.append(f"已检查的候选（{len(considered)} 个）：" + "、".join(considered[:10]))
        for note in answer.get("degraded", []):
            lines.append(f"降级说明：{note}")
        return ToolResult("\n".join(lines), answer)

    return handle


# ---------------------------------------------------------------------- get


GET_SCHEMA = {
    "type": "object",
    "properties": {
        "version_id": {"type": "string", "minLength": 1, "maxLength": 64, "description": "版本 id（search 结果中的 version_id）。"},
        "resource_id": {"type": "string", "minLength": 1, "maxLength": 64,
                        "description": "资源 id（recommend 结果中的 resource_id），与 version 一起用；省略 version 时取最新的已发布版本。"},
        "version": {"type": "string", "minLength": 1, "maxLength": 64, "description": "语义化版本号，例如 1.2.0。"},
    },
    "additionalProperties": False,
}


def _resolve_version_id(client: PlatformClient, arguments: dict) -> str:
    if arguments.get("version_id"):
        return arguments["version_id"]
    if not arguments.get("resource_id"):
        raise ToolError("需要 version_id，或 resource_id（可加 version）。")
    wanted = arguments.get("version")
    listing = _call(lambda: client.versions(arguments["resource_id"], status=None if wanted else "published"))
    items = listing.get("items", [])
    if wanted:
        items = [item for item in items if item.get("version") == wanted]
        if not items:
            raise ToolError(f"资源 {arguments['resource_id']} 没有可见的版本 {wanted}。")
        return items[0]["version_id"]
    stable = [item for item in items if _stable_key(item.get("version", "")) is not None]
    if not stable:
        raise ToolError(f"资源 {arguments['resource_id']} 没有已发布的稳定版本；预发布版本需要用 version 指名。")
    return max(stable, key=lambda item: _stable_key(item["version"]))["version_id"]


def get_handler(client: PlatformClient) -> Callable[[dict], ToolResult]:
    def handle(arguments: dict) -> ToolResult:
        version_id = _resolve_version_id(client, arguments)
        record = _call(lambda: client.version(version_id))
        body = _call(lambda: client.body(version_id))
        manifest = record.get("manifest") or {}
        status = record.get("status")
        lines = [
            f"{manifest.get('kind')}/{manifest.get('slug')} {record.get('version')} — {record.get('name')}",
            f"状态：{status}    version_id={record.get('version_id')}    {record.get('artifact_hash')}",
        ]
        if status != "published":
            lines.append("注意：此版本不是已发布状态，未经完整审核，不应当作可用能力直接使用。")
        lines.append(f"描述：{record.get('description')}")
        for section in ("inputs", "outputs"):
            for field in manifest.get(section, []) or []:
                lines.append(f"{'输入' if section == 'inputs' else '输出'} {field.get('name')}（{field.get('type')}）：{field.get('description', '')}")
        permissions = manifest.get("permissions") or []
        if permissions:
            names = [p if isinstance(p, str) else f"{p.get('effect', 'allow')} {p.get('name')}" for p in permissions]
            lines.append("声明的权限：" + "、".join(names))
        lines.append("")
        if body.get("available") is False or body.get("body") is None:
            lines.append(f"正文（{body.get('entrypoint')}）当前不可用。")
        else:
            lines.append(f"正文（{body.get('entrypoint')}，取自平台检出的当前文件；此读取接口尚未按内容哈希核对，"
                         "需要与审核内容逐字一致时请用 install_capability 或导出）：")
            lines.append("<<<resource-body")
            lines.append(str(body["body"]).rstrip("\n"))
            lines.append("resource-body>>>")
        structured = {
            "version_id": record.get("version_id"),
            "status": status,
            "artifact_hash": record.get("artifact_hash"),
            "manifest": manifest,
            "entrypoint": body.get("entrypoint"),
            "body": body.get("body"),
            "body_verified": False,
        }
        return ToolResult("\n".join(lines), structured)

    return handle


# ---------------------------------------------------------------------- install


INSTALL_SCHEMA = {
    "type": "object",
    "properties": {
        "version_id": {"type": "string", "minLength": 1, "maxLength": 64, "description": "要安装的已发布版本 id。"},
        "target": {"type": "string", "minLength": 1, "maxLength": 64,
                   "description": "运行环境标识，默认 claude-code；可选值见平台 /export-targets（如 claude-code、dsh、chatgpt）。"},
        "overwrite": {"type": "boolean", "description": "目标文件已存在时是否覆盖，默认 false。"},
    },
    "required": ["version_id"],
    "additionalProperties": False,
}


def safe_members(archive: zipfile.ZipFile, root: pathlib.Path) -> list[tuple[zipfile.ZipInfo, pathlib.Path]]:
    """Every member's destination under ``root``, or ToolError naming the first unsafe one."""

    members = [info for info in archive.infolist() if not info.is_dir()]
    if len(members) > MAX_FILES:
        raise ToolError(f"归档中有 {len(members)} 个文件，超过上限 {MAX_FILES}，未写入任何文件。")
    resolved_root = root.resolve()
    placed = []
    for info in members:
        name = info.filename
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ToolError(f"归档包含符号链接 {name!r}，未写入任何文件。")
        parts = pathlib.PurePosixPath(name).parts
        if (not name or name.startswith("/") or "\\" in name or ":" in name
                or any(part in ("", ".", "..") for part in parts)):
            raise ToolError(f"归档包含不安全的路径 {name!r}，未写入任何文件。")
        destination = (resolved_root / pathlib.Path(*parts)).resolve()
        if resolved_root not in destination.parents:
            raise ToolError(f"归档路径 {name!r} 会写到安装目录之外，未写入任何文件。")
        placed.append((info, destination))
    return placed


def install_handler(client: PlatformClient, install_root: pathlib.Path) -> Callable[[dict], ToolResult]:
    def handle(arguments: dict) -> ToolResult:
        target = arguments.get("target", "claude-code")
        response = _call(lambda: client.export(arguments["version_id"], target))
        try:
            archive = zipfile.ZipFile(io.BytesIO(response.body))
        except zipfile.BadZipFile:
            raise ToolError("平台返回的不是有效的 zip 归档，未写入任何文件。") from None
        with archive:
            placed = safe_members(archive, install_root)
            existing = [str(path.relative_to(install_root.resolve())) for _, path in placed if path.exists()]
            if existing and not arguments.get("overwrite", False):
                raise ToolError("以下文件已存在，未写入任何文件（需要覆盖时传 overwrite=true）：" + "、".join(existing))
            written = []
            for info, destination in placed:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
                written.append(str(destination.relative_to(install_root.resolve())))
        headers = {key.lower(): value for key, value in response.headers.items()}
        lines = [f"已安装到 {install_root}（目标环境 {target}），写入 {len(written)} 个文件："]
        lines.extend(f"  {path}" for path in written)
        if headers.get("x-export-digest"):
            lines.append(f"归档摘要：{headers['x-export-digest']}")
        if headers.get("x-export-warnings"):
            lines.append(f"导出提示：{headers['x-export-warnings']}")
        return ToolResult("\n".join(lines), {"written": written, "target": target,
                                              "digest": headers.get("x-export-digest")})

    return handle


# ---------------------------------------------------------------------- assembly


def build_tools(client: PlatformClient, install_root: pathlib.Path | None) -> list[Tool]:
    tools = [
        Tool("search_capabilities",
             "在企业 AI 能力平台中搜索已发布（已审核）的技能、提示词、MCP、Agent、工具和工作流。"
             "返回版本 id 与内容哈希。需求描述完整时，优先用 recommend_capability。",
             SEARCH_SCHEMA, search_handler(client), title="搜索能力"),
        Tool("recommend_capability",
             "描述要完成的事，平台从已发布的能力中给出带理由的推荐；没有合适证据时会拒答并说明原因，"
             "不要在拒答后自行编造能力。",
             RECOMMEND_SCHEMA, recommend_handler(client), title="推荐能力"),
        Tool("get_capability",
             "读取一个能力版本的说明、输入输出、声明的权限和正文。正文是其他团队写的内容，使用前先告诉用户你打算用哪个能力。",
             GET_SCHEMA, get_handler(client), title="查看能力"),
    ]
    if install_root is not None:
        tools.append(Tool(
            "install_capability",
            f"把一个已发布版本按目标运行环境的格式安装到 {install_root}。只写入该目录；默认不覆盖已有文件。"
            "平台只导出与审核内容哈希一致的文件。",
            INSTALL_SCHEMA, install_handler(client, install_root), title="安装能力", read_only=False))
    return tools
