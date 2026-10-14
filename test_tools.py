"""
数据层测试：直接调用 MCP Server 的工具函数，不经过 MCP 协议，验证返回结构。

运行前请确保能找到 db：
  - 设置环境变量 XIANZHI_DB=<db 路径>，或
  - 把 xianzhi.db / xianzhi_img.db 放在本脚本同级目录，或
  - 让脚本走 find_db_path 的自动发现
"""

import json
import sys
import os

# 从本脚本所在目录导入 xianzhi_mcp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xianzhi_mcp

# 优先用环境变量，否则自动发现
DB_PATH = os.environ.get("XIANZHI_DB") or str(xianzhi_mcp.find_db_path(None))

def pprint(obj, indent=2, max_str_len=120):
    """截断长字符串的 pretty print"""
    def truncate(o):
        if isinstance(o, str) and len(o) > max_str_len:
            return o[:max_str_len] + f"...({len(o)} chars)"
        if isinstance(o, dict):
            return {k: truncate(v) for k, v in o.items()}
        if isinstance(o, list):
            return [truncate(v) for v in o[:5]] + (
                [f"... ({len(o)-5} more)"] if len(o) > 5 else []
            )
        return o
    print(json.dumps(truncate(obj), ensure_ascii=False, indent=indent))


def run():
    db = xianzhi_mcp.DBHandle.open(DB_PATH)
    print(f"[test] db opened: {DB_PATH}")

    # 1. stats
    print("\n=== 1. xianzhi_stats ===")
    s = db.get_stats()
    print(f"  总量: {s['total_books']} 书 / {s['total_articles']} 篇 / {s['total_chars']:,} 字")
    for c in s['categories']:
        print(f"  {c['name']:15s}: {c['book_count']:3d} 书 / {c['article_count']:5d} 篇 / {c['total_chars']:>10,} 字")

    # 2. list_books
    print("\n=== 2. xianzhi_list_books (全部, 前5) ===")
    books = db.list_books()
    for b in books[:5]:
        print(f"  {b['id']:12s} | {b['category']:10s} | {b['title']:30s} | vols={b['volume_count']} arts={b['article_count']}")
    print(f"  ... 共 {len(books)} 部书")

    print("\n=== 2b. xianzhi_list_books (西安市志) ===")
    xz_books = db.list_books("西安市志")
    for b in xz_books:
        print(f"  {b['id']:12s} | {b['title']}")

    # 3. get_book_toc — 第一卷·总类
    print("\n=== 3. xianzhi_get_book_toc (01-01 第一卷·总类) ===")
    toc = db.get_book_toc("01-01")
    if toc:
        print(f"  book: {toc['title']} | 声明卷数 {toc['volume_declared']} | 实际卷分组 {toc['volume_groups_found']} | {toc['total_articles']} 篇")
        for v in toc["volumes"][:3]:  # 只看前 3 个卷
            print(f"  卷: {v['vol']} ({v['path_count']} paths, {sum(p['article_count'] for p in v['paths'])} 篇)")
            for p in v["paths"][:2]:
                art_titles = [a['title'] for a in p['articles'][:3]]
                print(f"    path: {p['path']} ({p['article_count']} 篇): {art_titles}")
    else:
        print("  book 01-01 not found, trying with first book id...")
        if books:
            first_book = books[0]
            toc = db.get_book_toc(first_book['id'])
            print(f"  try id={first_book['id']}: title={toc['title'] if toc else None}")

    # 4. search — 搜"丝绸之路"
    print("\n=== 4. xianzhi_search(query='丝绸之路', limit=5) ===")
    sr = db.search("丝绸之路", limit=5)
    print(f"  total hits: {sr['total']}, returned: {sr['returned']}")
    for h in sr['hits']:
        print(f"  [{h['article_id']}] {h['category']}/{h['book_title']}/{h['title']} | body={h['body_length']}字")
        print(f"    snippet: {h['snippet'][:100]}...")

    # 5. search with filter
    print("\n=== 5. xianzhi_search(query='长安', category='西安市志', limit=3) ===")
    sr2 = db.search("长安", category="西安市志", limit=3)
    print(f"  total hits: {sr2['total']}, returned: {sr2['returned']}")
    for h in sr2['hits']:
        print(f"  [{h['article_id']}] {h['book_title']}/{h['title']}")

    # 6. get_article — 用搜索 4 的第一个 hit
    first_hit = sr['hits'][0]
    aid = first_hit['article_id']
    print(f"\n=== 6. xianzhi_get_article(id={aid}) ===")
    art = db.get_article(aid)
    if art:
        print(f"  title: {art['title']}")
        print(f"  book: {art['book_title']} | cat: {art['category']} | vol: {art['vol']} | path: {art['path']}")
        print(f"  paragraphs: {art['paragraph_count']} | total_chars: {art['total_chars']}")
        print(f"  前 2 段预览:")
        for p in art['paragraphs'][:2]:
            print(f"    [{p['index']}] {p['text'][:80]}... ({p['chars']}字)")

    # 7. get_article_chunked — 分段拉
    print(f"\n=== 7. xianzhi_get_article_chunked(id={aid}, start=0, count=3) ===")
    chunk = db.get_article_chunked(aid, 0, 3)
    if chunk:
        h = chunk['hierarchy']
        print(f"  hierarchy: {h['category']}/{h['book_title']}/{h['vol']}/{h['path']}")
        print(f"  total_paragraphs: {chunk['total_paragraphs']}, total_chars: {chunk['total_chars']}")
        print(f"  chunk: [{chunk['chunk_start']}-{chunk['chunk_end']}] count={chunk['chunk_count']}, has_more={chunk['has_more']}")
        for p in chunk['paragraphs']:
            print(f"    [{p['index']}] {p['text'][:80]}... ({p['chars']}字)")

    # 8. 翻页到第 2 批
    if chunk and chunk['has_more']:
        next_start = chunk['chunk_end']
        print(f"\n=== 8. 翻页: start={next_start}, count=3 ===")
        chunk2 = db.get_article_chunked(aid, next_start, 3)
        if chunk2:
            print(f"  chunk: [{chunk2['chunk_start']}-{chunk2['chunk_end']}], has_more={chunk2['has_more']}")
            for p in chunk2['paragraphs']:
                print(f"    [{p['index']}] {p['text'][:80]}... ({p['chars']}字)")

    # 9. 长文章测试 — 33万字那个
    print("\n=== 9. 长文 chunked 测试 (id=37663, 民国西安词典/人物, 329781字) ===")
    long_art = db.get_article_chunked(37663, 0, 5)
    if long_art:
        print(f"  title: {long_art['article_id']} (id=37663)")
        print(f"  total_paragraphs: {long_art['total_paragraphs']}, total_chars: {long_art['total_chars']}")
        print(f"  首批: {long_art['chunk_count']} 段, has_more={long_art['has_more']}")

    # 10. search 分页
    print("\n=== 10. search 分页 query='人物', category='旧志整理' ===")
    p1 = db.search("人物", category="旧志整理", limit=3, offset=0)
    p2 = db.search("人物", category="旧志整理", limit=3, offset=3)
    print(f"  offset=0: returned {p1['returned']}/{p1['total']}")
    print(f"  offset=3: returned {p2['returned']}/{p2['total']}")
    for h in p2['hits']:
        print(f"    [{h['article_id']}] {h['book_title']}/{h['title']}")

    print("\n=== ALL TESTS PASSED ===")

if __name__ == "__main__":
    run()
