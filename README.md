# 西安地方志 MCP Server

[![Release](https://img.shields.io/github/v/release/hupimaodaren/xianzhi-mcp)](https://github.com/hupimaodaren/xianzhi-mcp/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

把西安地方志 App 自带的 **6670 万字**全文知识库接入你的 Agent（Trae / Claude Desktop / Cursor / Windsurf 等）。

**全离线运行，不需要联网，不需要 API Key。**

> 本工具只提供查询接口，**不包含、不再分发任何志书数据**。数据来自 [西安地方志 App](https://github.com/hupimaodaren/xianzhi-app) 安装后本机已有的 `xianzhi.db`。

---

## 3 步上手

### 第 1 步：拿到 exe

在 [Releases](https://github.com/hupimaodaren/xianzhi-mcp/releases) 下载 `xianzhi-mcp.exe`（单文件，约 31 MB，**无需安装 Python**）。

### 第 2 步：放对位置

把 `xianzhi-mcp.exe` 放到能看到 `xianzhi.db` 的地方，exe 会**自动发现**数据库：

| 安装方式 | 放哪里最省事 |
|---------|-------------|
| **便携版**（解压即用） | 放到便携版解压目录的**根目录**，与 `xianzhi.exe` 同级 |
| **EXE 安装版** | 放哪里都行，exe 会自动读 `%APPDATA%\xianzhi\data\` |

自动发现优先级（找不到才往下走）：

1. `--db` 参数 / `XIANZHI_DB` 环境变量指定的文件或目录
2. exe 同级目录
3. exe 所在目录下的 `app/resources/data/`、`resources/data/`、`data/`
4. `XIANZHI_HOME/data/`（对应 App 的 `-Dxianzhi.home`）
5. `%APPDATA%\xianzhi\data\`
6. 当前工作目录

> **优先图文版**：目录里同时有 `xianzhi_img.db` 和 `xianzhi.db` 时，自动选图文版（信息更全）。
> 想强制指定，用 `--db` 或 `XIANZHI_DB`。

### 第 3 步：接入你的 Agent

#### Trae IDE

写入 `.trae/mcp.json`（项目根目录）或 Trae 设置 → MCP Servers：

```json
{
  "mcpServers": {
    "xianzhi-knowledge-base": {
      "command": "D:/xianzhi-app/xianzhi-mcp.exe",
      "args": []
    }
  }
}
```

#### Claude Desktop

写入 `%APPDATA%/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "xianzhi-knowledge-base": {
      "command": "D:/xianzhi-app/xianzhi-mcp.exe",
      "args": []
    }
  }
}
```

#### Cursor

Settings → MCP Tools → Add New MCP Server：
- **Type**: `command`
- **Command**: `D:/xianzhi-app/xianzhi-mcp.exe`

#### Windsurf / Codeium

Settings → MCP Servers → Add Server：

```json
{
  "xianzhi-knowledge-base": {
    "type": "stdio",
    "command": "D:/xianzhi-app/xianzhi-mcp.exe",
    "args": []
  }
}
```

> 把 `D:/xianzhi-app/` 换成你自己的 exe 路径（Windows 路径建议用正斜杠 `/` 或双反斜杠 `\\`）。
> 更多模板见 [`mcp-config-examples.json`](mcp-config-examples.json)。

重启 Agent，即可调用地方志知识库。

---

## Agent 能用什么

| 工具 | 作用 |
|------|------|
| `xianzhi_stats` | 知识库全貌（几本书、多少篇、总字数） |
| `xianzhi_list_categories` | 六大分类（西安市志 / 区县志鉴 / 专业志·部门志 / 西安年鉴 / 地情丛书 / 旧志整理） |
| `xianzhi_list_books` | 列出书目，可按分类过滤 |
| `xianzhi_get_book_toc` | 某书目录树（卷次 → path → 篇目） |
| `xianzhi_search` | 全文检索（关键词，精确子串匹配，标题命中优先） |
| `xianzhi_get_article` | 完整文章正文（含分段） |
| `xianzhi_get_article_chunked` | 分段拉取长文（默认 10 段/次，按需继续拉） |

**推荐调用链**：

```
xianzhi_stats                                    了解全貌
  → xianzhi_search("丝绸之路", category="西安市志")  找资料
    → xianzhi_get_article_chunked(id, 0, 10)       读前 10 段
      → xianzhi_get_article_chunked(id, 10, 10)    按需续读
```

所有结果都带**完整层级路径**（`category → book_title → vol → path`），Agent 能自行判断是否需要继续下钻。

---

## 常见问题

**Q: Agent 报找不到 db？**
把 exe 放到便携版解压目录根目录旁，或设置 `XIANZHI_DB=D:/你的路径/xianzhi_img.db`。

**Q: 搜索结果太少？**
`xianzhi_search` 是精确子串匹配（不分大小写、不分词）。换个关键词，比如搜「长安」而非「唐代长安」。

**Q: 需要联网吗？**
不需要。全离线，直接读本机 `xianzhi.db`。

**Q: 支持哪些数据库？**
`xianzhi.db`（文字版）与 `xianzhi_img.db`（图文版）均可，自动优先图文版。

**Q: 只读安全吗？**
数据库以 `PRAGMA read_only = 1` 打开，**绝不会写入或修改**你的志书库。

---

## 开发者

### 从源码运行

```bash
pip install -r requirements.txt
python xianzhi_mcp.py                        # 自动发现 db
python xianzhi_mcp.py --db D:/apps/xianzhi.db
python xianzhi_mcp.py --transport sse        # SSE 模式
```

### 打包单文件 exe

```bash
pip install pyinstaller typer
python build.py
# 产物：dist/xianzhi-mcp.exe（约 31 MB）
```

### 测试

```bash
# 数据层 + 自动发现（需先设置 XIANZHI_DB 或把 db 放在自动发现路径下）
python test_tools.py

# 完整 MCP 协议端到端（启动 exe 子进程，走 stdio 协议）
python test_mcp_client.py
```

### 命令行选项

```
xianzhi-mcp.exe                              # 自动发现 db，stdio 模式（默认）
xianzhi-mcp.exe --db D:/apps/xianzhi.db      # 显式指定 db 文件
xianzhi-mcp.exe --db D:/apps/data            # 显式指定目录（目录内优先图文版）
xianzhi-mcp.exe --transport sse              # SSE 模式，被远程 Agent 访问
XIANZHI_DB=D:/xxx/xianzhi_img.db xianzhi-mcp.exe
```

---

## 技术说明

- **数据库**：SQLite（`books` + `arts` 两表），`sqlite3` 标准库直读，无第三方依赖
- **检索**：`LIKE` 全表扫描（38978 篇约 1–3 秒）；标题命中排在正文命中之前
- **分块**：正文按 `\n\n` 分割为段落，长文用 `xianzhi_get_article_chunked` 按需拉取
- **打包**：PyInstaller `--onefile`，内置 Python 运行时，单文件分发
- **MCP SDK**：`mcp >= 2.3.0`（`mcp.server.mcpserver.MCPServer`）

## 相关项目

- [xianzhi-app](https://github.com/hupimaodaren/xianzhi-app) — 西安地方志全文离线 App（Android + Windows）

## 许可

代码采用 [MIT License](LICENSE)，Copyright (c) 2026 西安地名协会。
志书正文与配图版权归西安市地方志办公室所有。
