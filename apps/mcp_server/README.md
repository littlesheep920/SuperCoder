# SuperCoder MCP Server

让 Claude Code、Cursor 等编码 Agent 在工作中直接查询平台：搜索和推荐已发布的能力、读取说明与正文，并且（可选）把能力安装进当前项目。

只依赖 Python 标准库，不需要安装后端依赖。平台 API 必须已经在运行，本机路径或 `docker compose` 都可以。

## 1. 提供的工具

| 工具 | 做什么 | 写文件 |
|---|---|---|
| `search_capabilities` | 关键词与语义混合检索，只返回已发布版本，附版本 id 与内容哈希 | 否 |
| `recommend_capability` | 描述需求，平台给出带理由的推荐；没有证据时拒答，并列出已检查的候选 | 否 |
| `get_capability` | 按版本 id，或按资源 id 加版本号，读取描述、输入输出、权限与正文；未发布版本会明确标注 | 否 |
| `install_capability` | 按目标运行环境的格式，把已发布版本写入安装目录 | 是，仅在设置了 `SUPERCODER_MCP_INSTALL_DIR` 时提供 |

## 2. 配置

| 环境变量 | 含义 |
|---|---|
| `SUPERCODER_API_URL` | API 地址，含 `/v1`。默认 `http://localhost:8000/v1` |
| `SUPERCODER_API_TOKEN` | Agent 以哪个身份访问平台。开发环境用 `scripts/dev_tokens.json` 中的令牌，例如 `dev-owner-token` |
| `SUPERCODER_MCP_INSTALL_DIR` | 可选。设置后才提供 `install_capability`，且只写入这个目录，通常设为项目根目录 |
| `SUPERCODER_MCP_TIMEOUT` | 可选。单次 API 调用的超时秒数，默认 20 |

## 3. 接入 Claude Code

在项目目录中执行（路径换成你的仓库位置）：

```bash
claude mcp add supercoder \
  --env SUPERCODER_API_URL=http://localhost:8000/v1 \
  --env SUPERCODER_API_TOKEN=dev-owner-token \
  --env SUPERCODER_MCP_INSTALL_DIR="$PWD" \
  -- python /path/to/SuperCoder/apps/mcp_server/server.py
```

不同版本的 Claude Code 命令行参数可能略有差别，以 `claude mcp add --help` 为准。

也可以写进项目根目录的 `.mcp.json`，与团队共享：

```json
{
  "mcpServers": {
    "supercoder": {
      "command": "python",
      "args": ["/path/to/SuperCoder/apps/mcp_server/server.py"],
      "env": {
        "SUPERCODER_API_URL": "http://localhost:8000/v1",
        "SUPERCODER_API_TOKEN": "dev-owner-token"
      }
    }
  }
}
```

安装到 `claude-code` 目标时，文件写在安装目录下的 `.claude/skills/<slug>/`，Claude Code 会自动识别。

## 4. 接入 Cursor

在项目的 `.cursor/mcp.json` 中写入与上面相同的 `mcpServers` 配置。

## 5. 安全边界

- **只读为默认。** 不设置安装目录时，服务器不提供任何写文件的工具。
- **只写安装目录。** 归档中的绝对路径、`..`、反斜杠、盘符和符号链接一律拒绝。拒绝时一个文件都不写，已存在的文件默认不覆盖。
- **只装审过的内容。** 安装经过导出接口：只有已发布版本可以导出，并且检出中的文件必须与版本记录的内容哈希一致（`docs/ADAPTERS.md` §5）。
- **正文读取未核对哈希。** `get_capability` 读取正文用的 `GET /resource-versions/{id}/body` 返回的是检出中的当前文件，平台尚未按内容哈希核对，工具输出中会写明这一点。
- **令牌不回显。** 令牌只放在请求头里，不出现在任何输出或日志中。日志只写 stderr，stdout 只有协议消息，而且全部是 ASCII，不受 Windows 控制台代码页影响。

## 6. 验证

```bash
python -m unittest tests.test_mcp_server
```

测试会在本机起一个按冻结契约应答的 HTTP 服务，再以子进程方式经真实 stdio 完成一次会话。**接入真实的 Claude Code 或 Cursor、连接真实 API 的验证尚未进行。**
