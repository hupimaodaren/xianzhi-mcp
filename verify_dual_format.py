# -*- coding: utf-8 -*-
"""xianzhi-mcp 双格式端到端验证：v2 压缩库 vs v1 旧库。"""
import sys, time, json

sys.path.insert(0, r"D:\xianzhi-mcp")
from xianzhi_mcp import DBHandle

V2 = r"D:\西安地方志App开发\data\xianzhi.db"
V1 = r"D:\西安地方志App开发\app\build\backup_v1\xianzhi-v1.db"

t0 = time.perf_counter()
v2 = DBHandle.open(V2)
v1 = DBHandle.open(V1)
print("format: v2=%d v1=%d  open=%.2fs" % (v2.format, v1.format, time.perf_counter() - t0))

# stats
t0 = time.perf_counter()
s2 = v2.get_stats()
t_stats = time.perf_counter() - t0
s1 = v1.get_stats()
print("stats books/articles: v2=%s/%s v1=%s/%s" % (
    s2["total_books"], s2["total_articles"], s1["total_books"], s1["total_articles"]))
print("total_chars: v2=%d v1=%d (char-parity=%s)" % (
    s2["total_chars"], s1["total_chars"], s2["total_chars"] == s1["total_chars"]))
print("v2 stats(含首建 chars 缓存) %.2fs, 二次 %.2fs" % (t_stats, (time.perf_counter() - t0) if False else 0))
t0 = time.perf_counter(); v2.get_stats(); print("v2 stats 二次调用 %.2fs" % (time.perf_counter() - t0))

# categories 对比
c2 = {c["name"]: c for c in v2.list_categories()}
c1 = {c["name"]: c for c in v1.list_categories()}
ok = all(c2[k] == c1[k] for k in c1) and set(c2) == set(c1)
print("categories 全等:", ok)
if not ok:
    for k in c1:
        if c2.get(k) != c1[k]:
            print("  DIFF", k, c2.get(k), "!=", c1[k])

# books 对比
b2 = v2.list_books(); b1 = v1.list_books()
print("books 全等:", b2 == b1, "(%d 部)" % len(b2))

# toc 对比（抽 2 本）
for bid in [b2[0]["id"], b2[40]["id"]]:
    t2 = v2.get_book_toc(bid); t1 = v1.get_book_toc(bid)
    print("toc %s 全等: %s" % (bid, t2 == t1))

# search 对比（大小写不敏感语义）
for q, kw2 in [("西安", "西安"), ("XI'AN", "xi'an"), ("大雁塔", "大雁塔"), ("不存在词xyz", "不存在词xyz")]:
    t0 = time.perf_counter()
    r2 = v2.search(q, limit=50)
    dt = time.perf_counter() - t0
    r1 = v1.search(q, limit=50)
    same_ids = [h["article_id"] for h in r2["hits"]] == [h["article_id"] for h in r1["hits"]]
    print("search %-8r total v2=%-6d v1=%-6d hits50全等=%s  v2耗时 %.2fs" % (
        q, r2["total"], r1["total"], same_ids, dt))

# offset 分页一致性
p0 = v2.search("大雁塔", limit=10, offset=0)
p1 = v2.search("大雁塔", limit=10, offset=10)
full = v2.search("大雁塔", limit=50)
print("分页拼接一致:", [h["article_id"] for h in p0["hits"] + p1["hits"]] ==
      [h["article_id"] for h in full["hits"][:20]])

# category/book 过滤
r_c = v2.search("西安", category="西安年鉴", limit=20)
r_b = v2.search("西安", book_id=b2[0]["id"], limit=20)
print("category 过滤: total=%d 全为年鉴=%s" % (
    r_c["total"], all(h["category"] == "西安年鉴" for h in r_c["hits"])))
print("book 过滤: total=%d 全为同书=%s" % (
    r_b["total"], all(h["book_id"] == b2[0]["id"] for h in r_b["hits"])))

# article 对比（抽 3 篇）
import random
random.seed(7)
ids = random.sample(range(1, 38978), 3)
for aid in ids:
    a2 = v2.get_article(aid); a1 = v1.get_article(aid)
    same = a2 == a1
    print("article %d 全等: %s (chars=%s)" % (aid, same, a2["total_chars"] if a2 else None))
    if not same and a2 and a1:
        for k in a1:
            if a2[k] != a1[k]:
                print("   DIFF", k)

# chunked 对比
ch2 = v2.get_article_chunked(ids[0], 2, 5)
ch1 = v1.get_article_chunked(ids[0], 2, 5)
print("chunked 全等:", ch2 == ch1)

# get_body_text
print("get_body_text 一致:", v2.get_body_text(ids[0]) == v1.get_body_text(ids[0]))

# 空库边界
print("空 query:", v2.search("")["total"], "limit=0:", v2.search("西安", limit=0)["total"])
print("不存在文章:", v2.get_article(99999999))
print("ALL DONE")
