"""默认离线机制演示；--embedding仅一次真实向量批次；--milvus显式导入/检索。"""
import argparse
import json

from fastapi.testclient import TestClient

from .api import create_app
from .embeddings import EmbeddingError, QwenEmbedding
from .lore_rag import LoreRag, load_lore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding", action="store_true")
    parser.add_argument("--milvus", action="store_true")
    parser.add_argument("--ingest", action="store_true")
    args = parser.parse_args()
    if args.ingest and not args.milvus:
        parser.error("--ingest必须与--milvus一起使用")
    if args.embedding:
        embedder = QwenEmbedding.from_environment()
        if embedder is None:
            print(json.dumps({"mode": "real_qwen", "status": "missing_config"}))
            return 2
        try:
            vectors = embedder.embed(["浮空城邦的钟楼位于风铃广场北侧。", "钟楼在哪里？"])
            from .vector_search import cosine
            print(json.dumps({"mode": "real_qwen", "model": embedder.model,
                              "dimension": embedder.dimension, "vectors": len(vectors),
                              "pair_cosine": cosine(*vectors)}))
            return 0
        except EmbeddingError as error:
            print(json.dumps({"mode": "real_qwen", "status": str(error)}))
            return 2
    if args.milvus:
        rag = LoreRag.from_environment()
        if rag is None:
            print(json.dumps({"mode": "real_qwen_milvus", "status": "missing_config"}))
            return 2
        try:
            if args.ingest:
                count = rag.ingest()
                print(json.dumps({"mode": "real_qwen_milvus", "ingested_chunks": count}))
            result = rag.retrieve("钟楼在哪里？", "world-alice")
            print(json.dumps({"mode": "real_qwen_milvus", "status": result.status,
                              "error_code": result.error_code,
                              "sources": [row["source"] for row in result.hits]}))
            return 0 if result.status == "ok" else 2
        except Exception:
            print(json.dumps({"mode": "real_qwen_milvus", "status": "INGEST_FAILED"}))
            return 2
        finally:
            rag.close()
    from .test_lore_rag import demo_rag
    with TestClient(create_app(rag=demo_rag())) as client:
        result = client.post("/api/v1/worlds/world-alice/chat",
            headers={"Authorization": "Bearer demo-alice"}, json={"message": "钟楼在哪里？"}).json()
        assert result["rag_status"] == "ok" and result["reply_policy"] == "lore_extract"
        assert "广场北侧" in result["reply"]
        print(json.dumps({"mode": "offline_fixture_not_milvus", "rag_status": result["rag_status"],
                          "reply_policy": result["reply_policy"], "sources": result["sources"],
                          "trace_saved": result["trace_saved"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
