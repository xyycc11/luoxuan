"""显式资料导入/配置诊断。导入会调用千问并写项目集合，不在聊天时自动执行。"""
import argparse
import json
import os
from pathlib import Path


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ingest-public", action="store_true")
    group.add_argument("--ingest-private", choices=["alice", "bob"])
    args = parser.parse_args()
    configured = {
        "deepseek_key_present": bool(os.getenv("ECHOWORLD_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")),
        "qwen_key_present": bool(os.getenv("ECHOWORLD_QWEN_EMBEDDING_KEY") or os.getenv("DASHSCOPE_API_KEY")),
        "redis_url_present": bool(os.getenv("ECHOWORLD_REDIS_URL")),
        "milvus_uri_present": bool(os.getenv("ECHOWORLD_MILVUS_URI")),
    }
    if not (args.ingest_public or args.ingest_private):
        print(json.dumps({"mode": "presence_only_not_connection_test", **configured}))
        return 0
    if not (configured["qwen_key_present"] and configured["milvus_uri_present"]):
        print(json.dumps({"ok": False, "code": "MISSING_QWEN_OR_MILVUS_CONFIG"}))
        return 2
    resource, store = None, None
    try:
        if args.ingest_public:
            from .lore_rag import LoreRag
            resource = LoreRag.from_environment()
            count = resource.ingest()
        else:
            from .database import GameSave, SaveStore, unpack
            from .long_memory import LongMemory
            from .api import DEFAULT_DATABASE_URL
            Path(__file__).parent.joinpath("data").mkdir(exist_ok=True)
            store = SaveStore(os.getenv("ECHOWORLD_DATABASE_URL", DEFAULT_DATABASE_URL))
            resource = LongMemory.from_environment(store)
            with store.sessions.begin() as db:
                game, _ = unpack(db.get(GameSave, "world-" + args.ingest_private))
            count = resource.ingest(game)
        print(json.dumps({"ok": True, "code": "INGEST_COMPLETED", "result": count}, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({"ok": False, "code": "INGEST_FAILED_CHECK_CONFIG_AND_SERVICES"}))
        return 2
    finally:
        if resource is not None:
            if args.ingest_public:
                resource.close()
            elif resource.index is not None:
                resource.index.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
