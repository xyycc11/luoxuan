"""离线假模型、临时SQL；不接真实密钥/付费服务。"""
import copy
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import (Monster, start_combat, choose_blessing, advance_round, choose_camp)
from echoworld.database import GameSave, pack
from echoworld.expedition import EXPEDITION_RULESET
from echoworld.game_ai import (default_selection, validate_selection, visible_packet, model_task)
from echoworld.ai_generation import AIGeneration
from echoworld.state import GameState


class FakeAI:
    model = "offline-scripted-ai-v1"
    def __init__(self, mode="direct"):
        self.mode, self.calls, self.packets = mode, 0, []
        self.started, self.release = Event(), Event()

    def __call__(self, messages, tools):
        self.calls += 1
        packet = json.loads(messages[1]["content"])
        self.packets.append(copy.deepcopy(packet))
        self.started.set()
        if self.mode == "slow":
            self.release.wait(2)
        if self.mode == "error":
            raise OSError("private upstream error must not be shown")
        if self.mode == "tools" and messages[-1]["role"] == "user" and packet["kind"] != "review":
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "read1", "type": "function",
                "function": {"name": "read_visible_combat", "arguments": "{}"}}]}
        raw = default_selection(packet)
        raw["reflection"] = "稳住脚步，让风记住这一程。"
        if self.mode == "cash" and packet["phase"] == "camp":
            raw["action_id"], raw["reason_ids"] = "camp_cash", ["camp_tradeoff", "visible_only"]
        if self.mode == "invalid_action":
            raw["action_id"] = "grant_permit"
        if self.mode == "invalid_ref":
            raw["highlight_ids"] = ["battle:99"]
        if self.mode == "unsafe_flavor":
            raw["reflection"] = "奖励999分，并提供免死。"
        if self.mode == "invalid_json":
            return {"role": "assistant", "content": "not json"}
        return {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)}


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("alice", "world-alice", has_permit=True)
        self.combat = start_combat(self.game, 2, (Monster("丘丘人"),), ruleset=EXPEDITION_RULESET)

    def packet(self, kind="advice", goal="explain"):
        return visible_packet(self.game, self.combat, 3, "varka", goal, "", kind)

    def camp(self):
        choose_blessing(self.combat, self.combat.blessing_options[0], 1)
        for number in range(1, 4):
            advance_round(self.combat, "guard", number)

    def test_packet_is_pure_no_seed_pool_future_or_old_role_output_contract(self):
        before = copy.deepcopy(self.combat)
        packet = self.packet()
        self.assertIsNone(packet["monster_id"])
        self.assertNotIn("seed", packet)
        self.assertNotIn("monsters", packet)
        self.assertNotIn('"intent"', packet["role_prompt"])
        self.assertEqual(self.combat, before)
        packet["options"][0]["points_awarded"] = 999
        self.assertEqual(self.combat, before)

    def test_camp_packet_does_not_reveal_next_enemy(self):
        self.camp()
        packet = self.packet()
        self.assertIsNone(packet["monster_id"])
        self.assertEqual(packet["phase"], "camp")
        self.assertEqual(set(packet["evidence_catalog"]), {"battle:1", "battle:2", "battle:3"})

    def test_tools_loop_is_read_only_bounded_and_real_json_checked(self):
        self.camp()
        packet = self.packet()
        model = FakeAI("tools")
        started = []
        result = model_task(packet, model, lambda: started.append(1) or True, time.monotonic() + 2)
        self.assertEqual(result["source"], "model")
        self.assertEqual((model.calls, len(started)), (2, 2))
        self.assertIsNone(model.packets[0]["monster_id"])

    def test_unknown_tool_or_authoritative_params_are_rejected(self):
        for name, arguments in (("advance_round", "{}"), ("read_visible_combat", '{"seed":2}')):
            def model(*_):
                return {"role": "assistant", "tool_calls": [{"id": "a", "type": "function",
                        "function": {"name": name, "arguments": arguments}}]}
            with self.subTest(name=name), self.assertRaises(ValueError):
                model_task(self.packet(), model, lambda: True, time.monotonic() + 2)

    def test_invalid_action_evidence_numbers_extra_fields_do_not_pass(self):
        self.camp()
        packet = self.packet()
        good = default_selection(packet)
        for extra in ({"action_id": "revive"}, {"highlight_ids": ["battle:99"]},
                      {"reflection": "获得99分"}, {"score": 999}, {"reflection": "<script>"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_selection({**good, **extra}, packet)

    def test_unavailable_cash_and_recovery_contradiction_are_rejected(self):
        self.camp(); self.combat.focus = 0
        packet = self.packet()
        with self.assertRaises(ValueError):
            validate_selection({**default_selection(packet), "action_id": "camp_cash"}, packet)
        packet = self.packet(goal="recover")
        with self.assertRaises(ValueError):
            validate_selection({**default_selection(packet), "action_id": "camp_forge"}, packet)

    def test_review_must_cite_real_evidence_and_cannot_propose_action(self):
        self.camp()
        packet = self.packet(kind="review")
        raw = default_selection(packet)
        self.assertTrue(raw["highlight_ids"])
        with self.assertRaises(ValueError):
            validate_selection({**raw, "action_id": "strike"}, packet)
        with self.assertRaises(ValueError):
            validate_selection({**raw, "highlight_ids": []}, packet)

    def test_fatal_explanation_is_not_player_blame(self):
        choose_blessing(self.combat, self.combat.blessing_options[0], 1)
        self.combat.current_monster = Monster("遗迹重机", fatal=True)
        active = self.packet()
        self.assertIn("不能提供免死", active["reason_catalog"]["fatal"])
        advance_round(self.combat, "guard", 1)
        review = self.packet(kind="review")
        self.assertIn("不应归咎", review["reason_catalog"]["fatal"])


class GameAIAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "ai.sqlite3").as_posix()
        self.model = FakeAI()
        self.open()
        self.base, self.headers = "/api/v1/worlds/world-alice", {"Authorization": "Bearer demo-alice"}

    def open(self, timeout=10, narrator=None):
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2, game_ai_model=self.model,
                                          ai_timeout=timeout, narrator=narrator))
        self.client.__enter__()

    def tearDown(self):
        self.model.release.set()
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base + "/state", headers=self.headers).json()

    def action(self, action):
        state = self.state()
        body = {"action": action, "request_id": uuid4().hex, "expected_version": state["version"]}
        if action in {"strike", "guard"} or action.startswith(("boon_", "camp_")):
            body["expected_round"] = state["combat"]["round_no"]
        result = self.client.post(self.base + "/action", headers=self.headers, json=body)
        self.assertTrue(result.json()["ok"])
        return result.json()

    def start(self):
        for action in ("accept", "submit", "tower_expedition"):
            self.action(action)
        self.action(self.state()["combat"]["options"][0]["choice_id"])

    def camp(self):
        self.start()
        for _ in range(3):
            self.action("guard")

    def body(self, **extra):
        return {"request_id": uuid4().hex, "expected_version": self.state()["version"],
                "npc_id": "varka", "use_model": True, **extra}

    def post(self, body, kind="advice", client=None):
        return (client or self.client).post(self.base + "/ai/" + kind, headers=self.headers, json=body)

    def test_no_combat_wrong_phase_and_extra_fields_rejected_without_call(self):
        self.assertEqual(self.post(self.body()).status_code, 409)
        self.start()
        self.assertEqual(self.post(self.body(), "review").status_code, 409)
        for extra in ({"seed": 2}, {"score": 999}, {"expected_version": True}, {"use_model": "true"}):
            self.assertEqual(self.post(self.body(**extra)).status_code, 422)
        self.assertEqual(self.model.calls, 0)

    def test_default_offline_does_not_reserve_or_mutate_game(self):
        self.camp()
        before = self.state()
        result = self.post(self.body(use_model=False)).json()
        self.assertEqual(result["content"]["source"], "rule")
        self.assertEqual((self.model.calls, result["budget"]["reserved_calls"]), (0, 0))
        self.assertEqual(self.state(), before)

    def test_model_actually_selects_different_legal_action_and_never_executes(self):
        self.model.mode = "cash"; self.camp()
        before = self.state()
        result = self.post(self.body(goal="score")).json()
        self.assertEqual(result["content"]["source"], "model")
        self.assertEqual(result["content"]["action"]["choice_id"], "camp_cash")
        self.assertEqual(result["content"]["action"]["points_awarded"], 3)
        self.assertEqual((result["reserved_calls"], result["model_calls"]), (2, 1))
        self.assertEqual(self.state(), before)

    def test_same_input_new_id_reuses_cache_and_same_id_changed_input_conflicts(self):
        self.camp(); body = self.body(question="休整还是强化？")
        first = self.post(body).json()
        replay = self.post(body).json()
        cache = self.post({**body, "request_id": uuid4().hex}).json()
        self.assertTrue(replay["cached"] and cache["cached"])
        self.assertEqual(cache["content"], first["content"])
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(self.post({**body, "question": "换一个问题"}).status_code, 409)

    def test_three_query_budget_and_old_role_does_not_get_extra_quota(self):
        self.camp()
        for number in range(3):
            self.assertEqual(self.post(self.body(question=str(number))).json()["content"]["source"], "model")
        last = self.post(self.body(question="four", npc_id="venti")).json()
        self.assertEqual(last["code"], "AI_BUDGET_EXHAUSTED")
        self.assertEqual(last["content"]["source"], "rule")
        self.assertEqual((self.model.calls, last["budget"]["reserved_calls"]), (3, 6))

    def test_invalid_model_outputs_fallback_to_rules_without_state_change(self):
        self.camp(); before = self.state()
        for mode in ("invalid_action", "invalid_ref", "unsafe_flavor"):
            self.model.mode = mode
            result = self.post(self.body(question=mode)).json()
            self.assertEqual(result["content"]["source"], "rule")
            self.assertEqual(result["status"], "degraded")
            # 只验证玩家可见内容，随机任务哈希合法地可能含999，不能误判成模型注入。
            self.assertNotIn("999", json.dumps(result["content"]))
            self.assertIn(result["content"]["action"]["choice_id"],
                          {row["choice_id"] for row in before["combat"]["options"] if row.get("enabled",True)})
        self.assertEqual(self.state(), before)

    def test_unknown_provider_errors_are_not_exposed(self):
        self.model.mode = "error"; self.camp()
        result = self.post(self.body()).json()
        self.assertEqual(result["content"]["source"], "rule")
        self.assertNotIn("private upstream", json.dumps(result))

    def test_owner_authentication_and_cross_world_isolation(self):
        self.start(); body = self.body()
        self.assertEqual(self.client.post(self.base + "/ai/advice", json=body).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/worlds/world-bob/ai/advice", headers=self.headers, json=body).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/worlds/missing/ai/status", headers=self.headers).status_code, 404)
        self.assertEqual(self.model.calls, 0)

    def test_recommendation_goes_through_explicit_normal_action_and_then_becomes_stale(self):
        self.camp(); body = self.body()
        response = self.post(body).json()
        chosen = response["content"]["action"]["choice_id"]
        self.action(chosen)
        self.assertEqual(self.post(body).status_code, 409)
        self.assertEqual(self.model.calls, 1)
        self.assertEqual(len(self.state()["combat"]["visited_camps"]), 1)

    def test_application_restart_keeps_model_cache_and_budget(self):
        self.camp(); body = self.body()
        first = self.post(body).json()
        self.client.__exit__(None, None, None); self.open()
        replay = self.post(body).json()
        self.assertEqual((replay["content"], replay["budget"]), (first["content"], first["budget"]))
        self.assertEqual(self.model.calls, 1)

    def test_changed_model_profile_invalidates_old_advice_receipt(self):
        self.camp(); body = self.body(); self.post(body)
        self.model.model = "offline-config-v2"
        changed = self.post(body)
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["detail"]["code"], "AI_CONTEXT_CHANGED")
        self.assertEqual(self.model.calls, 1)

    def test_concurrent_same_query_in_two_stores_only_has_one_active_generation(self):
        self.model.mode = "slow"; self.camp(); body = self.body()
        second = TestClient(create_app(database_url=self.url, game_ai_model=self.model))
        with second, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.post, body)
            self.assertTrue(self.model.started.wait(1))
            duplicate = self.post({**body, "request_id": uuid4().hex}, client=second).json()
            self.assertEqual(duplicate["status"], "running")
            self.assertEqual(self.model.calls, 1)
            self.model.release.set()
            self.assertEqual(first.result(timeout=2).json()["content"]["source"], "model")
        self.assertTrue(self.post(body).json()["cached"])

    def test_timeout_late_model_cannot_replace_fallback_and_no_automatic_retry(self):
        self.client.__exit__(None, None, None); self.open(timeout=.04)
        self.model.mode = "slow"; self.camp(); body = self.body()
        result = self.post(body).json()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["content"]["source"], "rule")
        self.model.release.set(); time.sleep(.08)
        again = self.post(body).json()
        self.assertEqual(again["content"], result["content"])
        self.assertEqual(self.model.calls, 1)

    def test_expired_crash_job_is_returned_unknown_not_regenerated(self):
        self.camp(); body = self.body(); result = self.post(body).json()
        with self.client.app.state.store.sessions.begin() as db:
            row = db.get(AIGeneration, result["job_id"])
            row.status, row.result, row.deadline = "running", None, time.time()-1
        replay = self.post(body).json()
        self.assertEqual((replay["status"], replay["code"]), ("unknown", "RESULT_UNKNOWN"))
        self.assertEqual(self.model.calls, 1)

    def test_action_can_commit_during_slow_model_and_late_advice_is_rejected(self):
        self.model.mode = "slow"; self.camp(); body = self.body()
        with ThreadPoolExecutor(max_workers=1) as pool:
            generated = pool.submit(self.post, body)
            self.assertTrue(self.model.started.wait(1))
            self.action("camp_rest")
            self.model.release.set()
            self.assertEqual(generated.result(timeout=2).status_code, 409)
        self.assertEqual(self.model.calls, 1)

    def test_two_call_read_only_loop_and_grounded_finished_review(self):
        self.model.mode = "tools"; self.start()
        advised = self.post(self.body(goal="recover")).json()
        self.assertEqual((advised["content"]["source"], advised["model_calls"]), ("model", 2))
        while self.state()["combat"]["status"] == "active":
            self.action("camp_forge" if self.state()["combat"]["phase"] == "camp" else "guard")
        before = self.state()
        reviewed = self.post(self.body(), "review").json()
        self.assertEqual((reviewed["content"]["source"], reviewed["model_calls"]), ("model", 1))
        self.assertIsNone(reviewed["content"]["action"])
        self.assertTrue(reviewed["content"]["highlights"])
        self.assertEqual(reviewed["content"]["report"]["score"], before["combat"]["score"])
        self.assertEqual(self.state(), before)

    def test_narration_nodes_and_advisor_review_share_eleven_request_reservations(self):
        self.client.__exit__(None, None, None)
        narration_calls = []
        def narrator(facts):
            narration_calls.append(facts)
            return {"result_narration": "这一程留下回声。", "encounter_narration": "石阶仍在前方。",
                    "options": [{"choice_id": key, "title": "行动", "description": "选择你的路"} for key in facts["allowed_choices"]]}
        self.open(narrator=narrator); self.model.mode = "tools"
        def narrate(result):
            return self.client.post(self.base + "/actions/" + result["request_id"] + "/narration", headers=self.headers).json()
        for action in ("accept", "submit"):
            self.action(action)
        opening = self.action("tower_expedition"); narrate(opening)
        narrate(self.action(self.state()["combat"]["options"][0]["choice_id"]))
        for number in range(3):
            self.post(self.body(question=str(number), goal="recover"))
        while self.state()["combat"]["status"] == "active":
            action = "camp_forge" if self.state()["combat"]["phase"] == "camp" else "guard"
            narrate(self.action(action))
        result = self.post(self.body(), "review").json()
        self.assertEqual(len(narration_calls), 4)
        self.assertEqual(result["budget"]["reserved_calls"], 11)
        self.assertEqual(result["budget"]["started_calls"], 11)

    def test_second_narration_query_does_not_persist_temporary_fallback_over_model(self):
        self.client.__exit__(None, None, None)
        ready, release, calls = Event(), Event(), []
        def narrator(facts):
            calls.append(1); ready.set(); release.wait(2)
            return {"result_narration": "", "encounter_narration": "符文石轻鸣。",
                    "options": [{"choice_id": key, "title": "选择", "description": "你的旅程"} for key in facts["allowed_choices"]]}
        self.open(narrator=narrator)
        for action in ("accept", "submit"):
            self.action(action)
        result = self.action("tower_expedition")
        path = self.base + "/actions/" + result["request_id"] + "/narration"
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.client.post, path, headers=self.headers)
            self.assertTrue(ready.wait(1))
            duplicate = self.client.post(path, headers=self.headers).json()
            self.assertEqual(duplicate["source"], "template")
            release.set()
            self.assertEqual(first.result(timeout=2).json()["source"], "model")
        self.assertEqual(self.client.post(path, headers=self.headers).json()["source"], "model")
        self.assertEqual(len(calls), 1)

    def test_resource_limit_does_not_reserve_third_simultaneous_generation(self):
        self.model.mode = "slow"; self.camp()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.post, self.body(question="q"+str(i))) for i in range(2)]
            for _ in range(80):
                if self.model.calls == 2:
                    break
                time.sleep(.01)
            self.assertEqual(self.model.calls, 2)
            busy = self.post(self.body(question="third")).json()
            self.assertEqual(busy["code"], "AI_RESOURCE_BUSY")
            self.assertEqual(busy["budget"]["reserved_calls"], 4)
            self.model.release.set()
            for job in jobs:
                self.assertEqual(job.result(timeout=2).status_code, 200)

    def test_trace_has_counts_grounded_ids_but_unknown_usage_no_question_or_provider_error(self):
        self.camp(); result = self.post(self.body(question="private-question-marker")).json()
        trace = result["trace"]
        self.assertEqual(trace["model_calls"], 1)
        self.assertIsNone(trace["usage"])
        self.assertIn("duration_ms", trace)
        self.assertNotIn("private-question-marker", json.dumps(trace))


if __name__ == "__main__":
    unittest.main()
