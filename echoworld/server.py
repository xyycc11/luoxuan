"""本机入口：加载私有.env，可显式离线启动；不开放公网、不自动导入向量。"""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="EchoWorld local demo server")
    parser.add_argument("--port", type=int, default=8019)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--demo-seed", type=int, help="固定演示seed；不传时由服务端随机生成")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).parent / ".env")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    if args.offline:
        os.environ.update(ECHOWORLD_NPC_MODE="template", ECHOWORLD_NARRATION_MODE="template",
                          ECHOWORLD_GAME_AI_MODE="template", ECHOWORLD_REDIS_URL="", ECHOWORLD_MILVUS_URI="")
    if args.database:
        path = args.database.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["ECHOWORLD_DATABASE_URL"] = "sqlite+pysqlite:///" + path.as_posix()
    if args.demo_seed is not None:
        os.environ["ECHOWORLD_DEMO_SEED"] = str(args.demo_seed)
    import uvicorn
    uvicorn.run("echoworld.api:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
