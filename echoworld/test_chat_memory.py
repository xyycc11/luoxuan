"""SQL真实落盘；缓存用可控替身，不冒充真实Redis服务验收。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.chat_memory import ChatMemory, ChatScope, ChatTurn
from echoworld.context import build_messages, message_chars
from echoworld.database import SaveStore
from echoworld.agent_graph import run_graph_turn
from echoworld.state import GameState


class FakeCache:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.read_fails = self.write_fails = False

    def get(self, key):
        if self.read_fails:
            raise OSError("模拟缓存读取超时")
        return self.values.get(key)

    def set(self, key, value, *, ex):
        if self.write_fails:
            raise OSError("模拟缓存写入超时")
        self.values[key] = value
        self.ttls[key] = ex


class ChatMemoryTests(unittest.TestCase):
    def setUp(self):
        self.store = SaveStore()
        self.cache = FakeCache()
        self.memory = ChatMemory(self.store, self.cache, recent_turns=2, ttl=60)
        self.scope = ChatScope("alice", "world-alice")

    def tearDown(self):
        self.store.close()

    def test_committed_pairs_cache_hit_and_ttl(self):
        self.memory.append_turn(self.scope, "你好", "你好呀")
        result = self.memory.load_recent(self.scope)
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["turns"], [{"user": "你好", "assistant": "你好呀"}])
        self.assertEqual(self.cache.ttls[self.memory.cache_key(self.scope)], 60)

    def test_recent_window_preserves_order(self):
        for i in range(4):
            self.memory.append_turn(self.scope, str(i), f"答{i}")
        self.assertEqual([turn["user"] for turn in self.memory.load_recent(self.scope)["turns"]], ["2", "3"])
        with self.store.sessions() as db:
            self.assertEqual(db.query(ChatTurn).count(), 4)  # SQL未因窗口裁剪删历史。

    def test_expired_key_falls_back_to_sql(self):
        self.memory.append_turn(self.scope, "你好", "回复")
        self.cache.values.clear()  # 模拟GET因TTL过期返回None，不测试真实时钟。
        result = self.memory.load_recent(self.scope)
        self.assertEqual(result["source"], "sql")
        self.assertEqual(len(result["turns"]), 1)

    def test_cache_read_error_does_not_lose_sql(self):
        self.memory.append_turn(self.scope, "你好", "回复")
        self.cache.read_fails = True
        result = self.memory.load_recent(self.scope)
        self.assertEqual((result["source"], result["cache_status"]), ("sql", "read_error"))
        self.assertEqual(result["turns"][0]["user"], "你好")

    def test_cache_write_error_does_not_undo_commit(self):
        self.cache.write_fails = True
        saved = self.memory.append_turn(self.scope, "问", "答")
        self.assertEqual(saved["cache_status"], "write_error")
        self.assertEqual(ChatMemory(self.store).load_recent(self.scope)["turns"], [{"user": "问", "assistant": "答"}])

    def test_old_cache_version_is_rejected(self):
        self.memory.append_turn(self.scope, "旧", "旧答")
        old = self.cache.values[self.memory.cache_key(self.scope)]
        self.memory.append_turn(self.scope, "新", "新答")
        self.cache.values[self.memory.cache_key(self.scope)] = old
        result = self.memory.load_recent(self.scope)
        self.assertEqual(result["source"], "sql")
        self.assertEqual(result["turns"][-1]["user"], "新")

    def test_corrupt_or_wrong_scope_cache_is_rejected(self):
        self.memory.append_turn(self.scope, "真实", "回复")
        key = self.memory.cache_key(self.scope)
        for payload in ("not-json", json.dumps({"scope": "other", "version": 1, "turns": []}),
                        json.dumps({"scope": self.scope.key, "version": 1, "turns": [{"user": "孤立"}]})):
            with self.subTest(payload=payload):
                self.cache.values[key] = payload
                self.assertEqual(self.memory.load_recent(self.scope)["turns"][0]["user"], "真实")

    def test_scope_isolates_player_world_session_and_npc(self):
        self.memory.append_turn(self.scope, "私人消息", "答")
        for other in (ChatScope("bob", "world-alice"), ChatScope("alice", "world-bob"),
                      ChatScope("alice", "world-alice", "second"),
                      ChatScope("alice", "world-alice", "main", "other-guard")):
            self.assertNotEqual(self.scope.key, other.key)
            self.assertEqual(self.memory.load_recent(other)["turns"], [])
        self.assertNotEqual(ChatScope("a:b", "c").key, ChatScope("a", "b:c").key)

    def test_sql_failure_propagates_without_cache_success(self):
        with patch("sqlalchemy.orm.Session.flush", side_effect=RuntimeError("模拟SQL失败")):
            with self.assertRaises(RuntimeError):
                self.memory.append_turn(self.scope, "不能提交", "不能成功")
        self.assertEqual(ChatMemory(self.store).load_recent(self.scope)["turns"], [])


class ContextTests(unittest.TestCase):
    def test_drops_oldest_complete_pairs_and_preserves_request(self):
        history = [{"user": "old" * 50, "assistant": "old-answer" * 50},
                   {"user": "recent", "assistant": "recent-answer"}]
        messages = build_messages("规则", "现在的问题", history, max_chars=250)
        self.assertEqual([msg["role"] for msg in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "recent")
        self.assertEqual(messages[-1]["content"], "现在的问题")
        self.assertLessEqual(message_chars(messages), 250)

    def test_oversized_base_and_incomplete_history_are_rejected(self):
        with self.assertRaises(ValueError):
            build_messages("系统" * 100, "当前", max_chars=50)
        with self.assertRaises(ValueError):
            build_messages("系统", "当前", [{"user": "孤立"}])

    def test_context_budget_stops_before_next_model_request(self):
        calls = []
        def model(messages, schemas):
            calls.append(1)
            return {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_inventory", "arguments": "{}"}}]}
        game = GameState("alice", "w", inventory={f"item{i}": 1 for i in range(1000)})
        result = run_graph_turn(game, "背包？", model, max_context_chars=2000)
        self.assertEqual(result["stop_reason"], "CONTEXT_BUDGET_EXCEEDED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["messages"][-1]["role"], "tool")  # 不剪断tool_call配对。

    def test_model_output_size_is_bounded(self):
        result = run_graph_turn(GameState("a", "w"), "问", lambda *_: {"role": "assistant", "content": "x" * 6001})
        self.assertEqual(result["stop_reason"], "MODEL_OUTPUT_TOO_LARGE")


class ChatMemoryApiTests(unittest.TestCase):
    headers = {"Authorization": "Bearer demo-alice"}
    path = "/api/v1/worlds/world-alice/chat"

    def test_second_request_receives_history_but_fresh_state_is_not_changed(self):
        seen = []
        def model(messages, schemas):
            seen.append(messages)
            return {"role": "assistant", "content": "收到"}
        with TestClient(create_app(model)) as client:
            client.post(self.path, json={"message": "我叫旅行者"}, headers=self.headers)
            response = client.post(self.path, json={"message": "刚才说了什么？"}, headers=self.headers).json()
            self.assertEqual(response["history_turns"], 1)
            self.assertEqual([msg["content"] for msg in seen[1][1:]], ["我叫旅行者", "收到", "刚才说了什么？"])
            self.assertFalse(client.get("/api/v1/worlds/world-alice/state", headers=self.headers).json()["has_permit"])

    def test_session_and_player_history_are_isolated_and_authority_forbidden(self):
        with TestClient(create_app()) as client:
            client.post(self.path, json={"message": "私人", "session_id": "one"}, headers=self.headers)
            self.assertEqual(client.get(self.path + "/history?session_id=two", headers=self.headers).json()["turns"], [])
            self.assertEqual(client.get(self.path + "/history?session_id=one",
                                        headers={"Authorization": "Bearer demo-bob"}).status_code, 403)
            for extra in ({"player_id": "bob"}, {"has_permit": True}, {"session_id": "../bad"}):
                self.assertEqual(client.post(self.path, json={"message": "问", **extra}, headers=self.headers).status_code, 422)

    def test_chat_pairs_survive_app_restart_with_no_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite+pysqlite:///" + (Path(directory) / "chat.sqlite3").as_posix()
            with TestClient(create_app(database_url=url)) as client:
                client.post(self.path, json={"message": "重启前消息"}, headers=self.headers)
            with TestClient(create_app(database_url=url)) as client:
                result = client.get(self.path + "/history", headers=self.headers).json()
                self.assertEqual(result["turns"][0]["user"], "重启前消息")
                self.assertEqual(result["source"], "sql")

    def test_sql_failure_returns_500_and_no_saved_turn(self):
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            client.get("/api/v1/worlds/world-alice/state", headers=self.headers)  # 先建夹具。
            with patch("sqlalchemy.orm.Session.flush", side_effect=RuntimeError("模拟提交故障")):
                response = client.post(self.path, json={"message": "问"}, headers=self.headers)
            self.assertEqual(response.status_code, 500)
            self.assertEqual(client.get(self.path + "/history", headers=self.headers).json()["turns"], [])


if __name__ == "__main__":
    unittest.main()
