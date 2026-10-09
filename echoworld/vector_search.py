"""教学精确搜索与实际Milvus适配分开，不将内存索引冒充Milvus。"""
import json
import math
import re
from threading import Lock

from .embeddings import validate_vector


def cosine(left, right):
    if not left or len(left) != len(right):
        raise ValueError("向量必须非空且维度相同")
    validate_vector(left, len(left))
    validate_vector(right, len(right))
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    if not math.isfinite(left_norm) or not math.isfinite(right_norm):
        raise ValueError("向量模长超出数值范围")
    score = sum((a / left_norm) * (b / right_norm) for a, b in zip(left, right))
    return max(-1.0, min(1.0, score))


def top_k(query, records, k):
    if type(k) is not int or k < 0:
        raise ValueError("k必须是非负整数")
    if k == 0:
        return []
    scores = [{**row, "score": cosine(query, row["vector"])} for row in records]
    return sorted(scores, key=lambda row: (-row["score"], row["id"]))[:k]


class MemoryVectorIndex:
    """仅离线测试/教学用，使用精确余弦搜索，无持久化。"""
    def __init__(self):
        self.records = {}

    def upsert(self, records):
        self.records.update({row["id"]: dict(row) for row in records})

    def search(self, query, world_id, embedding_key, k):
        rows = [row for row in self.records.values()
                if row["world_id"] == world_id and row["embedding_key"] == embedding_key]
        return top_k(query, rows, k)

    def close(self):
        pass


class MilvusLoreIndex:
    def __init__(self, uri, dimension, *, collection="echoworld_lore_qwen_v4_256_v1", token=""):
        if not re.fullmatch(r"echoworld_lore_[A-Za-z0-9_]+", collection):
            raise ValueError("仅使用项目专用collection")
        self.uri, self.dimension, self.collection, self.token = uri, dimension, collection, token
        self._client = None
        self._lock = Lock()

    def client(self):
        with self._lock:
            if self._client is None:
                from pymilvus import MilvusClient
                self._client = MilvusClient(uri=self.uri, token=self.token, timeout=3)
        return self._client

    def upsert(self, records):
        # 只有显式资料导入才建collection；聊天检索不seed、不写游戏状态。
        client = self.client()
        if not client.has_collection(self.collection, timeout=3):
            client.create_collection(collection_name=self.collection, dimension=self.dimension,
                                     id_type="string", max_length=128, auto_id=False,
                                     metric_type="COSINE", consistency_level="Strong", timeout=10)
        else:
            fields = client.describe_collection(self.collection, timeout=3).get("fields", [])
            vector = next((field for field in fields if field["name"] == "vector"), {})
            if int(vector.get("params", {}).get("dim", -1)) != self.dimension:
                raise ValueError("已有collection维度不匹配，不删除或重建")
        for row in records:
            validate_vector(row["vector"], self.dimension)
        client.upsert(collection_name=self.collection, data=records, timeout=5)

    def search(self, query, world_id, embedding_key, k):
        validate_vector(query, self.dimension)
        # JSON字符串编码，不直接插入玩家提交的任意过滤表达式。
        expr = f"world_id == {json.dumps(world_id)} and embedding_key == {json.dumps(embedding_key)}"
        results = self.client().search(collection_name=self.collection, data=[query], limit=k,
            filter=expr, output_fields=["world_id", "embedding_key", "text", "source"],
            search_params={"metric_type": "COSINE", "params": {}}, consistency_level="Strong", timeout=3)
        return [{"id": hit["id"], **hit["entity"], "score": hit["distance"]}
                for hit in (results[0] if results else [])]

    def close(self):
        if self._client is not None:
            self._client.close()
