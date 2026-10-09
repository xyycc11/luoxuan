"""真实MCP stdio适配；每进程绑定一个可信玩家/世界，仅提供两个只读工具。"""
import argparse
import asyncio
import json

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .agent_steps import TOOL_SCHEMAS
from .database import GameSave, unpack
from .tools import execute_tool


class ScopedReader:
    def __init__(self, database_url: str, player_id: str, world_id: str):
        self.engine = create_engine(database_url)  # 不create_all、不seed、不写业务状态。
        self.sessions = sessionmaker(self.engine)
        self.player_id, self.world_id = player_id, world_id

    def call(self, name: str, arguments: dict) -> dict:
        with self.sessions() as db:
            row = db.get(GameSave, self.world_id)
            if row is None or row.player_id != self.player_id:
                return {"ok": False, "data": None, "error": {
                    "code": "FORBIDDEN", "message": "无法访问绑定存档"}}
            game, _ = unpack(row)
        return execute_tool(game, name, arguments)

    def close(self):
        self.engine.dispose()


def build_server(reader: ScopedReader) -> Server:
    server = Server("EchoWorld-readonly")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=item["function"]["name"],
                           description=item["function"]["description"],
                           inputSchema=item["function"]["parameters"],
                           annotations=types.ToolAnnotations(readOnlyHint=True))
                for item in TOOL_SCHEMAS]

    # 参数校验由原execute_tool完成，保持统一错误，不因SDK自动报错丢掉结构。
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            result = await asyncio.to_thread(reader.call, name, arguments)
        except Exception:
            result = {"ok": False, "data": None, "error": {
                "code": "READ_FAILED", "message": "存档读取失败"}}
        return types.CallToolResult(content=[types.TextContent(
            type="text", text=json.dumps(result, ensure_ascii=False))],
            structuredContent=result, isError=not result["ok"])

    return server


async def serve(reader: ScopedReader):
    server = build_server(reader)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description="仅本机可信Host启动的只读MCP服务")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--world", required=True)
    args = parser.parse_args()
    reader = ScopedReader(args.database_url, args.player, args.world)
    try:
        asyncio.run(serve(reader))
    finally:
        reader.close()


if __name__ == "__main__":
    main()
