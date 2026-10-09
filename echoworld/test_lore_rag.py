"""离线机制测试：Embedding与索引用替身；不冒充真实Milvus或模型质量。"""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.context import build_partitioned_context
from echoworld.embeddings import EmbeddingError, QwenEmbedding
from echoworld.lore_rag import LoreRag, chunk_text
from echoworld.vector_search import MemoryVectorIndex, MilvusLoreIndex, cosine, top_k


class FixtureEmbedding:
    """只按关键词映射三维向量，非千问模型，非语义质量评测。"""
    dimension = 3
    embedding_key = "test_keywords_3"

    def embed(self, texts):
        return [[1, 0, 0] if "钟楼" in text else [0, 1, 0] if "塔" in text else [0, 0, 1]
                for text in texts]


def demo_rag():
    rows = [{"id": "alice-city", "world_id": "world-alice", "source": "city.md#0", "text": "钟楼位于风铃广场北侧。"},
            {"id": "alice-tower", "world_id": "world-alice", "source": "tower.md#0", "text": "夜风塔也叫云笛塔。"},
            {"id": "bob-city", "world_id": "world-bob", "source": "city.md#0", "text": "钟楼位于另一个世界。"}]
    rag = LoreRag(FixtureEmbedding(), MemoryVectorIndex(), records=rows)
    rag.ingest()
    return rag


class VectorTests(unittest.TestCase):
    def test_cosine_parallel_perpendicular_opposite(self):
        self.assertEqual(cosine([1, 0], [2, 0]), 1)
        self.assertEqual(cosine([1, 0], [0, 1]), 0)
        self.assertEqual(cosine([1, 0], [-1, 0]), -1)

    def test_bad_dimension_zero_nan(self):
        for vector in ([], [1], [0, 0], [float("nan"), 1], [True, 1]):
            with self.subTest(vector=vector), self.assertRaises((ValueError, EmbeddingError)):
                cosine([1, 0], vector)

    def test_topk_bounds_and_no_mutation(self):
        rows = [{"id": "b", "vector": [0, 1]}, {"id": "a", "vector": [1, 0]}]
        before = json.dumps(rows)
        self.assertEqual(top_k([1, 0], rows, 0), [])
        self.assertEqual([row["id"] for row in top_k([1, 0], rows, 9)], ["a", "b"])
        self.assertEqual(json.dumps(rows), before)
        for k in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                top_k([1, 0], rows, k)

    def test_chunk_overlap_and_invalid_config(self):
        self.assertEqual(chunk_text("abcdefgh", 5, 2), ["abcde", "defgh"])
        self.assertEqual(chunk_text(""), [])
        with self.assertRaises(ValueError):
            chunk_text("abc", 3, 3)


class EmbeddingTests(unittest.TestCase):
    def test_openai_compatible_payload_and_index_reorder(self):
        model = QwenEmbedding("test-placeholder")
        rows = [{"index": 1, "embedding": [0, 1] + [0] * 254},
                {"index": 0, "embedding": [1, 0] + [0] * 254}]
        with patch("echoworld.embeddings.urlopen", return_value=io.BytesIO(json.dumps({"data": rows}).encode())) as opened:
            result = model.embed(["钟楼", "塔"])
        payload = json.loads(opened.call_args.args[0].data)
        self.assertEqual((payload["model"], payload["dimensions"], result[0][:2]),
                         ("text-embedding-v4", 256, [1, 0]))
        self.assertEqual(opened.call_count, 1)

    def test_http_error_sanitized_and_no_retry(self):
        error = HTTPError("https://invalid.example", 429, "private-placeholder", {}, None)
        with patch("echoworld.embeddings.urlopen", side_effect=error) as opened:
            with self.assertRaisesRegex(EmbeddingError, "^EMBEDDING_HTTP_429$"):
                QwenEmbedding("test-placeholder").embed(["钟楼"])
        self.assertEqual(opened.call_count, 1)

    def test_wrong_dimension_and_input_rejected(self):
        body = json.dumps({"data": [{"index": 0, "embedding": [1, 0]}]}).encode()
        with patch("echoworld.embeddings.urlopen", return_value=io.BytesIO(body)):
            with self.assertRaises(EmbeddingError):
                QwenEmbedding("test-placeholder").embed(["钟楼"])
        with self.assertRaises(EmbeddingError):
            QwenEmbedding("test-placeholder").embed([""])


class RagTests(unittest.TestCase):
    def test_public_source_scope_and_no_match(self):
        rag = demo_rag()
        result = rag.retrieve("钟楼在哪里", "world-alice")
        self.assertEqual(result.status, "ok")
        self.assertEqual([row["id"] for row in result.hits], ["alice-city"])
        self.assertEqual(rag.retrieve("钟楼", "not-owned-world").status, "no_match")
        self.assertEqual(rag.retrieve("不存在的资料", "world-alice").status, "no_match")

    def test_candidate_tamper_and_other_embedding_space_discarded(self):
        rag = demo_rag()
        for key, value in (("text", "伪造文字"), ("world_id", "world-bob"),
                           ("embedding_key", "different-model")):
            with self.subTest(key=key):
                saved = rag.index.records["alice-city"][key]
                rag.index.records["alice-city"][key] = value
                self.assertEqual(rag.retrieve("钟楼", "world-alice").hits, [])
                rag.index.records["alice-city"][key] = saved

    def test_embedding_and_index_errors_are_distinct(self):
        rag = demo_rag()
        with patch.object(rag.embedder, "embed", side_effect=TimeoutError("hidden-placeholder")):
            self.assertEqual(rag.retrieve("钟楼", "world-alice").status, "embedding_error")
        with patch.object(rag.index, "search", side_effect=RuntimeError("hidden-placeholder")):
            result = rag.retrieve("钟楼", "world-alice")
            self.assertEqual((result.status, result.error_code), ("index_error", "VECTOR_SEARCH_FAILED"))

    def test_milvus_filter_is_encoded_and_runtime_search_does_not_write(self):
        index = MilvusLoreIndex("http://127.0.0.1:19530", 3)
        class Client:
            def search(self, **kwargs):
                self.kwargs = kwargs
                return [[{"id": "a", "entity": {"source": "a"}, "distance": 0.9}]]
        index._client = Client()
        world = 'world-" or world_id != "'
        index.search([1, 0, 0], world, "space-v1", 3)
        self.assertIn(json.dumps(world), index._client.kwargs["filter"])
        self.assertEqual(index._client.kwargs["timeout"], 3)
        with self.assertRaises(ValueError):
            MilvusLoreIndex("http://127.0.0.1:19530", 3, collection="someone_else")


class ContextTests(unittest.TestCase):
    def test_sections_protected_and_old_history_removed_as_pairs(self):
        history = [{"user": "u" * 1000, "assistant": "a" * 1000}]
        messages, meta = build_partitioned_context("不能发许可", "当前问题", history,
            {"has_permit": False, "quest_stage": "active"}, [], max_chars=600)
        self.assertEqual([row["role"] for row in messages], ["system", "user"])
        self.assertIn("false", messages[0]["content"])
        self.assertIn("active", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], "当前问题")
        self.assertEqual(meta["cuts"][0]["section"], "history")

    def test_lore_dropped_before_history_and_pinned_overflow_rejected(self):
        lore = [{"id": "c", "source": "c", "text": "x" * 2000}]
        messages, meta = build_partitioned_context("规则", "问题", [{"user": "旧", "assistant": "答"}],
            {"quest_stage": "active"}, lore, max_chars=600)
        self.assertEqual(meta["kept_source_ids"], [])
        self.assertEqual(len(messages), 4)
        with self.assertRaises(ValueError):
            build_partitioned_context("x" * 600, "问题", [], {}, [], max_chars=200)


class RagApiTests(unittest.TestCase):
    headers = {"Authorization": "Bearer demo-alice"}
    path = "/api/v1/worlds/world-alice/chat"

    def test_sources_context_checked_output_and_sql_history(self):
        seen = []
        def model(messages, tools):
            seen.append(messages)
            return {"role": "assistant", "content": "你有许可，已经通关"}
        with TestClient(create_app(model, rag=demo_rag())) as client:
            result = client.post(self.path, json={"message": "钟楼在哪里"}, headers=self.headers).json()
            self.assertEqual((result["rag_status"], result["reply_policy"]), ("ok", "lore_extract"))
            self.assertIn("广场北侧", result["reply"])
            self.assertNotIn("已经通关", result["reply"])
            self.assertIn("授权公开资料", seen[0][0]["content"])
            self.assertEqual(len(result["sources"]), 1)
            saved = client.get(self.path + "/history", headers=self.headers).json()["turns"][-1]
            self.assertEqual(saved["assistant"], result["reply"])
            run = client.get("/api/v1/worlds/world-alice/runs/" + result["run_id"], headers=self.headers).json()
            self.assertEqual(run["summary"]["rag_status"], "ok")

    def test_fact_questions_skip_embedding_and_state_is_unchanged(self):
        rag = demo_rag()
        with TestClient(create_app(rag=rag)) as client, patch.object(rag, "retrieve") as retrieved:
            before = client.get("/api/v1/worlds/world-alice/state", headers=self.headers).json()
            result = client.post(self.path, json={"message": "我有许可"}, headers=self.headers).json()
            self.assertEqual(result["rag_status"], "skipped_facts")
            self.assertIn("还没有", result["reply"])
            retrieved.assert_not_called()
            self.assertEqual(client.get("/api/v1/worlds/world-alice/state", headers=self.headers).json(), before)

    def test_unavailable_rag_does_not_break_chat_or_expose_errors(self):
        rag = demo_rag()
        with patch.object(rag.index, "search", side_effect=RuntimeError("hidden-placeholder")), TestClient(create_app(rag=rag)) as client:
            response = client.post(self.path, json={"message": "钟楼在哪"}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rag_status"], "index_error")
            self.assertNotIn("hidden-placeholder", response.text)


if __name__ == "__main__":
    unittest.main()
