"""公开世界观检索：查询与资料使用同一向量空间，候选回查内置可信资料。"""
import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .embeddings import EmbeddingError, QwenEmbedding, validate_vector
from .vector_search import MilvusLoreIndex


def chunk_text(text, size=320, overlap=40):
    if type(size) is not int or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("分块参数不合法")
    text = text.strip()
    chunks = []
    for start in range(0, len(text), size - overlap):
        chunks.append(text[start:start + size])
        if start + size >= len(text):
            break
    return chunks


def load_lore(world_ids=("world-alice", "world-bob")):
    records = []
    for path in sorted(Path(__file__).with_name("lore").glob("*.md")):
        for i, text in enumerate(chunk_text(path.read_text(encoding="utf-8"))):
            for world in world_ids:
                digest = hashlib.sha256(f"{world}:{path.name}:{i}:{text}".encode()).hexdigest()
                records.append({"id": digest[:32], "world_id": world, "text": text,
                                "source": f"{path.name}#{i}"})
    return records


@dataclass
class RagResult:
    status: str
    hits: list[dict] = field(default_factory=list)
    error_code: str | None = None


class LoreRag:
    def __init__(self, embedder, index, *, records=None, threshold=0.4):
        self.embedder, self.index, self.threshold = embedder, index, threshold
        self.records = {row["id"]: row for row in (load_lore() if records is None else records)}

    def ingest(self):
        rows = list(self.records.values())
        vectors = []
        for start in range(0, len(rows), 10):
            vectors.extend(self.embedder.embed([row["text"] for row in rows[start:start + 10]]))
        if len(vectors) != len(rows):
            raise EmbeddingError("EMBEDDING_RESPONSE")
        self.index.upsert([{**row, "vector": validate_vector(vector, self.embedder.dimension),
                            "embedding_key": self.embedder.embedding_key}
                           for row, vector in zip(rows, vectors)])
        return len(rows)

    def retrieve(self, question, world_id, *, k=3):
        if type(k) is not int or not 1 <= k <= 5:
            raise ValueError("k范围为1～5")
        try:
            vector = self.embedder.embed([question])[0]
            validate_vector(vector, self.embedder.dimension)
        except EmbeddingError as error:
            code = str(error)
            return RagResult("embedding_error", error_code=code if re.fullmatch(r"EMBEDDING_[A-Z0-9_]+", code)
                             else "EMBEDDING_FAILED")
        except Exception:
            return RagResult("embedding_error", error_code="EMBEDDING_FAILED")
        try:
            candidates = self.index.search(vector, world_id, self.embedder.embedding_key, k)
            hits, seen = [], set()
            for candidate in candidates:
                source = self.records.get(candidate.get("id"))
                score = candidate.get("score")
                if source is None or source["world_id"] != world_id or source["id"] in seen:
                    continue
                if any(candidate.get(key) != source[key] for key in ("world_id", "text", "source")):
                    continue
                if candidate.get("embedding_key") != self.embedder.embedding_key:
                    continue
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                    continue
                if not self.threshold <= score <= 1.00001:
                    continue
                seen.add(source["id"])
                hits.append({**source, "score": score})
            hits.sort(key=lambda hit: (-hit["score"], hit["id"]))
            return RagResult("ok" if hits else "no_match", hits[:k])
        except Exception:
            return RagResult("index_error", error_code="VECTOR_SEARCH_FAILED")

    def close(self):
        self.index.close()

    @classmethod
    def from_environment(cls):
        uri = os.getenv("ECHOWORLD_MILVUS_URI")
        if not uri:
            return None
        embedder = QwenEmbedding.from_environment()
        if not uri or embedder is None:
            return None
        return cls(embedder, MilvusLoreIndex(uri, embedder.dimension,
                   token=os.getenv("ECHOWORLD_MILVUS_TOKEN", "")))
