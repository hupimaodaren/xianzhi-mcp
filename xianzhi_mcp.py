"""
西安地方志知识库 MCP Server
============================
为各种 Agent/Harness 提供结构化的地方志全文检索接口。

数据库：sqlite3 直接读取 xianzhi.db / xianzhi_img.db
路径层级：分类(cat) → 书名(book.title) → 卷次(vol) → path(/) → 篇目(arts)
段落分隔：\\n\\n 分割的段落作为自然分块
检索：全库子串匹配（一期不加 FTS5）

库格式（自动探测，双格式兼容）：
  v1：body 直存 arts 表（v0.3.2 及更早安装包内置）
  v2：正文按篇对齐拼接成 256KB 块、整块 zlib-9 压缩存于 blocks 表；
      arts 只存位置（blk/off/nb）；meta.format_version=2（轻量化版内置）。
      正文由本模块解压后使用，检索语义与 v1 保持一致。

自动发现 db 路径（优先级与桌面端一致）：
  1. --db 命令行参数 或 XIANZHI_DB 环境变量（显式指定文件）
  2. XIANZHI_HOME/data 下
  3. 可执行文件（或脚本）同级目录
  4. 可执行文件所在目录的 app/resources/data / resources/data / data
  5. %APPDATA%/xianzhi/data（EXE 安装版数据根）
  6. 当前工作目录
  7. 已知便携版解压路径模板

db 选择：优先 xianzhi_img.db（图文版信息更全），否则 xianzhi.db

运行（打包后的 exe）：
    xianzhi-mcp.exe                          # 自动发现 db，stdio 模式
    xianzhi-mcp.exe --db D:/apps/xianzhi.db  # 显式指定
    xianzhi-mcp.exe --transport sse          # SSE 模式，浏览器可访问

运行（源码）：
    python xianzhi_mcp.py
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# MCP SDK (v2.x)
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    print(
        "ERROR: MCP SDK not installed. Run: pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# 数据层：薄封装 sqlite3，向上暴露 Python 原生 dict
# ---------------------------------------------------------------------------

CATEGORY_ORDER = [
    "西安市志", "区县志鉴", "专业志·部门志", "西安年鉴", "地情丛书", "旧志整理",
]

MAX_SEARCH_HITS = 500          # 单次检索上限
DEFAULT_SEARCH_LIMIT = 50      # 默认返回数
DEFAULT_ARTICLE_CHUNK_SIZE = 10  # 默认每次返回的段落数


@dataclass
class DBHandle:
    conn: sqlite3.Connection

    @classmethod
    def open(cls, path: str) -> "DBHandle":
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA read_only = 1")
        h = cls(conn)
        h.format = h._detect_format()
        h._blocks: "OrderedDict[int, bytes]" = OrderedDict()
        h._chars: dict[int, int] | None = None
        return h

    # -- 库格式（v1 body 直存 / v2 blocks zlib 压缩）----------------------

    def _detect_format(self) -> int:
        """meta.format_version=2 为压缩格式；无 meta 表（查询报错）按 v1。"""
        try:
            r = self.conn.execute(
                "SELECT v FROM meta WHERE k = 'format_version'").fetchone()
        except sqlite3.OperationalError:
            return 1
        if r is None:
            return 1
        try:
            return int(r[0])
        except (TypeError, ValueError):
            return 1

    def _block(self, blk: int) -> bytes:
        """解压正文块，保留最近 8 块（每块解压后约 256KB）。"""
        cached = self._blocks.get(blk)
        if cached is not None:
            self._blocks.move_to_end(blk)
            return cached
        r = self.conn.execute(
            "SELECT data FROM blocks WHERE blk = ?", (blk,)).fetchone()
        if r is None:
            raise KeyError(f"正文块缺失 blk={blk}")
        raw = zlib.decompress(r[0])
        self._blocks[blk] = raw
        while len(self._blocks) > 8:
            self._blocks.popitem(last=False)
        return raw

    def _v2_body(self, blk: int, off: int, nb: int) -> str:
        return self._block(blk)[off:off + nb].decode("utf-8")

    def get_body_text(self, article_id: int) -> str:
        """单篇正文文本（双格式）。"""
        if self.format == 1:
            r = self.conn.execute(
                "SELECT body FROM arts WHERE id = ?", (article_id,)).fetchone()
            return (r[0] if r and r[0] else "") if r else ""
        r = self.conn.execute(
            "SELECT blk, off, nb FROM arts WHERE id = ?", (article_id,)).fetchone()
        if r is None:
            return ""
        return self._v2_body(r[0], r[1], r[2])

    def _ensure_chars(self) -> dict[int, int]:
        """v2：每篇字符数（与 v1 LENGTH(body) 语义一致），全量解压一遍后缓存。"""
        if self._chars is None:
            chars: dict[int, int] = {}
            for aid, blk, off, nb in self.conn.execute(
                    "SELECT id, blk, off, nb FROM arts ORDER BY id"):
                chars[aid] = len(self._v2_body(blk, off, nb))
            self._chars = chars
        return self._chars

    def _body_chars_expr_sql(self) -> str:
        """v1 用于 SQL 聚合的字符数表达式（v2 不可用，须走 _ensure_chars）。"""
        return "LENGTH(a.body)"

    # -- 基础查询 --------------------------------------------------------

    def list_categories(self) -> list[dict[str, Any]]:
        """返回 6 大分类及统计。"""
        if self.format == 2:
            chars = self._ensure_chars()
            cur = self.conn.execute(
                """
                SELECT b.cat, b.id,
                       (SELECT COUNT(*) FROM arts a WHERE a.book = b.id) AS article_count
                FROM books b
                """
            )
            agg: dict[str, dict[str, int]] = {}
            for r in cur.fetchall():
                a = agg.setdefault(r["cat"], {"book_count": 0, "article_count": 0, "total_chars": 0})
                a["book_count"] += 1
                a["article_count"] += r["article_count"]
            # 聚合每书字符数（chars 缓存已含全部篇目）
            book_chars: dict[str, int] = {}
            for aid, book in self.conn.execute("SELECT id, book FROM arts"):
                book_chars[book] = book_chars.get(book, 0) + chars.get(aid, 0)
            for r in self.conn.execute("SELECT cat, id FROM books"):
                if r["id"] in book_chars:
                    agg[r["cat"]]["total_chars"] += book_chars[r["id"]]
            out = []
            for cat in CATEGORY_ORDER:
                if cat in agg:
                    a = agg[cat]
                    out.append({"name": cat, **a})
            for cat, a in agg.items():
                if cat not in CATEGORY_ORDER:
                    out.append({"name": cat, **a})
            return out
        cur = self.conn.execute(
            """
            SELECT b.cat,
                   COUNT(DISTINCT b.id) AS book_count,
                   COUNT(a.id)          AS article_count,
                   SUM(LENGTH(a.body))  AS total_chars
            FROM books b
            LEFT JOIN arts a ON a.book = b.id
            GROUP BY b.cat
            """
        )
        stats = {r["cat"]: r for r in cur.fetchall()}
        out = []
        for cat in CATEGORY_ORDER:
            if cat in stats:
                s = stats[cat]
                out.append({
                    "name": cat,
                    "book_count": s["book_count"],
                    "article_count": s["article_count"] or 0,
                    "total_chars": s["total_chars"] or 0,
                })
        # 兜底：不在 CATEGORY_ORDER 里的分类也追加
        for cat, s in stats.items():
            if cat not in CATEGORY_ORDER:
                out.append({
                    "name": cat,
                    "book_count": s["book_count"],
                    "article_count": s["article_count"] or 0,
                    "total_chars": s["total_chars"] or 0,
                })
        return out

    def list_books(self, category: str | None = None) -> list[dict[str, Any]]:
        """列出书目，可按分类过滤。"""
        if self.format == 2:
            chars = self._ensure_chars()
            book_chars: dict[str, int] = {}
            for aid, book in self.conn.execute("SELECT id, book FROM arts"):
                book_chars[book] = book_chars.get(book, 0) + chars.get(aid, 0)
            sql = """
                SELECT b.id, b.title, b.cat, b.n,
                       (SELECT COUNT(*) FROM arts a WHERE a.book = b.id) AS article_count
                FROM books b
            """
            params: list[Any] = []
            if category:
                sql += " WHERE b.cat = ?"
                params.append(category)
            sql += " GROUP BY b.id ORDER BY b.seq, b.id"
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "category": r["cat"],
                    "volume_count": r["n"],
                    "article_count": r["article_count"],
                    "total_chars": book_chars.get(r["id"], 0),
                }
                for r in self.conn.execute(sql, params)
            ]
        sql = """
            SELECT b.id, b.title, b.cat, b.n,
                   COUNT(a.id)         AS article_count,
                   SUM(LENGTH(a.body)) AS total_chars
            FROM books b
            LEFT JOIN arts a ON a.book = b.id
        """
        params: list[Any] = []
        if category:
            sql += " WHERE b.cat = ?"
            params.append(category)
        sql += " GROUP BY b.id ORDER BY b.seq, b.id"
        cur = self.conn.execute(sql, params)
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "category": r["cat"],
                "volume_count": r["n"],
                "article_count": r["article_count"] or 0,
                "total_chars": r["total_chars"] or 0,
            }
            for r in cur.fetchall()
        ]

    def get_book_toc(self, book_id: str) -> dict[str, Any] | None:
        """某书卷次结构（vol + path 两级目录）。"""
        cur = self.conn.execute(
            """
            SELECT id, title, cat, n FROM books WHERE id = ?
            """,
            (book_id,),
        )
        book = cur.fetchone()
        if not book:
            return None

        cur = self.conn.execute(
            """
            SELECT vol, path, title, id
            FROM arts
            WHERE book = ?
            ORDER BY id
            """,
            (book_id,),
        )
        arts = cur.fetchall()

        # 按 vol → path 两级分组
        toc: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for a in arts:
            vol = a["vol"] or "（无卷次）"
            path = a["path"] or "（无层级）"
            toc.setdefault(vol, {}).setdefault(path, []).append({
                "id": a["id"],
                "title": a["title"],
            })

        # 转换为有序列表
        volumes = []
        for vol, paths in toc.items():
            path_list = []
            for p, articles in paths.items():
                path_list.append({
                    "path": p,
                    "article_count": len(articles),
                    "articles": articles,
                })
            volumes.append({
                "vol": vol,
                "path_count": len(path_list),
                "paths": path_list,
            })

        return {
            "book_id": book["id"],
            "title": book["title"],
            "category": book["cat"],
            "volume_declared": book["n"],
            "volume_groups_found": len(volumes),
            "total_articles": len(arts),
            "volumes": volumes,
        }

    def search(
        self,
        query: str,
        category: str | None = None,
        book_id: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """全文检索：标题命中优先 → body 命中。"""
        if not query or limit <= 0:
            return {"total": 0, "query": query, "hits": []}

        if self.format == 2:
            return self._search_v2(query, category, book_id, limit, offset)

        where_parts = [
            "(a.title LIKE ? OR a.body LIKE ?)",
        ]
        like = f"%{query}%"

        # COUNT 查询参数（只有 WHERE 里的 ?）
        params_count: list[Any] = [like, like]
        # SELECT 查询参数：WHERE 全部参数 + ORDER BY LIKE + LIMIT + OFFSET
        params_select: list[Any] = [like, like]

        if category:
            where_parts.append("b.cat = ?")
            params_count.append(category)
            params_select.append(category)
        if book_id:
            where_parts.append("a.book = ?")
            params_count.append(book_id)
            params_select.append(book_id)

        where = " AND ".join(where_parts)

        cur = self.conn.execute(
            f"""
            SELECT COUNT(*) FROM arts a
            JOIN books b ON b.id = a.book
            WHERE {where}
            """,
            params_count,
        )
        total = cur.fetchone()[0]

        # SELECT: WHERE ? 全部填完后，加 ORDER BY 的 LIKE，再加 LIMIT/OFFSET
        params_select.append(like)       # ORDER BY (a.title LIKE ?)
        params_select.extend([limit, offset])
        cur = self.conn.execute(
            f"""
            SELECT a.id, a.title, a.book, a.vol, a.path, b.title AS book_title,
                   b.cat, a.body
            FROM arts a
            JOIN books b ON b.id = a.book
            WHERE {where}
            ORDER BY (a.title LIKE ?) DESC, a.id
            LIMIT ? OFFSET ?
            """,
            params_select,
        )

        hits = []
        for r in cur.fetchall():
            body = r["body"] or ""
            snippet = _build_snippet(body, query)
            hits.append({
                "article_id": r["id"],
                "title": r["title"],
                "vol": r["vol"],
                "path": r["path"],
                "book_id": r["book"],
                "book_title": r["book_title"],
                "category": r["cat"],
                "body_length": len(body),
                "snippet": snippet,
            })

        return {
            "total": total,
            "returned": len(hits),
            "offset": offset,
            "query": query,
            "hits": hits,
        }

    def _search_v2(
        self,
        query: str,
        category: str | None,
        book_id: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        """v2 压缩库检索：SQL 取元数据，Python 解压块后子串匹配。

        语义与 v1 的 LIKE 检索保持一致：ASCII 大小写不敏感、标题命中
        优先、同组内按 id 升序；片段仍由 _build_snippet 生成。
        """
        q = query.lower()
        title_hits: list[sqlite3.Row] = []
        body_hits: list[sqlite3.Row] = []
        sql = """
            SELECT a.id, a.title, a.book, a.vol, a.path, b.title AS book_title,
                   b.cat, a.blk, a.off, a.nb
            FROM arts a
            JOIN books b ON b.id = a.book
            ORDER BY a.id
        """
        for r in self.conn.execute(sql):
            if category and r["cat"] != category:
                continue
            if book_id and r["book"] != book_id:
                continue
            if q in (r["title"] or "").lower():
                title_hits.append(r)
                continue
            body = self._v2_body(r["blk"], r["off"], r["nb"])
            if q in body.lower():
                body_hits.append(r)
        matches = title_hits + body_hits
        total = len(matches)
        hits = []
        for r in matches[offset:offset + limit]:
            body = self._v2_body(r["blk"], r["off"], r["nb"])
            hits.append({
                "article_id": r["id"],
                "title": r["title"],
                "vol": r["vol"],
                "path": r["path"],
                "book_id": r["book"],
                "book_title": r["book_title"],
                "category": r["cat"],
                "body_length": len(body),
                "snippet": _build_snippet(body, query),
            })
        return {
            "total": total,
            "returned": len(hits),
            "offset": offset,
            "query": query,
            "hits": hits,
        }

    def get_article(self, article_id: int) -> dict[str, Any] | None:
        """获取完整文章。"""
        if self.format == 2:
            cur = self.conn.execute(
                """
                SELECT a.id, a.title, a.book, b.title AS book_title, b.cat,
                       a.vol, a.path, a.date, a.blk, a.off, a.nb
                FROM arts a JOIN books b ON b.id = a.book
                WHERE a.id = ?
                """,
                (article_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            body = self._v2_body(r["blk"], r["off"], r["nb"])
            return self._article_payload(r, body)
        cur = self.conn.execute(
            """
            SELECT a.id, a.title, a.book, b.title AS book_title, b.cat,
                   a.vol, a.path, a.date, a.body
            FROM arts a JOIN books b ON b.id = a.book
            WHERE a.id = ?
            """,
            (article_id,),
        )
        r = cur.fetchone()
        if not r:
            return None
        body = r["body"] or ""
        return self._article_payload(r, body)

    def _article_payload(self, r: sqlite3.Row, body: str) -> dict[str, Any]:
        paragraphs = _split_paragraphs(body)
        return {
            "article_id": r["id"],
            "title": r["title"],
            "book_id": r["book"],
            "book_title": r["book_title"],
            "category": r["cat"],
            "vol": r["vol"],
            "path": r["path"],
            "date": r["date"],
            "full_text": body,
            "paragraph_count": len(paragraphs),
            "total_chars": len(body),
            "paragraphs": paragraphs,
        }

    def get_article_chunked(
        self,
        article_id: int,
        start_paragraph: int = 0,
        paragraph_count: int = DEFAULT_ARTICLE_CHUNK_SIZE,
    ) -> dict[str, Any] | None:
        """分段拉取文章，用于长文按需获取。"""
        if self.format == 2:
            cur = self.conn.execute(
                """
                SELECT a.id, a.title, a.book, b.title AS book_title, b.cat,
                       a.vol, a.path, a.date, a.blk, a.off, a.nb
                FROM arts a JOIN books b ON b.id = a.book
                WHERE a.id = ?
                """,
                (article_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            body = self._v2_body(r["blk"], r["off"], r["nb"])
        else:
            cur = self.conn.execute(
                """
                SELECT a.id, a.title, a.book, b.title AS book_title, b.cat,
                       a.vol, a.path, a.date, a.body
                FROM arts a JOIN books b ON b.id = a.book
                WHERE a.id = ?
                """,
                (article_id,),
            )
            r = cur.fetchone()
            if not r:
                return None
            body = r["body"] or ""

        paragraphs = _split_paragraphs(body)
        total = len(paragraphs)
        start = max(0, min(start_paragraph, total))
        end = min(start + max(1, paragraph_count), total)
        chunk = paragraphs[start:end]

        return {
            "article_id": r["id"],
            "title": r["title"],
            "hierarchy": {
                "category": r["cat"],
                "book_id": r["book"],
                "book_title": r["book_title"],
                "vol": r["vol"],
                "path": r["path"],
            },
            "total_paragraphs": total,
            "total_chars": len(body),
            "chunk_start": start,
            "chunk_end": end,
            "chunk_count": end - start,
            "has_more": end < total,
            "paragraphs": chunk,
        }

    def get_stats(self) -> dict[str, Any]:
        """全库统计。"""
        if self.format == 2:
            chars = self._ensure_chars()
            n_articles = self.conn.execute("SELECT COUNT(*) FROM arts").fetchone()[0]
            return {
                "total_books": self.conn.execute(
                    "SELECT COUNT(*) FROM books").fetchone()[0],
                "total_articles": n_articles,
                "total_chars": sum(chars.get(aid, 0)
                                   for aid, in self.conn.execute("SELECT id FROM arts")),
                "categories": self.list_categories(),
                "category_order": CATEGORY_ORDER,
                "db_format": 2,
            }
        cur = self.conn.execute(
            """
            SELECT COUNT(DISTINCT b.id)  AS books,
                   COUNT(a.id)           AS articles,
                   SUM(LENGTH(a.body))   AS total_chars
            FROM books b
            LEFT JOIN arts a ON a.book = b.id
            """
        )
        r = cur.fetchone()
        return {
            "total_books": r["books"],
            "total_articles": r["articles"] or 0,
            "total_chars": r["total_chars"] or 0,
            "categories": self.list_categories(),
            "category_order": CATEGORY_ORDER,
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _split_paragraphs(body: str) -> list[dict[str, Any]]:
    """按 \\n\\n 分割段落，每段带索引和长度。"""
    raw = body.split("\n\n")
    out = []
    idx = 0
    for p in raw:
        stripped = p.strip()
        if stripped:
            out.append({
                "index": idx,
                "text": stripped,
                "chars": len(stripped),
            })
            idx += 1
    return out


def _build_snippet(body: str, query: str, window: int = 60) -> str:
    """截取包含 query 的上下文片段（简单版，不高亮）。"""
    if not query:
        return body[:window * 2]
    pos = body.find(query)
    if pos < 0:
        return body[:window * 2]
    start = max(0, pos - window)
    end = min(len(body), pos + len(query) + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return prefix + body[start:end] + suffix


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = MCPServer(
    name="xianzhi-knowledge-base",
    version="0.1.0",
    description="西安地方志全文知识库 MCP Server，覆盖 81 部书 / 38978 篇 / 约 6670 万字",
)

_db: DBHandle | None = None


def _get_db() -> DBHandle:
    global _db
    if _db is None:
        raise RuntimeError("Database not initialized. Did you set XIANZHI_DB?")
    return _db


# --- 工具定义 --------------------------------------------------------------

@mcp.tool()
def xianzhi_stats() -> dict[str, Any]:
    """获取全库统计：书/篇/字数总量、六大分类分布、分类排序。

    Agent 调用建议：在探索知识库时先调用此工具了解全貌。
    """
    return _get_db().get_stats()


@mcp.tool()
def xianzhi_list_categories() -> list[dict[str, Any]]:
    """列出六大分类及其书目数、篇数、总字数。

    Agent 调用建议：导航起点，选定分类后用 xianzhi_list_books。
    """
    return _get_db().list_categories()


@mcp.tool()
def xianzhi_list_books(category: str | None = None) -> list[dict[str, Any]]:
    """列出书目。

    Args:
        category: 可选，分类名称（需与 xianzhi_list_categories 返回的 name 一致）。
                  传入则只返回该分类下的书。
    """
    return _get_db().list_books(category)


@mcp.tool()
def xianzhi_get_book_toc(book_id: str) -> dict[str, Any]:
    """获取某书的目录结构（卷次 vol → path 层级 → 篇目列表）。

    Args:
        book_id: 书 ID，从 xianzhi_list_books 返回的 id 字段获取。

    Agent 调用建议：拿到结构后，从 path 下选文章 ID 去读全文或做搜索。
    """
    result = _get_db().get_book_toc(book_id)
    if result is None:
        return {"error": f"book '{book_id}' not found"}
    return result


@mcp.tool()
def xianzhi_search(
    query: str,
    category: str | None = None,
    book_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """全文检索（SQLite instr，不区分大小写）。

    Args:
        query: 搜索关键词（不支持通配符、不支持分词，原串匹配）。
        category: 可选，限定在某分类内搜索。
        book_id: 可选，限定在某书内搜索。
        limit: 返回条数上限，默认 50，单次最多 500。
        offset: 跳过前 N 条，用于分页。

    Returns:
        {
            "total": 命中总数,
            "returned": 本次返回数,
            "offset": 本次偏移,
            "query": 原查询,
            "hits": [
                {article_id, article_title, vol, path, book_id, book_title,
                 category, body_length, snippet}
            ]
        }

    Agent 调用建议：
        - 先用小 limit(20) 看结果概况，再按需加大或翻页。
        - 命中后，用 article_id 调 xianzhi_get_article / xianzhi_get_article_chunked 读正文。
    """
    if limit > MAX_SEARCH_HITS:
        limit = MAX_SEARCH_HITS
    if limit <= 0:
        limit = DEFAULT_SEARCH_LIMIT
    return _get_db().search(query, category, book_id, limit, offset)


@mcp.tool()
def xianzhi_get_article(article_id: int) -> dict[str, Any]:
    """获取完整文章正文。

    Args:
        article_id: 文章 ID（数字），从搜索或目录中获取。

    Returns:
        {
            article_id, title, book_id, book_title, category, vol, path, date,
            full_text: str, paragraph_count, total_chars,
            paragraphs: [{index, text, chars}, ...]  # 按 \\n\\n 分割
        }

    注意：长文（如 33 万字）返回的 full_text 很大。
    Agent 调用建议：超长文章改用 xianzhi_get_article_chunked 分段拉取。
    """
    result = _get_db().get_article(article_id)
    if result is None:
        return {"error": f"article {article_id} not found"}
    return result


@mcp.tool()
def xianzhi_get_article_chunked(
    article_id: int,
    start_paragraph: int = 0,
    paragraph_count: int = 10,
) -> dict[str, Any]:
    """分段获取文章正文，适合长文按需拉取。

    Args:
        article_id: 文章 ID。
        start_paragraph: 起始段落索引（从 0 开始）。
        paragraph_count: 本次返回的段落数，默认 10。

    Returns:
        {
            article_id, title, hierarchy: {category, book_id, book_title, vol, path},
            total_paragraphs, total_chars,
            chunk_start, chunk_end, chunk_count, has_more,
            paragraphs: [{index, text, chars}, ...]
        }

    Agent 调用建议：
        - 返回的 paragraphs 有完整 index，has_more 为 True 时继续用下一段索引调本工具。
        - 用 hierarchy 还原文章完整路径。
    """
    result = _get_db().get_article_chunked(article_id, start_paragraph, paragraph_count)
    if result is None:
        return {"error": f"article {article_id} not found"}
    return result


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

TXT_DB = "xianzhi.db"
IMG_DB = "xianzhi_img.db"


def _exe_dir() -> Path:
    """PyInstaller 打包后 sys.frozen=True，sys.executable 是 exe 路径。
    源码运行时 sys.argv[0] 是脚本路径。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def _home_data_dirs() -> list[Path]:
    """与 DesktopDataDeployer 一致的用户数据根目录候选。"""
    dirs: list[Path] = []
    # 1. XIANZHI_HOME 环境变量（对应 Java -Dxianzhi.home）
    home_override = os.environ.get("XIANZHI_HOME")
    if home_override:
        dirs.append(Path(home_override) / "data")

    # 2. Windows: %APPDATA%\xianzhi\data
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "xianzhi" / "data")
    # 3. 跨平台兜底: ~/.xianzhi/data
    dirs.append(Path.home() / ".xianzhi" / "data")
    return dirs


def _packed_data_dirs(exe_root: Path) -> list[Path]:
    """便携版/安装版 app-image 周边候选（与 DesktopDataDeployer.findPackedDataDir 对齐）。"""
    return [
        exe_root / "app" / "resources" / "data",
        exe_root / "resources" / "data",
        exe_root / "data",
    ]


def _find_db_file(directory: Path) -> Path | None:
    """在某目录内找 db，优先图文版。"""
    img = directory / IMG_DB
    txt = directory / TXT_DB
    if img.is_file():
        return img
    if txt.is_file():
        return txt
    return None


def find_db_path(explicit_arg: str | None) -> Path:
    """按优先级自动发现 xianzhi.db / xianzhi_img.db。

    优先级与 DesktopDataDeployer 完全对齐：
      1. 命令行 --db / XIANZHI_DB（显式指定文件）
      2. XIANZHI_HOME/data
      3. 可执行文件（或脚本）同级目录
      4. 可执行文件所在目录的 app/resources/data / resources/data / data
      5. %APPDATA%/xianzhi/data
      6. 当前工作目录
    """
    # --- 优先级 1: 显式指定 ---
    if explicit_arg:
        p = Path(explicit_arg).expanduser()
        if p.is_file():
            return p.resolve()
        # 用户可能给的是目录
        if p.is_dir():
            found = _find_db_file(p)
            if found:
                return found
        raise FileNotFoundError(f"指定的 db 未找到: {explicit_arg}")

    env_db = os.environ.get("XIANZHI_DB")
    if env_db:
        p = Path(env_db).expanduser()
        if p.is_file():
            return p.resolve()
        if p.is_dir():
            found = _find_db_file(p)
            if found:
                return found

    # --- 收集候选目录 ---
    exe_root = _exe_dir()
    candidates_dirs: list[Path] = []

    # XIANZHI_HOME/data
    candidates_dirs.extend(_home_data_dirs())
    # exe / script 同级目录（用户可能把 exe 拖进了便携版解压目录旁边）
    candidates_dirs.append(exe_root)
    # exe 周边的打包资源目录
    candidates_dirs.extend(_packed_data_dirs(exe_root))
    # 当前工作目录
    candidates_dirs.append(Path.cwd())

    # --- 遍历 ---
    tried: list[str] = []
    for d in candidates_dirs:
        tried.append(str(d))
        if not d.is_dir():
            continue
        found = _find_db_file(d)
        if found:
            return found

    # --- 都没找到 ---
    msg_lines = [
        "ERROR: 未找到 xianzhi.db 或 xianzhi_img.db",
        "",
        "已搜索以下位置：",
    ]
    for t in tried:
        msg_lines.append(f"  - {t}")
    msg_lines.extend([
        "",
        "快速解决方法（任选其一）：",
        "  1) 把 xianzhi.db 放到本 exe 同级目录",
        "  2) 把 xianzhi.db 放到便携版解压目录下（app/resources/data/ 或根目录）",
        "  3) 设置环境变量 XIANZHI_DB=<db 完整路径> 再启动",
        "  4) 加 --db <path> 参数",
    ])
    raise FileNotFoundError("\n".join(msg_lines))


def main():
    # --- Windows 控制台 UTF-8 修复 ---
    # PyInstaller 打包后 stdout/stderr 默认 cp936，会把中文输出成 mojibake
    # 在 stdio 模式下 MCP 客户端（Agent）通过管道收数据，管道默认编码可能也不对
    # 强制 reconfigure 到 utf-8，避免乱码
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # 管道场景可能 reconfigure 失败，静默忽略

    parser = argparse.ArgumentParser(
        description="西安地方志 MCP Server（自动发现本地 xianzhi.db）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  xianzhi-mcp.exe                          # 自动发现 db，stdio 模式（默认）
  xianzhi-mcp.exe --db D:/apps/xianzhi.db  # 显式指定 db
  xianzhi-mcp.exe --transport sse          # SSE 模式，浏览器可访问
  XIANZHI_DB=D:/xxx/xianzhi.img.db xianzhi-mcp.exe
        """.strip(),
    )
    parser.add_argument(
        "--db",
        default=None,
        help="db 文件或目录路径（不传则自动发现）",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="MCP 传输协议（默认 stdio，SSE 可被浏览器/远程 agent 访问）",
    )
    args = parser.parse_args()

    try:
        db_path = find_db_path(args.db)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    global _db
    _db = DBHandle.open(str(db_path))

    edition = "图文版" if db_path.name == IMG_DB else "文字版"
    print(
        f"[xianzhi-mcp] db={db_path} ({edition})  transport={args.transport}",
        file=sys.stderr,
    )
    stats = _db.get_stats()
    print(
        f"[xianzhi-mcp] {stats['total_books']} 部书 / "
        f"{stats['total_articles']} 篇 / {stats['total_chars']:,} 字",
        file=sys.stderr,
    )

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
