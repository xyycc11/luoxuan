"""Day24：明确确认的私有偏好；SQL是权威，向量只返回候选ID/版本。"""
import json
import math
import os
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .embeddings import QwenEmbedding, validate_vector
from .vector_search import MilvusLoreIndex


STYLES = {"brief": "玩家偏好简洁提示。", "detailed": "玩家偏好详细提示。",
          "stepwise": "玩家偏好分步骤提示。"}


class PlayerMemory(Base):
    __tablename__ = "player_memories"
    __table_args__ = (UniqueConstraint("player_id", "world_id", "kind"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(80), index=True)
    world_id: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="guidance_style")
    style: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool]


def is_preference_question(message):
    return any(word in message for word in ("偏好", "提示风格", "引导风格"))


def validate_candidates(candidates, rows, player_id, world_id, now, *, threshold=0.4):
    """H5：只从授权SQL记录生成文本，绝不信索引携带的文本。"""
    hits, seen = [], set()
    for candidate in candidates:
        row = rows.get(candidate.get("id"))
        score = candidate.get("score")
        if row is None or row.player_id != player_id or row.world_id != world_id:
            continue
        if row.kind != "guidance_style" or not row.active or row.expires_at <= now or type(candidate.get("version")) is not int or candidate["version"] != row.version:
            continue
        if row.style not in STYLES or row.id in seen:
            continue
        if type(score) not in (int, float) or not math.isfinite(score) or not threshold <= score <= 1.00001:
            continue
        seen.add(row.id)
        hits.append({"id": row.id, "version": row.version, "source": "confirmed_preference",
                     "text": STYLES[row.style], "score": score})
    return sorted(hits, key=lambda row: (-row["score"], row["id"]))


@dataclass
class MemoryResult:
    status: str
    hits: list[dict] = field(default_factory=list)


class MilvusMemoryIndex(MilvusLoreIndex):
    def __init__(self, uri, dimension, token=""):
        # 专用集合与公开世界观集合分离；基类前缀限制仍生效。
        super().__init__(uri, dimension, collection="echoworld_lore_private_memory_v1", token=token)

    def search(self, query, player_id, world_id, embedding_key, k):
        validate_vector(query, self.dimension)
        expr = (f"player_id == {json.dumps(player_id)} and world_id == {json.dumps(world_id)} "
                f"and embedding_key == {json.dumps(embedding_key)}")
        result = self.client().search(collection_name=self.collection, data=[query], limit=k,
            filter=expr, output_fields=["version"], search_params={"metric_type": "COSINE"},
            consistency_level="Strong", timeout=3)
        return [{"id": hit["id"], "version": hit["entity"]["version"], "score": hit["distance"]}
                for hit in (result[0] if result else [])]


class LongMemory:
    def __init__(self, store, embedder=None, index=None, *, clock=time.time):
        self.store, self.embedder, self.index, self.clock = store, embedder, index, clock

    def confirm(self, game, style, *, consent, ttl_seconds=86400):
        if consent is not True or style not in STYLES or type(ttl_seconds) is not int or not 600 <= ttl_seconds <= 2592000:
            raise ValueError("偏好、明确同意或有效期不合法")
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.scalar(select(PlayerMemory).where(PlayerMemory.player_id == game.player_id,
                PlayerMemory.world_id == game.world_id, PlayerMemory.kind == "guidance_style"))
            if row is None:
                row = PlayerMemory(id=uuid.uuid4().hex, player_id=game.player_id, world_id=game.world_id,
                                   style=style, version=1, active=True, expires_at=0)
                db.add(row)
            else:
                row.version += 1
            row.style, row.active, row.expires_at = style, True, int(self.clock()) + ttl_seconds
            result = {"id": row.id, "version": row.version, "index_status": "pending_explicit_ingest"}
        return result  # 成功只表示SQL保存，不冒充向量已更新。

    def revoke(self, game, memory_id):
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.scalar(select(PlayerMemory).where(PlayerMemory.id == memory_id,
                PlayerMemory.player_id == game.player_id, PlayerMemory.world_id == game.world_id))
            if row is None:
                return False
            row.active, row.version = False, row.version + 1
        return True

    def ingest(self, game):
        """显式导入已确认记录；无模型调用期间的数据库长事务。"""
        if self.embedder is None or self.index is None:
            raise ValueError("长期记忆向量服务未配置")
        with self.store.lock, self.store.sessions.begin() as db:
            rows = list(db.scalars(select(PlayerMemory).where(PlayerMemory.player_id == game.player_id,
                PlayerMemory.world_id == game.world_id, PlayerMemory.active.is_(True),
                PlayerMemory.expires_at > int(self.clock()))))
        vectors = self.embedder.embed([STYLES[row.style] for row in rows]) if rows else []
        if len(vectors) != len(rows):
            raise ValueError("向量批次长度不匹配")
        records = [{"id": row.id, "player_id": row.player_id, "world_id": row.world_id,
                    "version": row.version, "embedding_key": self.embedder.embedding_key,
                    "vector": validate_vector(vector, self.embedder.dimension)}
                   for row, vector in zip(rows, vectors)]
        if records:
            self.index.upsert(records)
        return len(records)

    def retrieve(self, message, game, *, k=3):
        if type(k) is not int or not 1 <= k <= 5:
            raise ValueError("k范围为1～5")
        if self.embedder is None or self.index is None:
            return MemoryResult("disabled")
        try:
            vector = self.embedder.embed([message])[0]
            validate_vector(vector, self.embedder.dimension)
        except Exception:
            return MemoryResult("embedding_error")
        try:
            candidates = self.index.search(vector, game.player_id, game.world_id, self.embedder.embedding_key, k)
        except Exception:
            return MemoryResult("index_error")
        # SQL异常不当成“记忆没命中”；候选不携带被展示的权威文本。
        with self.store.lock, self.store.sessions.begin() as db:
            ids = [hit.get("id") for hit in candidates if isinstance(hit.get("id"), str)]
            rows = {row.id: row for row in db.scalars(select(PlayerMemory).where(
                PlayerMemory.id.in_(ids), PlayerMemory.player_id == game.player_id,
                PlayerMemory.world_id == game.world_id))}
            hits = validate_candidates(candidates, rows, game.player_id, game.world_id, int(self.clock()))
        return MemoryResult("ok" if hits else "no_match", hits[:k])

    def refresh(self, hits, game):
        # 模型执行后再核验撤回/版本/到期；不保证之后的并发撤回也被即时观察。
        with self.store.lock, self.store.sessions.begin() as db:
            rows = {row.id: row for row in db.scalars(select(PlayerMemory).where(
                PlayerMemory.id.in_([hit["id"] for hit in hits]), PlayerMemory.player_id == game.player_id,
                PlayerMemory.world_id == game.world_id))}
            return validate_candidates(hits, rows, game.player_id, game.world_id, int(self.clock()))

    @classmethod
    def from_environment(cls, store):
        uri = os.getenv("ECHOWORLD_MILVUS_URI")
        embedder = QwenEmbedding.from_environment() if uri else None
        return cls(store, embedder, MilvusMemoryIndex(uri, embedder.dimension,
            token=os.getenv("ECHOWORLD_MILVUS_TOKEN", "")) if embedder else None)
