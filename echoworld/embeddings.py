"""千问文本向量适配：固定模型/维度，无自动重试，不打印凭据。"""
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmbeddingError(RuntimeError):
    pass


def validate_vector(vector, dimension):
    if not isinstance(vector, list) or len(vector) != dimension:
        raise EmbeddingError("EMBEDDING_DIMENSION")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
        raise EmbeddingError("EMBEDDING_INVALID")
    if not any(x != 0 for x in vector):
        raise EmbeddingError("EMBEDDING_ZERO_VECTOR")
    return vector


class QwenEmbedding:
    model = "text-embedding-v4"
    dimension = 256
    embedding_key = "qwen_v4_256_v1"
    endpoints = {
        "cn": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/embeddings",
    }

    def __init__(self, api_key, *, region="cn", timeout=8):
        if not api_key or region not in self.endpoints:
            raise EmbeddingError("EMBEDDING_CONFIG")
        self.api_key, self.endpoint, self.timeout = api_key, self.endpoints[region], timeout

    @classmethod
    def from_environment(cls):
        key = os.getenv("ECHOWORLD_QWEN_EMBEDDING_KEY") or os.getenv("DASHSCOPE_API_KEY")
        return cls(key, region=os.getenv("ECHOWORLD_EMBEDDING_REGION", "cn")) if key else None

    def embed(self, texts):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 10:
            raise EmbeddingError("EMBEDDING_BATCH")
        if any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in texts):
            raise EmbeddingError("EMBEDDING_INPUT")
        payload = {"model": self.model, "input": texts, "dimensions": self.dimension,
                   "encoding_format": "float"}
        request = Request(self.endpoint, data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.api_key,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise EmbeddingError("EMBEDDING_RESPONSE_SIZE")
            rows = json.loads(raw)["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise EmbeddingError("EMBEDDING_RESPONSE")
            by_index = {row["index"]: row["embedding"] for row in rows}
            if set(by_index) != set(range(len(texts))):
                raise EmbeddingError("EMBEDDING_RESPONSE")
            return [validate_vector(by_index[i], self.dimension) for i in range(len(texts))]
        except HTTPError as error:
            code = error.code
            error.close()
            raise EmbeddingError(f"EMBEDDING_HTTP_{code}") from None
        except (URLError, TimeoutError, OSError):
            raise EmbeddingError("EMBEDDING_NETWORK") from None
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("EMBEDDING_RESPONSE") from None
