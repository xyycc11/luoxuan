"""受信本地管理员入口；不向玩家HTTP/模型工具开放审批权限。"""
import argparse
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .database import SaveStore
from .shared_knowledge import SharedKnowledge
from .action_store import RequestConflict


def main():
    parser = argparse.ArgumentParser(description="EchoWorld受控FAQ审核，不自动训练模型")
    parser.add_argument("--database", type=Path, required=True, help="现有SQLite存档，与server --database一致")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).parent / ".env")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="受信审核者查看待审建议；不要截图公开原文")
    commands.add_parser("report", help="审核审计与反馈计数，不代表质量提升")
    commands.add_parser("ingest", help="显式调用千问、更新专用公共FAQ Milvus集合")
    for name in ("approve", "reject", "disable", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("id")
        command.add_argument("--reviewer", required=True)
        command.add_argument("--note", required=True)
        if name == "approve":
            command.add_argument("--ttl", type=int, default=2592000)
        if name == "rollback":
            command.add_argument("--version", type=int, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    if not path.is_file():
        parser.error("指定现有存档文件；不隐式建立新库或覆盖旧库")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(game_saves)")}
        if not {"world_id", "player_id", "game", "combat"} <= columns:
            parser.error("不是EchoWorld存档，拒绝初始化或迁移其他数据库")
    except sqlite3.Error:
        parser.error("无法只读核验EchoWorld存档")
    store = SaveStore("sqlite+pysqlite:///" + path.as_posix())
    knowledge = SharedKnowledge(store)
    try:
        if args.command == "list":
            result = knowledge.candidates()
        elif args.command == "report":
            result = knowledge.report()
        elif args.command in ("approve", "reject"):
            result = knowledge.review(args.id, approve=args.command == "approve", actor=args.reviewer,
                                      note=args.note, ttl_seconds=getattr(args, "ttl", 2592000))
        elif args.command == "disable":
            result = knowledge.disable(args.id, actor=args.reviewer, note=args.note)
        elif args.command == "rollback":
            result = knowledge.rollback(args.id, args.version, actor=args.reviewer, note=args.note)
        else:
            from dotenv import load_dotenv
            load_dotenv(args.env_file, override=False)
            # 唯有显式ingest允许外部调用；审核/list/report完全离线。
            if not os.getenv("ECHOWORLD_MILVUS_URI"):
                raise ValueError("向量配置缺失")
            knowledge = SharedKnowledge.from_environment(store)
            result = {"indexed": knowledge.ingest()}
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except (RequestConflict, ValueError) as error:
        print(json.dumps({"ok": False, "code": error.code if isinstance(error, RequestConflict)
                          else "INVALID_REVIEW_OR_CONFIG"}))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "code": "KNOWLEDGE_OPERATION_FAILED"}))
        return 2
    finally:
        knowledge.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
