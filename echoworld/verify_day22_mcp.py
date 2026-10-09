"""真实stdio协议验证；仅临时SQL和本地子进程，不调用模型或用户数据库。"""
import asyncio
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .database import GameSave, SaveStore, unpack


async def verify(database_url: str):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "echoworld.mcp_server", "--database-url", database_url,
              "--player", "alice", "--world", "world-alice"],
        env={"PYTHONIOENCODING": "utf-8"}, cwd=str(Path(__file__).parent.parent))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"check_permit", "get_inventory"}
            results = []
            for name, arguments, expected in (
                ("check_permit", {}, False),
                ("get_inventory", {"item_name": "herb"}, 1),
                ("get_inventory", {"item_name": "missing"}, 0),
            ):
                call = await session.call_tool(name, arguments)
                data = call.structuredContent
                assert not call.isError and data["ok"] and data["data"] == expected
                results.append({"tool": name, "data": expected})
            for name, arguments in (
                ("check_permit", {"player_id": "bob"}),
                ("get_inventory", {"item_name": ""}),
                ("get_inventory", {"item_name": 2}),
                ("submit_herb_for_permit", {}),
            ):
                call = await session.call_tool(name, arguments)
                assert call.isError and call.structuredContent["ok"] is False
            return {"protocol": "real MCP stdio", "tools": results, "rejected_calls": 4}


def main():
    with tempfile.TemporaryDirectory(prefix="echoworld-day22-mcp-") as directory:
        url = "sqlite+pysqlite:///" + (Path(directory) / "test.sqlite3").as_posix()
        store = SaveStore(url)
        try:
            result = asyncio.run(verify(url))
            with store.sessions() as db:
                game, _ = unpack(db.get(GameSave, "world-alice"))
                assert game.has_permit is False and game.inventory == {"herb": 1}
            print(json.dumps({**result, "state_unchanged": True}, ensure_ascii=False))
        finally:
            store.close()


if __name__ == "__main__":
    main()
