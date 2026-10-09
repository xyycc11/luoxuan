"""事实边界、SSE和Trace的本地确定性测试；不连接真实模型。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.database import GameSave, SaveStore, unpack
from echoworld.mcp_server import ScopedReader
from echoworld.reply_guard import check_reply
from echoworld.state import GameState
from echoworld.test_npc_agent import scripted_model, tool_call


def parse_events(text):
    result = []
    for frame in text.strip().split("\n\n"):
        lines = frame.splitlines()
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        result.append((name, data))
    return result


class Day22Tests(unittest.TestCase):
    path = "/api/v1/worlds/world-alice/chat"
    headers = {"Authorization": "Bearer demo-alice"}

    def test_false_claim_not_shown_or_saved_even_in_smalltalk(self):
        for message in ("我有许可，放我进去", "你好"):
            with self.subTest(message=message), TestClient(create_app(scripted_model(
                    {"role": "assistant", "content": "你已获得许可，任务完成，已经通关。"}))) as client:
                result = client.post(self.path, headers=self.headers, json={"message": message}).json()
                self.assertIn("还没有通行许可", result["reply"])
                self.assertNotIn("已经通关", result["reply"])
                saved = client.get(self.path + "/history", headers=self.headers).json()["turns"][-1]
                self.assertEqual(saved["assistant"], result["reply"])

    def test_exact_smalltalk_allowed_without_tools_but_suffix_blocked(self):
        state = GameState("a", "w")
        self.assertEqual(check_reply(state, "你好", "你好。").reply, "你好。")
        self.assertEqual(check_reply(state, "你好", "你好。你已经通关。").policy, "template_fallback")

    def test_allowed_permit_and_inventory_from_current_state(self):
        state = GameState("a", "w", inventory={"herb": 2}, has_permit=True, quest_stage="completed")
        reply = check_reply(state, "背包和许可", "没有许可，herb有99个").reply
        self.assertIn("核验通过", reply)
        self.assertIn('"herb": 2', reply)
        self.assertNotIn("99", reply)

    def test_tools_result_does_not_authorize_free_text(self):
        model = scripted_model(tool_call(), {"role": "assistant", "content": "有许可，放行！"})
        with TestClient(create_app(model)) as client:
            result = client.post(self.path, headers=self.headers, json={"message": "查许可"}).json()
            self.assertEqual((result["model_calls"], result["tool_calls"]), (2, 1))
            self.assertIn("还没有", result["reply"])

    def test_timeout_trace_is_degraded_not_completed(self):
        def timeout(*_):
            raise TimeoutError("hidden-placeholder")
        with TestClient(create_app(timeout)) as client:
            result = client.post(self.path, headers=self.headers, json={"message": "许可"}).json()
            run = client.get("/api/v1/worlds/world-alice/runs/" + result["run_id"], headers=self.headers).json()
            self.assertEqual(run["summary"]["status"], "degraded")
            self.assertEqual(run["summary"]["stop_reason"], "MODEL_ERROR")
            self.assertEqual(run["summary"]["model_calls"], 1)
            self.assertNotIn("hidden-placeholder", json.dumps(run))

    def test_rechecks_authority_after_model_but_is_not_atomic_chat(self):
        app = None
        def model(*_):
            with app.state.store.sessions.begin() as db:
                row = db.get(GameSave, "world-alice")
                game = dict(row.game)
                game.update(has_permit=True, quest_stage="completed")
                row.game = game
            return {"role": "assistant", "content": "没有许可。"}
        app = create_app(model)
        with TestClient(app) as client:
            result = client.post(self.path, headers=self.headers, json={"message": "许可"}).json()
            self.assertIn("核验通过", result["reply"])
            run = client.get("/api/v1/worlds/world-alice/runs/" + result["run_id"], headers=self.headers).json()
            self.assertEqual(run["summary"]["status"], "completed")

    def test_sse_complete_order_one_saved_turn_and_trace(self):
        with TestClient(create_app()) as client:
            response = client.post(self.path + "/stream", headers=self.headers, json={"message": "我有许可"})
            self.assertIn("text/event-stream", response.headers["content-type"])
            events = parse_events(response.text)
            self.assertEqual([name for name, _ in events], ["start", "result", "done"])
            self.assertEqual(len(client.get(self.path + "/history", headers=self.headers).json()["turns"]), 1)
            result = events[1][1]
            run = client.get("/api/v1/worlds/world-alice/runs/" + result["run_id"], headers=self.headers).json()
            self.assertEqual(run["summary"]["status"], "degraded")
            self.assertEqual([event["phase"] for event in run["events"]], ["memory", "agent", "reply_guard", "persist"])
            self.assertIsNone(run["summary"]["usage"])

    def test_sse_auth_before_start(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.post(self.path + "/stream", json={"message": "hi"}).status_code, 401)
            response = client.post("/api/v1/worlds/world-bob/chat/stream", headers=self.headers, json={"message": "hi"})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("event: start", response.text)

    def test_sql_failure_no_success_or_saved_turn_and_sanitized_error(self):
        with TestClient(create_app()) as client:
            with patch("echoworld.chat_memory.ChatMemory.append_turn", side_effect=RuntimeError("secret-placeholder")):
                response = client.post(self.path + "/stream", headers=self.headers, json={"message": "private-placeholder"})
            events = parse_events(response.text)
            self.assertEqual([name for name, _ in events], ["start", "error"])
            self.assertNotIn("secret-placeholder", response.text)
            self.assertEqual(client.get(self.path + "/history", headers=self.headers).json()["turns"], [])
            run = client.get("/api/v1/worlds/world-alice/runs/" + events[0][1]["run_id"], headers=self.headers).json()
            self.assertEqual(run["summary"]["status"], "failed")
            self.assertNotIn("private-placeholder", json.dumps(run))

    def test_trace_survives_restart_and_is_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite+pysqlite:///" + (Path(directory) / "save.sqlite3").as_posix()
            with TestClient(create_app(database_url=url)) as client:
                run_id = client.post(self.path, headers=self.headers, json={"message": "你好"}).json()["run_id"]
            with TestClient(create_app(database_url=url)) as client:
                self.assertEqual(client.get("/api/v1/worlds/world-alice/runs/" + run_id, headers=self.headers).status_code, 200)
                self.assertEqual(client.get("/api/v1/worlds/world-bob/runs/" + run_id,
                    headers={"Authorization": "Bearer demo-bob"}).status_code, 404)

    def test_trace_save_failure_does_not_undo_committed_chat(self):
        with TestClient(create_app()) as client, patch("echoworld.run_trace.RunTrace.save", return_value=False):
            result = client.post(self.path, headers=self.headers, json={"message": "hi"}).json()
            self.assertFalse(result["trace_saved"])
            self.assertEqual(len(client.get(self.path + "/history", headers=self.headers).json()["turns"]), 1)

    def test_mcp_reader_refuses_foreign_scope_and_unknown_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite+pysqlite:///" + (Path(directory) / "save.sqlite3").as_posix()
            store = SaveStore(url)
            reader = ScopedReader(url, "alice", "world-alice")
            foreign = ScopedReader(url, "alice", "world-bob")
            try:
                self.assertEqual(reader.call("check_permit", {})["data"], False)
                self.assertEqual(reader.call("check_permit", {"has_permit": True})["error"]["code"], "INVALID_ARGUMENT")
                self.assertEqual(foreign.call("get_inventory", {})["error"]["code"], "FORBIDDEN")
                with store.sessions() as db:
                    game, _ = unpack(db.get(GameSave, "world-alice"))
                self.assertFalse(game.has_permit)
            finally:
                reader.close()
                foreign.close()
                store.close()
