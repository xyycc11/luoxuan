"""Day20：SQL存完整对话轮次，Redis只缓存最近几轮；不是图checkpoint。"""
import hashlib
import json
import os
from dataclasses import dataclass

from sqlalchemy import Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, SaveStore


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    user_message: Mapped[str] = mapped_column(Text)
    assistant_reply: Mapped[str] = mapped_column(Text)


@dataclass(frozen=True)
class ChatScope:
    player_id: str
    world_id: str
    session_id: str = "main"
    npc_id: str = "guard"

    @property
    def key(self) -> str:
        # 身份来自认证/归属核验；序列化元组避免冒号拼接歧义。
        payload = json.dumps([self.player_id, self.world_id, self.session_id, self.npc_id])
        return hashlib.sha256(payload.encode()).hexdigest()


class RedisRecentCache:
    """同步API使用同步客户端；只转换Redis故障，不吞SQL或业务错误。"""
    def __init__(self, client):
        self.client = client

    @classmethod
    def from_environment(cls):
        url = os.getenv("ECHOWORLD_REDIS_URL")
        if not url:
            return None  # 默认仅SQL，不偷偷连接服务。
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        return cls(Redis.from_url(url, decode_responses=True,
                                  socket_connect_timeout=0.3, socket_timeout=0.3,
                                  retry=Retry(NoBackoff(), 0)))

    def get(self, key):
        from redis.exceptions import RedisError
        try:
            return self.client.get(key)
        except RedisError as error:
            raise OSError("Redis读取失败") from error

    def set(self, key, value, *, ex):
        from redis.exceptions import RedisError
        try:
            return self.client.set(key, value, ex=ex)
        except RedisError as error:
            raise OSError("Redis写入失败") from error

    def close(self):
        self.client.close()


class ChatMemory:
    def __init__(self, store: SaveStore, cache=None, *, recent_turns: int = 6, ttl: int = 3600):
        if recent_turns < 1 or ttl < 1:
            raise ValueError("记忆窗口和TTL必须为正数")
        self.store, self.cache = store, cache
        self.recent_turns, self.ttl = recent_turns, ttl

    def cache_key(self, scope: ChatScope) -> str:
        return "echoworld:chat:v1:" + scope.key

    def _version(self, db, scope):
        return db.scalar(select(func.max(ChatTurn.id)).where(ChatTurn.scope_key == scope.key)) or 0

    def _decode(self, raw, scope, version):
        if not isinstance(raw, str) or len(raw) > 100_000:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("scope") != scope.key:
            return None
        if type(payload.get("version")) is not int or payload["version"] != version:
            return None
        turns = payload.get("turns")
        if not isinstance(turns, list) or len(turns) > self.recent_turns:
            return None
        for turn in turns:
            if not isinstance(turn, dict) or set(turn) != {"user", "assistant"}:
                return None
            if any(not isinstance(value, str) or len(value) > 6000 for value in turn.values()):
                return None
        return turns

    def _write_cache(self, scope, version, turns):
        if self.cache is None:
            return "disabled"
        payload = json.dumps({"scope": scope.key, "version": version, "turns": turns}, ensure_ascii=False)
        try:
            self.cache.set(self.cache_key(scope), payload, ex=self.ttl)
            return "ready"
        except OSError:
            return "write_error"  # SQL已经成功；缓存失败不能反向撤销SQL。

    def load_recent(self, scope: ChatScope) -> dict:
        # 先核验SQL版本：较旧缓存的晚到写入不会被下次请求当成最新记录。
        # 为易读牺牲一次小SQL查询；不是分布式CAS，也不是零SQL的缓存命中。
        with self.store.lock, self.store.sessions.begin() as db:
            version = self._version(db, scope)
        cache_status = "disabled" if self.cache is None else "miss"
        if self.cache is not None:
            try:
                turns = self._decode(self.cache.get(self.cache_key(scope)), scope, version)
                if turns is not None:
                    return {"turns": turns, "revision": version, "source": "cache", "cache_status": "ready"}
            except OSError:
                cache_status = "read_error"
        # Redis访问不放在SQL事务里；未命中、过期、损坏或故障都读SQL。
        with self.store.lock, self.store.sessions.begin() as db:
            version = self._version(db, scope)
            rows = db.scalars(select(ChatTurn).where(ChatTurn.scope_key == scope.key)
                              .order_by(ChatTurn.id.desc()).limit(self.recent_turns)).all()
            turns = [{"user": row.user_message, "assistant": row.assistant_reply} for row in reversed(rows)]
        write_status = self._write_cache(scope, version, turns)
        if cache_status != "read_error":
            cache_status = write_status
        return {"turns": turns, "revision": version, "source": "sql", "cache_status": cache_status}

    def append_turn(self, scope: ChatScope, user_message: str, assistant_reply: str, *, db=None) -> dict:
        if any(not isinstance(value, str) or not value or len(value) > 6000
               for value in (user_message, assistant_reply)):
            raise ValueError("对话必须为非空、受限长度文本")
        if db is not None:
            db.add(ChatTurn(scope_key=scope.key, user_message=user_message, assistant_reply=assistant_reply))
            db.flush()
            return {"cache_status": "deferred" if self.cache else "disabled"}
        with self.store.lock, self.store.sessions.begin() as db:
            db.add(ChatTurn(scope_key=scope.key, user_message=user_message, assistant_reply=assistant_reply))
        # 完整user/assistant轮次一起提交；网络调用在提交之后。
        return self.load_recent(scope)
