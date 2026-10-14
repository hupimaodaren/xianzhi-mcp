"""
端到端验证：用 mcp SDK 的 client 启动 xianzhi-mcp.exe 子进程，走完整 MCP 协议。

默认用 dist/xianzhi-mcp.exe；也可通过环境变量 XIANZHI_MCP_EXE 指定别的 exe。
db 通过环境变量 XIANZHI_DB 传入（不传则 exe 走自动发现）。
"""
import asyncio, sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.environ.get("XIANZHI_MCP_EXE") or os.path.join(HERE, "dist", "xianzhi-mcp.exe")
DB = os.environ.get("XIANZHI_DB")  # 可为 None，交给 exe 自动发现

async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if DB:
        env["XIANZHI_DB"] = DB

    server_params = StdioServerParameters(
        command=EXE,
        args=[],
        env=env,
    )

    print(f"启动: {EXE}")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # list tools
            result = await session.list_tools()
            tools = list(result.tools)
            print(f"\n✅ 发现 {len(tools)} 个工具:")
            for t in tools:
                print(f"   - {t.name}")
            assert len(tools) == 7

            # call stats
            print("\n✅ xianzhi_stats:")
            result = await session.call_tool("xianzhi_stats")
            stats = json.loads(result.content[0].text)
            print(f"   {stats['total_books']} 书 / {stats['total_articles']} 篇 / {stats['total_chars']:,} 字")
            assert stats["total_articles"] == 38978

            # call search
            print("\n✅ xianzhi_search('丝绸之路'):")
            result = await session.call_tool("xianzhi_search", {"query": "丝绸之路", "limit": 3})
            search = json.loads(result.content[0].text)
            print(f"   total={search['total']}, returned={search['returned']}")
            for h in search["hits"][:2]:
                print(f"   [{h['article_id']}] {h['book_title']}/{h['title']}")

            # call chunked
            hit_id = search["hits"][0]["article_id"]
            print(f"\n✅ xianzhi_get_article_chunked(id={hit_id}):")
            result = await session.call_tool("xianzhi_get_article_chunked", {
                "article_id": hit_id, "start_paragraph": 0, "paragraph_count": 3
            })
            chunk = json.loads(result.content[0].text)
            print(f"   hierarchy: {chunk['hierarchy']['category']}/{chunk['hierarchy']['book_title']}")
            print(f"   {chunk['chunk_count']} 段, total={chunk['total_paragraphs']}, has_more={chunk['has_more']}")
            print(f"   首段: {chunk['paragraphs'][0]['text'][:80]}...")

            print("\n🎉 全部 MCP 协议端到端测试通过！")

asyncio.run(main())
