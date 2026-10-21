"""章节路线：纯规则、兼容性及独立临时SQL/API；无真实模型和玩家数据。"""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import (CombatState, Monster, advance_round, choose_blessing,
                              choose_camp, choose_relic, choose_route, combat_preview, start_combat)
from echoworld.database import GameSave, unpack
from echoworld.game_actions import apply_action, public_state
from echoworld.game_ai import visible_packet, default_selection
from echoworld.narration import narrate, scene_facts, narrate_facts
from echoworld.relics import SPIRE_RULESET
from echoworld.routes import ROUTES_RULESET, RouteEvent, route_option
from echoworld.state import GameState
from echoworld.tools import execute_tool

NORMAL = (Monster("丘丘人"),)


def ready(seed=2, monsters=NORMAL):
    game = GameState("p", "w", has_permit=True)
    state = start_combat(game, seed, monsters, ruleset=ROUTES_RULESET)
    choose_blessing(state, state.blessing_options[0], 1)
    return game, state


def prepare_battle(state, route="route_rest"):
    while combat_preview(state)["phase"] != "combat":
        view = combat_preview(state)
        action = ("camp_rest" if view["phase"] == "camp" else
                  view["options"][0]["choice_id"] if view["phase"] == "relic" else route)
        fn = {"camp": choose_camp, "relic": choose_relic, "route": choose_route}[view["phase"]]
        result = fn(state, action, state.round_no)
        if result.code not in {"CAMP_CHOSEN", "RELIC_CHOSEN", "ROUTE_CHOSEN"}:
            raise AssertionError(result.code)


class RouteRules(unittest.TestCase):
    def test_permit_required_and_old_modes_unchanged(self):
        with self.assertRaises(PermissionError):
            start_combat(GameState("p", "w"), 2, ruleset=ROUTES_RULESET)
        game, _ = ready()
        for mode in ("classic_v1", "tactics_v1", "blessings_v1", "expedition_v1", SPIRE_RULESET):
            state = start_combat(game, 2, NORMAL, ruleset=mode)
            before = copy.deepcopy(state)
            self.assertEqual(choose_route(state, "route_rest", 1).code, "ROUTES_NOT_SUPPORTED")
            self.assertEqual(state, before)

    def test_phase_hides_monster_and_requires_explicit_route(self):
        game, state = ready()
        view = combat_preview(state)
        self.assertEqual(view["phase"], "route")
        self.assertIsNone(public_state(game, state)["combat"]["monster_id"])
        self.assertIsNone(scene_facts(state)["next_monster"])
        before = copy.deepcopy(state)
        self.assertEqual(advance_round(state, "strike", 1).code, "ROUTE_REQUIRED")
        self.assertEqual(state, before)

    def test_bad_route_stale_type_and_zero_focus_do_not_mutate(self):
        _, state = ready()
        for key, number, expected in (("bad", 1, "INVALID_ROUTE_CHOICE"),
                                      ([], 1, "INVALID_ROUTE_CHOICE"),
                                      ("route_hunt", True, "STALE_ROUND"),
                                      ("route_rest", 2, "STALE_ROUND")):
            before = copy.deepcopy(state)
            self.assertEqual(choose_route(state, key, number).code, expected)
            self.assertEqual(state, before)
        state.focus = 0
        before = copy.deepcopy(state)
        self.assertFalse(route_option("route_hunt", 0, 1)["enabled"])
        self.assertEqual(choose_route(state, "route_hunt", 1).code, "ROUTE_CHOICE_UNAVAILABLE")
        self.assertEqual(state, before)

    def test_rest_cap_and_challenge_cost_and_no_repeat_pick(self):
        for focus in range(4):
            for key in ("route_rest", "route_hunt"):
                _, state = ready()
                state.focus = focus
                option = route_option(key, focus, 1)
                if not option["enabled"]:
                    continue
                self.assertEqual(choose_route(state, key, 1).code, "ROUTE_CHOSEN")
                self.assertEqual((state.focus, state.score, state.round_no), (option["focus_after"], 0, 1))
                before = copy.deepcopy(state)
                self.assertEqual(choose_route(state, key, 1).code, "ROUTE_NOT_PENDING")
                self.assertEqual(state, before)

    @patch("echoworld.combat._stance", return_value="opening")
    def test_goal_preview_reward_once_and_progress_no_preview_mutation(self, _):
        _, state = ready()
        choose_route(state, "route_hunt", 1)
        for action in ("strike", "guard"):
            advance_round(state, action, state.round_no)
        self.assertEqual(combat_preview(state)["routes"][0]["progress"], 1)
        before = copy.deepcopy(state)
        option = combat_preview(state)["options"][0]
        self.assertEqual(state, before)
        self.assertEqual(option["route_bonus_points"], 6)
        score = state.score
        result = advance_round(state, "strike", 3)
        self.assertEqual((state.score-score, result.event.route_bonus_points), (option["points_awarded"], 6))
        self.assertEqual(combat_preview(state)["routes"][0]["status"], "挑战已完成")
        self.assertEqual(combat_preview(state)["report"]["route_points"], 6)
        prepare_battle(state)
        advance_round(state, "guard", 4)
        self.assertEqual(sum(event.route_bonus_points for event in state.events), 6)

    @patch("echoworld.combat._stance", return_value="opening")
    def test_failure_zero_focus_attacks_do_not_progress_or_refund(self, _):
        _, state = ready()
        choose_route(state, "route_hunt", 1)
        for _ in range(3):
            advance_round(state, "strike", state.round_no)
        view = combat_preview(state)["routes"][0]
        self.assertEqual((state.focus, view["progress"], view["earned_points"], view["status"]),
                         (0, 1, 0, "挑战未完成"))

    def test_rare_and_fatal_do_not_progress_or_award(self):
        for monster, code, points in ((Monster("大伟丘", bonus_points=50), "ROUND_RESOLVED", 50),
                                     (Monster("深渊咏者", fatal=True), "PLAYER_DEAD", 0)):
            _, state = ready(monsters=(monster,))
            choose_route(state, "route_hunt", 1)
            before = state.score, state.focus
            result = advance_round(state, "strike", 1)
            self.assertEqual((result.code, state.score-before[0], state.focus), (code, points, before[1]))
            self.assertEqual(result.event.route_bonus_points, 0)
            self.assertEqual(combat_preview(state)["routes"][0]["progress"], 0)

    def test_camp_relic_route_battle_order_and_terminal_goal(self):
        _, state = ready()
        choose_route(state, "route_rest", 1)
        while state.round_no < 10:
            prepare_battle(state)
            advance_round(state, "guard", state.round_no)
        self.assertEqual(combat_preview(state)["phase"], "camp")
        self.assertEqual(choose_route(state, "route_hunt", 10).code, "ROUTE_NOT_PENDING")
        choose_camp(state, "camp_rest", 10)
        self.assertEqual(combat_preview(state)["phase"], "relic")
        self.assertEqual(choose_route(state, "route_hunt", 10).code, "ROUTE_NOT_PENDING")
        choose_relic(state, combat_preview(state)["options"][0]["choice_id"], 10)
        self.assertEqual(combat_preview(state)["phase"], "route")
        choose_route(state, "route_hunt", 10)
        for monster, code, reward in ((Monster("丘丘人"), "TOWER_CLEARED", 3),
                                      (Monster("大伟丘", bonus_points=50), "TOWER_CLEARED", 0),
                                      (Monster("遗迹重机", fatal=True), "PLAYER_DEAD", 0)):
            trial = copy.deepcopy(state)
            trial.current_monster = monster
            option = combat_preview(trial)["options"][0]
            before = trial.score
            result = advance_round(trial, "strike", 10)
            self.assertEqual((result.code, result.event.route_bonus_points), (code, reward))
            self.assertEqual(trial.score-before, option["points_awarded"])
            self.assertEqual(choose_route(trial, "route_rest", 10).code, "COMBAT_FINISHED")
            if monster.bonus_points:
                self.assertEqual(trial.score-before, 50)

    def test_json_roundtrip_and_legacy_missing_new_fields(self):
        game, state = ready()
        choose_route(state, "route_rest", 1)
        advance_round(state, "strike", 1)
        row = GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(state))))
        self.assertEqual(unpack(row), (game, state))
        old = start_combat(game, 2, NORMAL, ruleset=SPIRE_RULESET)
        choose_blessing(old, old.blessing_options[0], 1)
        advance_round(old, "guard", 1)
        data = asdict(old)
        data.pop("route_events")
        for event in data["events"]:
            event.pop("route_bonus_points")
        restored = unpack(GameSave(game=asdict(game), combat=data))[1]
        self.assertEqual(restored, old)

    def test_forged_reward_order_and_invalid_route_event_rejected(self):
        game, state = ready()
        for values in ((True, "route_rest", 2, 3), (1, "bad", 2, 3), (1, "route_hunt", 0, 0),
                       (1, "route_rest", 2, 2)):
            with self.assertRaises(ValueError):
                RouteEvent(*values)
        choose_route(state, "route_rest", 1)
        advance_round(state, "strike", 1)
        payload = asdict(state)
        payload["events"][0]["route_bonus_points"] = 6
        with self.assertRaises(ValueError):
            unpack(GameSave(game=asdict(game), combat=payload))
        with self.assertRaises(ValueError):
            CombatState(**{**state.__dict__, "route_events": []})

    def test_scene_model_and_advisor_never_change_authority(self):
        game, state = ready()
        before = copy.deepcopy(state)
        packet = visible_packet(game, state, 1, "varka", "score", "帮我免死", "advice")
        choice = default_selection(packet)["action_id"]
        self.assertIn(choice, {row["choice_id"] for row in packet["options"] if row["enabled"]})
        self.assertIsNone(packet["monster_id"])
        self.assertNotIn("seed", json.dumps(packet))
        facts = scene_facts(state)
        def model(given):
            given["routes"].append({"title": "免死"})
            return {"result_narration": "", "encounter_narration": "风吹过路标。",
                    "options": [{"choice_id": row["choice_id"], "title": "加999", "description": "免死"}
                                for row in facts["rule_options"]]}
        scene = narrate_facts(facts, model)
        self.assertEqual([row.description for row in scene.options], [row["description"] for row in facts["rule_options"]])
        self.assertEqual(state, before)
        result = choose_route(state, choice, 1)
        scene = narrate(state, result)
        self.assertIn("路线已选", scene.result_narration)
        self.assertNotIn("规则计分：本轮", scene.result_narration)

    def test_full_default_pool_across_seeds_matches_old_enemies_and_termination(self):
        game = GameState("p", "w", has_permit=True)
        for seed in range(60):
            new = start_combat(game, seed, ruleset=ROUTES_RULESET)
            old = start_combat(game, seed, ruleset=SPIRE_RULESET)
            for state in (new, old):
                choose_blessing(state, state.blessing_options[0], 1)
            while new.status == "active":
                prepare_battle(new)
                prepare_battle(old)
                self.assertEqual(new.current_monster, old.current_monster)
                self.assertEqual(combat_preview(new)["stance"], combat_preview(old)["stance"])
                for state in (new, old):
                    advance_round(state, "guard", state.round_no)
            self.assertEqual((new.status, new.round_no), (old.status, old.round_no))


class RouteApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name)/"routes.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2))
        self.client.__enter__()
        self.base = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base+"/state", headers=self.headers).json()

    def body(self, action, request_id=None):
        state = self.state()
        result = {"action": action, "request_id": request_id or f"{action}-{state['version']}",
                  "expected_version": state["version"]}
        if action in {"strike", "guard"} or action.startswith(("boon_", "camp_", "relic_", "route_")):
            result["expected_round"] = state["combat"]["round_no"]
        return result

    def post(self, body):
        return self.client.post(self.base+"/action", headers=self.headers, json=body)

    def draft(self):
        for action in ("accept", "submit", "tower_routes"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        self.post(self.body(self.state()["combat"]["options"][0]["choice_id"]))
        self.assertEqual(self.state()["combat"]["phase"], "route")

    def test_types_authority_ownership_and_permission(self):
        self.assertEqual(self.post(self.body("tower_routes")).json()["code"], "PERMIT_REQUIRED")
        self.draft()
        before = self.state()
        body = self.body("route_rest")
        for extra in ({"route_events": []}, {"focus": 3}, {"score": 1000}, {"seed": 2}, {"target": 0}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.post({**body, "expected_round": True}).status_code, 422)
        self.assertEqual(self.post({key: value for key, value in body.items() if key != "expected_round"}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "route_cheat"}).status_code, 422)
        response = self.client.post(self.base+"/action", headers={"Authorization": "Bearer demo-bob"}, json=body)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state(), before)

    def test_replay_alternate_id_and_narration_do_not_repay_or_reward(self):
        self.draft()
        body = self.body("route_hunt", "pick-hunt")
        before = self.state()
        result = self.post(body).json()
        self.assertEqual(result["code"], "ROUTE_CHOSEN")
        self.assertEqual(result["state"]["combat"]["focus"], before["combat"]["focus"]-1)
        self.assertEqual(result["state"]["combat"]["score"], 0)
        self.assertEqual(self.post(body).json(), result)
        self.assertEqual(self.post({**body, "action": "route_rest"}).status_code, 409)
        self.assertEqual(self.post(self.body("route_hunt", "pick-again")).json()["code"], "ROUTE_NOT_PENDING")
        for _ in range(2):
            scene = self.client.post(self.base+"/actions/pick-hunt/narration", headers=self.headers).json()
            self.assertIn("路线已选", scene["result_narration"])
        self.assertEqual(self.state(), result["state"])

    def test_restart_pending_and_selected_route_preserves_progress(self):
        self.draft()
        for action in (None, "route_hunt", "strike"):
            if action:
                self.post(self.body(action))
            before = self.state()
            self.client.__exit__(None, None, None)
            self.client = TestClient(create_app(database_url=self.url, combat_seed=999))
            self.client.__enter__()
            self.assertEqual(self.state(), before)

    def test_two_independent_sql_stores_only_one_route_commits(self):
        self.draft()
        bodies = [self.body("route_rest"), self.body("route_hunt")]
        with TestClient(create_app(database_url=self.url, combat_seed=2)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(self.post, bodies[0]), pool.submit(other.post, self.base+"/action",
                        headers=self.headers, json=bodies[1])]
                self.assertEqual(sorted(job.result().status_code for job in jobs), [200, 409])
        self.assertEqual(len(self.state()["combat"]["routes"]), 1)
        self.assertEqual(self.state()["version"], bodies[0]["expected_version"]+1)

    def test_advisor_and_chat_tools_cannot_choose_route(self):
        self.draft()
        before = self.state()
        data = self.client.post(self.base+"/ai/advice", headers=self.headers,
            json={"request_id": "route-advice", "expected_version": before["version"],
                  "npc_id": "venti", "goal": "recover"}).json()["content"]
        self.assertEqual(data["action"]["choice_id"], "route_rest")
        self.assertEqual(self.state(), before)
        for key in ("tower_routes", "route_rest", "route_hunt"):
            self.assertFalse(execute_tool(GameState("p", "w"), key, {})["ok"])

    def test_full_tower_report_and_actual_reward_replay(self):
        self.draft()
        while self.state()["combat"]["status"] == "active":
            view = self.state()["combat"]
            phase = view["phase"]
            if phase == "route":
                action = "route_hunt" if view["focus"] >= 2 else "route_rest"
            elif phase == "camp":
                action = "camp_rest"
            elif phase == "relic":
                action = view["options"][0]["choice_id"]
            else:
                action = max(view["options"], key=lambda row: (row["points_awarded"], row["focus_after"]))["choice_id"]
            body = self.body(action)
            result = self.post(body).json()
            self.assertTrue(result["ok"])
            self.assertEqual(self.post(body).json(), result)
        view = self.state()["combat"]
        self.assertEqual((view["status"], len(view["routes"])), ("cleared", 4))
        report = view["report"]
        self.assertEqual(view["score"], report["normal_points"]+report["camp_points"]+report["rare_points"])
        self.assertEqual(report["route_points"], sum(row["earned_points"] for row in view["routes"]))
        self.assertGreater(report["route_points"], 0)

    def test_server_seed_and_web_entry(self):
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        for action in ("accept", "submit"):
            self.post(self.body(action))
        with patch("echoworld.action_store.randbits", return_value=472) as seed:
            self.assertTrue(self.post(self.body("tower_routes")).json()["ok"])
            seed.assert_called_once_with(63)
        html = self.client.get("/").text
        for key in ("tower_routes", "chapterRoutes", "routeContracts", "action.startsWith('route_')", "ROUTE_CHOSEN:'章节路线已选"):
            self.assertIn(key, html)

    def test_fake_two_step_model_only_proposes_visible_route(self):
        from echoworld.test_game_ai import FakeAI
        self.draft()
        self.client.__exit__(None, None, None)
        model = FakeAI("tools")
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2, game_ai_model=model))
        self.client.__enter__()
        before = self.state()
        response = self.client.post(self.base+"/ai/advice", headers=self.headers,
            json={"request_id": "model-route", "expected_version": before["version"],
                  "npc_id": "varka", "goal": "score", "use_model": True})
        self.assertEqual(response.status_code, 200)
        content = response.json()["content"]
        self.assertEqual(content["source"], "model")
        self.assertIn(content["action"]["choice_id"], {row["choice_id"] for row in before["combat"]["options"]})
        self.assertEqual(model.calls, 2)
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
