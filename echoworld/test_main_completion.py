"""主版收尾：临时文件数据库、离线模型、故障注入；不触碰用户存档。"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event, select

from echoworld.action_store import ActionReceipt, execute_action
from echoworld.api import create_app
from echoworld.chat_memory import ChatTurn
from echoworld.chat_recovery import ChatRecovery, DurableChat
from echoworld.database import GameSave, SaveStore
from echoworld.test_npc_agent import scripted_model, tool_call


class MainCompletionTests(unittest.TestCase):
    headers = {"Authorization": "Bearer demo-alice"}
    base = "/api/v1/worlds/world-alice"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.sqlite3"
        self.url = "sqlite+pysqlite:///" + self.path.as_posix()

    def tearDown(self):
        self.temp.cleanup()

    def app(self, model=None):
        return create_app(model, database_url=self.url)

    def send(self, client, name, request_id, version, **kwargs):
        return client.post(self.base + "/action", headers=self.headers, json={
            "action": name, "request_id": request_id, "expected_version": version, **kwargs})

    def chat(self, client, request_id="chat1", message="查许可"):
        return client.post(self.base + "/chat", headers=self.headers,
                           json={"request_id": request_id, "message": message})

    def test_actions_require_id_version_and_strict_types(self):
        with TestClient(self.app()) as client:
            for body in [{"action": "accept"},
                         {"action": "accept", "request_id": "id", "expected_version": True},
                         {"action": "accept", "request_id": "", "expected_version": 0}]:
                self.assertEqual(client.post(self.base + "/action", headers=self.headers, json=body).status_code, 422)

    def test_same_id_replays_exact_result_and_changed_payload_conflicts(self):
        with TestClient(self.app()) as client:
            first = self.send(client, "accept", "a", 0)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(self.send(client, "accept", "a", 0).json(), first.json())
            self.assertEqual(self.send(client, "claim", "a", 1).status_code, 409)
            state = client.get(self.base + "/state", headers=self.headers).json()
            self.assertEqual(state["version"], 1)

    def test_old_version_rejected_and_failure_receipt_does_not_become_success(self):
        with TestClient(self.app()) as client:
            self.send(client, "accept", "a", 0)
            stale = self.send(client, "submit", "s", 0)
            self.assertEqual((stale.status_code, stale.json()["code"]), (409, "VERSION_CONFLICT"))
            self.send(client, "submit", "new", 1)
            self.assertEqual(self.send(client, "submit", "s", 0).json(), stale.json())

    def test_action_commit_survives_lost_response_restart_and_receipt_lookup(self):
        with TestClient(self.app()) as client:
            self.send(client, "accept", "a", 0)
            result = self.send(client, "submit", "s", 1).json()
        with TestClient(self.app()) as client:
            self.assertEqual(self.send(client, "submit", "s", 1).json(), result)
            receipt = client.get(self.base + "/actions/s", headers=self.headers).json()
            self.assertEqual(receipt["response"], result)
            self.assertEqual(client.get(self.base + "/state", headers=self.headers).json()["inventory"], {"herb": 0})
            self.assertEqual(client.get("/api/v1/worlds/world-bob/actions/s",
                headers={"Authorization": "Bearer demo-bob"}).status_code, 404)

    def test_receipt_insert_failure_rolls_back_business_cas(self):
        with TestClient(self.app(), raise_server_exceptions=False) as client:
            self.send(client, "accept", "a", 0)
            def fail(connection, cursor, statement, *args):
                if statement.startswith("UPDATE action_receipts"):
                    raise RuntimeError("receipt failure")
            engine = client.app.state.store.engine
            event.listen(engine, "before_cursor_execute", fail)
            try:
                self.assertEqual(self.send(client, "submit", "s", 1).status_code, 500)
            finally:
                event.remove(engine, "before_cursor_execute", fail)
            state = client.get(self.base + "/state", headers=self.headers).json()
            self.assertEqual((state["inventory"], state["has_permit"], state["version"]), ({"herb": 1}, False, 1))
            self.assertEqual(client.get(self.base + "/actions/s", headers=self.headers).status_code, 404)
            self.assertEqual(self.send(client, "submit", "s", 1).json()["code"], "COMPLETED")

    def test_two_independent_store_locks_use_database_cas(self):
        first, second = SaveStore(self.url), SaveStore(self.url)
        try:
            execute_action(first, "alice", "world-alice", {"action": "accept", "request_id": "a", "expected_version": 0})
            def submit(pair):
                store, key = pair
                return execute_action(store, "alice", "world-alice", {
                    "action": "submit", "request_id": key, "expected_version": 1})[0]["code"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(pool.map(submit, [(first, "s1"), (second, "s2")]))
            self.assertCountEqual(codes, ["COMPLETED", "VERSION_CONFLICT"])
            with first.sessions() as db:
                row = db.get(GameSave, "world-alice")
                self.assertEqual((row.version, row.game["inventory"]), (2, {"herb": 0}))
        finally:
            first.close()
            second.close()

    def test_same_id_independent_stores_only_one_receipt(self):
        first, second = SaveStore(self.url), SaveStore(self.url)
        try:
            payload = {"action": "accept", "request_id": "a", "expected_version": 0}
            with ThreadPoolExecutor(max_workers=2) as pool:
                replies = list(pool.map(lambda store: execute_action(store, "alice", "world-alice", payload), [first, second]))
            self.assertEqual(replies[0], replies[1])
            with first.sessions() as db:
                self.assertEqual(len(db.scalars(select(ActionReceipt)).all()), 1)
        finally:
            first.close()
            second.close()

    def test_old_schema_migration_preserves_progress(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE game_saves (world_id VARCHAR(80) PRIMARY KEY, player_id VARCHAR(80), game JSON, combat JSON)")
            connection.execute("INSERT INTO game_saves VALUES (?, ?, ?, NULL)", (
                "world-alice", "alice", json.dumps({"player_id": "alice", "world_id": "world-alice",
                "inventory": {"herb": 8}, "quest_stage": "completed", "has_permit": True})))
        store = SaveStore(self.url)
        try:
            with store.sessions() as db:
                row = db.get(GameSave, "world-alice")
                self.assertEqual((row.game["inventory"], row.game["has_permit"], row.version), ({"herb": 8}, True, 0))
        finally:
            store.close()

    def test_earlier_receipt_schema_adds_narration_columns_without_losing_receipt(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE action_receipts (scope VARCHAR(64), request_id VARCHAR(80), "
                "payload_hash VARCHAR(64), response JSON, http_status INTEGER, PRIMARY KEY(scope,request_id))")
            connection.execute("INSERT INTO action_receipts VALUES ('scope','old','hash',?,200)",
                               (json.dumps({"ok": True, "code": "COMPLETED"}),))
        store = SaveStore(self.url)
        try:
            with store.sessions() as db:
                receipt = db.get(ActionReceipt, ("scope", "old"))
                self.assertEqual(receipt.response["code"], "COMPLETED")
                self.assertIsNone(receipt.narration_facts)
                self.assertIsNone(receipt.narration)
        finally:
            store.close()

    def test_chat_replay_does_not_call_model_or_add_history_again(self):
        calls = []
        def model(*args):
            calls.append(1)
            return {"role": "assistant", "content": "没有许可。"}
        with TestClient(self.app(model)) as client:
            first = self.chat(client).json()
            self.assertEqual(self.chat(client).json(), first)
            self.assertEqual(self.chat(client, message="背包").status_code, 409)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(client.get(self.base + "/chat/history", headers=self.headers).json()["turns"]), 1)
        with TestClient(self.app(lambda *_: self.fail("completed run must not regenerate"))) as client:
            self.assertEqual(self.chat(client).json(), first)
            self.assertEqual(client.get(self.base + "/chat/runs/" + first["run_id"], headers=self.headers).json()["response"], first)

    def test_node_crash_restarts_at_pending_tool_with_preserved_budget(self):
        with TestClient(self.app(scripted_model(tool_call())), raise_server_exceptions=False) as client:
            with patch("echoworld.agent_graph.tools_step", side_effect=RuntimeError("injected crash")):
                self.assertEqual(self.chat(client).status_code, 500)
            with client.app.state.store.sessions() as db:
                job = db.scalar(select(DurableChat))
                run_id = job.run_id
                self.assertEqual(job.status, "interrupted")
                self.assertEqual(db.scalars(select(ChatTurn)).all(), [])
        with TestClient(self.app(scripted_model({"role": "assistant", "content": "没有许可。"}))) as client:
            result = client.post(self.base + "/chat/runs/" + run_id + "/resume", headers=self.headers).json()
            self.assertEqual((result["model_calls"], result["tool_calls"]), (2, 1))
            self.assertEqual(result["node_trace"], ["model", "tools", "model", "finalize"])
            self.assertEqual(len(client.get(self.base + "/chat/history", headers=self.headers).json()["turns"]), 1)

    def test_finished_graph_sql_failure_recovers_without_rerunning_model(self):
        with TestClient(self.app(scripted_model({"role": "assistant", "content": "没有许可。"})),
                        raise_server_exceptions=False) as client:
            with patch("echoworld.chat_memory.ChatMemory.append_turn", side_effect=RuntimeError("sql failure")):
                self.assertEqual(self.chat(client).status_code, 500)
        with TestClient(self.app(lambda *_: self.fail("finished checkpoint must not call model"))) as client:
            result = self.chat(client).json()
            self.assertEqual(result["model_calls"], 1)
            self.assertEqual(len(client.get(self.base + "/chat/history", headers=self.headers).json()["turns"]), 1)

    def test_resume_rejects_changed_save_and_foreign_scope(self):
        with TestClient(self.app(scripted_model(tool_call())), raise_server_exceptions=False) as client:
            with patch("echoworld.agent_graph.tools_step", side_effect=RuntimeError("crash")):
                self.chat(client)
            with client.app.state.store.sessions() as db:
                run_id = db.scalar(select(DurableChat)).run_id
            self.send(client, "accept", "a", 0)
            response = client.post(self.base + "/chat/runs/" + run_id + "/resume", headers=self.headers)
            self.assertEqual((response.status_code, response.json()["detail"]["code"]), (409, "CHECKPOINT_STATE_CHANGED"))
            self.assertEqual(client.post("/api/v1/worlds/world-bob/chat/runs/" + run_id + "/resume",
                headers={"Authorization": "Bearer demo-bob"}).status_code, 404)

    def test_sse_disconnect_replay_preserves_result_and_one_history_turn(self):
        with TestClient(self.app()) as client:
            body = {"message": "许可", "request_id": "stream1"}
            first = client.post(self.base + "/chat/stream", headers=self.headers, json=body)
            # 模拟结果已提交但客户端未看到最后一帧：用同ID重连。
        with TestClient(self.app()) as client:
            second = client.post(self.base + "/chat/stream", headers=self.headers, json=body)
            self.assertEqual(first.text, second.text)
            self.assertEqual(len(client.get(self.base + "/chat/history", headers=self.headers).json()["turns"]), 1)

    def test_schema_rejected_and_same_database_chat_file_lock_is_exclusive(self):
        with TestClient(self.app()) as client:
            self.chat(client)
            with client.app.state.store.sessions.begin() as db:
                db.scalar(select(DurableChat)).schema_version = 99
            self.assertEqual(self.chat(client).json()["detail"]["code"], "CHECKPOINT_SCHEMA_UNSUPPORTED")
            second = SaveStore(self.url)
            recovery = ChatRecovery(second)
            try:
                with client.app.state.recovery.execution_lock():
                    from echoworld.action_store import RequestConflict
                    with self.assertRaises(RequestConflict):
                        with recovery.execution_lock():
                            self.fail("must not acquire a second process-independent lock")
            finally:
                recovery.close()
                second.close()

    def test_narration_failure_only_degrades_scene_not_committed_round(self):
        def timeout(*_):
            raise TimeoutError("injected narrator timeout")
        with TestClient(create_app(database_url=self.url, narrator=timeout)) as client:
            self.send(client, "accept", "a", 0)
            self.send(client, "submit", "s", 1)
            self.send(client, "tower", "t", 2)
            turn = self.send(client, "strike", "r1", 3, expected_round=1).json()
            before = client.get(self.base + "/state", headers=self.headers).json()
            scene = client.post(self.base + "/actions/r1/narration", headers=self.headers).json()
            self.assertEqual(scene["source"], "template")
            self.assertEqual(client.get(self.base + "/state", headers=self.headers).json(), before)
            self.assertEqual(before["combat"]["round_no"], 2)
            self.assertEqual(self.send(client, "strike", "r1", 3, expected_round=1).json(), turn)

    def test_valid_model_narration_cached_and_wrong_options_rejected(self):
        calls = []
        def narrator(facts):
            calls.append(facts)
            return {"result_narration": "", "encounter_narration": "迷雾里传来沉重的脚步声。",
                    "options": [{"choice_id": name, "title": name, "description": "尝试行动"}
                                for name in facts["allowed_choices"]]}
        with TestClient(create_app(database_url=self.url, narrator=narrator)) as client:
            self.send(client, "accept", "a", 0)
            self.send(client, "submit", "s", 1)
            self.send(client, "tower", "t", 2)
            first = client.post(self.base + "/actions/t/narration", headers=self.headers).json()
            self.assertEqual(first["source"], "model")
            self.assertEqual(client.post(self.base + "/actions/t/narration", headers=self.headers).json(), first)
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.post("/api/v1/worlds/world-bob/actions/t/narration",
                headers={"Authorization": "Bearer demo-bob"}).status_code, 404)
        from echoworld.narration import narrate_facts
        poisoned = dict(calls[0])
        self.assertEqual(narrate_facts(poisoned, lambda _: {
            "result_narration": "", "encounter_narration": "错误选项", "options": []}).source, "template")

    def test_interrupted_private_context_is_not_sent_after_revocation(self):
        with TestClient(self.app(scripted_model(tool_call())), raise_server_exceptions=False) as client:
            with patch("echoworld.agent_graph.tools_step", side_effect=RuntimeError("crash")):
                self.chat(client)
            with client.app.state.store.sessions.begin() as db:
                row = db.scalar(select(DurableChat))
                context = dict(row.context)
                context["memories"] = {"status": "ready", "hits": [{"id": "revoked", "version": 1}]}
                row.context = context
            with patch.object(client.app.state.long_memory, "refresh", return_value=[]):
                self.assertEqual(self.chat(client).json()["detail"]["code"], "CHECKPOINT_MEMORY_CHANGED")

    def test_real_processes_compete_using_sql_cas_not_python_lock(self):
        store = SaveStore(self.url)
        execute_action(store, "alice", "world-alice", {"action": "accept", "request_id": "a", "expected_version": 0})
        store.close()
        script = (
            "import json,sys; from echoworld.database import SaveStore; "
            "from echoworld.action_store import execute_action; s=SaveStore(sys.argv[1]); "
            "r=execute_action(s,'alice','world-alice',{'action':'submit','request_id':sys.argv[2],'expected_version':1}); "
            "print(json.dumps(r)); s.close()"
        )
        processes = [subprocess.Popen([sys.executable, "-c", script, self.url, key],
            cwd=Path(__file__).parent.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for key in ("process1", "process2")]
        codes = []
        try:
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, error)
                codes.append(json.loads(output)[0]["code"])
            self.assertCountEqual(codes, ["COMPLETED", "VERSION_CONFLICT"])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_hard_process_exit_releases_lock_and_preserves_pending_graph_node(self):
        script = """
import os,sys
from echoworld.api import create_app
from echoworld.database import SaveStore,GameSave,unpack
from echoworld.chat_recovery import ChatRecovery
from echoworld.agent_graph import build_npc_graph
from echoworld.agent_steps import initial_turn
from echoworld.test_npc_agent import scripted_model,tool_call
store=SaveStore(sys.argv[1]); recovery=ChatRecovery(store)
with store.sessions() as db: game,_=unpack(db.get(GameSave,'world-alice'))
with recovery.execution_lock():
    req={'message':'查许可','request_id':'killed','session_id':'main'}
    job=recovery.prepare(game,req)
    recovery.save_context(job['run_id'],{'history':{'turns':[],'source':'sql','cache_status':'disabled'},
        'rag':{'status':'disabled','hits':[],'error_code':None},'memories':{'status':'skipped_facts','hits':[]}})
    graph=build_npc_graph(game,req['message'],scripted_model(tool_call()),
        checkpointer=recovery.checkpointer,interrupt_after=['model'])
    graph.invoke(initial_turn(req['message'],game_facts={'has_permit':False,'inventory':game.inventory,'quest_stage':game.quest_stage}),
        {'configurable':{'thread_id':job['run_id']}},durability='sync')
    os._exit(0)
"""
        child = subprocess.run([sys.executable, "-c", script, self.url],
            cwd=Path(__file__).parent.parent, capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 0, child.stderr)
        with TestClient(self.app(scripted_model({"role": "assistant", "content": "没有许可。"}))) as client:
            result = self.chat(client, request_id="killed").json()
            self.assertEqual((result["model_calls"], result["tool_calls"]), (2, 1))
            self.assertEqual(result["node_trace"], ["model", "tools", "model", "finalize"])
