"""只运行离线夹具与临时SQL；不读本地密钥、不连接实际Milvus。"""
import json
from fastapi.testclient import TestClient
from .state import GameState
from .test_long_memory import FixtureEmbedding, FixtureIndex
from .api import create_app


def main():
    headers = {"Authorization": "Bearer demo-alice"}
    path = "/api/v1/worlds/world-alice"
    with TestClient(create_app(memory_embedder=FixtureEmbedding(), memory_index=FixtureIndex())) as client:
        saved = client.post(path + "/memories/preferences", headers=headers,
                            json={"style": "brief", "consent": True}).json()
        memory = client.app.state.long_memory
        memory.ingest(GameState("alice", "world-alice"))
        result = client.post(path + "/chat", headers=headers,
                            json={"message": "我的提示偏好", "session_id": "fresh"}).json()
        bob = client.post("/api/v1/worlds/world-bob/chat", headers={"Authorization": "Bearer demo-bob"},
                          json={"message": "我的偏好"}).json()
        client.delete(path + "/memories/" + saved["id"], headers=headers)
        revoked = client.post(path + "/chat", headers=headers, json={"message": "我的偏好"}).json()
        assert result["long_memory_status"] == "ok" and result["history_turns"] == 0
        assert bob["long_memory_ids"] == revoked["long_memory_ids"] == []
        print(json.dumps({"mode": "offline_fixture_not_qwen_or_milvus", "cross_session": "ok",
            "cross_player": "rejected", "revoked": "rejected", "reply": result["reply"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
