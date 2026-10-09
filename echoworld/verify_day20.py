"""可选真实Redis验收：临时SQL、随机测试作用域、短TTL，不改玩家存档。"""
import os
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from .chat_memory import ChatMemory, ChatScope, RedisRecentCache
from .database import SaveStore


def main():
    if not os.getenv("ECHOWORLD_REDIS_URL"):
        raise SystemExit("请先在本地配置ECHOWORLD_REDIS_URL；不要把密码写入源码。")
    cache = RedisRecentCache.from_environment()
    store = None
    try:
        assert cache.client.ping()
        scope = ChatScope("verify-" + uuid.uuid4().hex, "temporary-world")
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            # Windows必须先释放SQLite句柄再清理临时目录；异常时也按此顺序。
            stack.callback(lambda: store.close() if store is not None else None)
            url = "sqlite+pysqlite:///" + (Path(directory) / "verify.sqlite3").as_posix()
            store = SaveStore(url)
            memory = ChatMemory(store, cache, ttl=2)
            memory.append_turn(scope, "我叫旅行者", "收到")
            hit = memory.load_recent(scope)
            assert hit["source"] == "cache" and len(hit["turns"]) == 1
            ttl = cache.client.ttl(memory.cache_key(scope))
            assert 0 <= ttl <= 2
            other = ChatScope(scope.player_id, scope.world_id, "another-session")
            assert memory.load_recent(other)["turns"] == []
            time.sleep(2.1)
            assert cache.client.get(memory.cache_key(scope)) is None
            recovered = memory.load_recent(scope)
            assert recovered["source"] == "sql" and recovered["turns"] == hit["turns"]
            store.close()
            store = SaveStore(url)
            assert ChatMemory(store).load_recent(scope)["turns"] == hit["turns"]
            # 最后刷新短TTL；测试键自行到期，不扫描/删除其他Redis数据。
            cache.client.expire(memory.cache_key(scope), 2)
            print("Redis PING: OK")
            print("Cache hit / TTL / session isolation: OK")
            print("Real expiry -> SQL fallback: OK")
            print("SQL store recreation -> chat recovery: OK")
            print("Paid model calls: 0; user save files modified: 0")
    finally:
        if store is not None:
            store.close()
        cache.close()


if __name__ == "__main__":
    main()
