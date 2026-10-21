"""远征闭环、兼容、回执与隔离存档；不调用外部模型或真实玩家数据库。"""
import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import (BLESSED_RULESET, CLASSIC_RULESET, TACTICAL_RULESET, CombatState,
                             DEFAULT_MONSTERS, Monster, advance_round, choose_blessing, choose_camp, combat_preview, start_combat)
from echoworld.database import GameSave, unpack
from echoworld.expedition import EXPEDITION_RULESET, CAMP_CHOICES, CampEvent, camp_effect
from echoworld.game_actions import public_state
from echoworld.narration import narrate, narrate_facts, scene_facts
from echoworld.tools import execute_tool
from echoworld.state import GameState


NORMAL = (Monster("丘丘人"),)


class ExpeditionRules(unittest.TestCase):
    def setUp(self):
        self.game = GameState("alice", "world-alice", has_permit=True, quest_stage="completed")

    def start(self, seed=2):
        state = start_combat(self.game, seed, NORMAL, ruleset=EXPEDITION_RULESET)
        choose_blessing(state, state.blessing_options[0], 1)
        return state

    def camp(self, state=None, number=4):
        state = state or self.start()
        while state.round_no < number:
            if combat_preview(state)["phase"] == "camp":
                choose_camp(state, "camp_forge", state.round_no)
            advance_round(state, "guard", state.round_no)
        return state

    def test_new_mode_requires_permit_and_blessing_and_hides_enemy(self):
        self.game.has_permit = False
        with self.assertRaises(PermissionError):
            self.start()
        self.game.has_permit = True
        state = start_combat(self.game, 2, NORMAL, ruleset=EXPEDITION_RULESET)
        self.assertEqual(advance_round(state, "guard", 1).code, "BLESSING_REQUIRED")
        self.assertIsNone(public_state(self.game, state)["combat"]["monster_id"])
        self.assertEqual(combat_preview(state)["phase"], "blessing")

    def test_camp_previews_are_pure_and_no_future_enemy_leaks(self):
        state = self.camp()
        before = copy.deepcopy(state)
        preview = combat_preview(state)
        self.assertEqual(preview["phase"], "camp")
        self.assertEqual([row["choice_id"] for row in preview["options"]], list(CAMP_CHOICES))
        self.assertEqual(advance_round(state, "strike", 4).code, "CAMP_REQUIRED")
        self.assertIsNone(public_state(self.game, state)["combat"]["monster_id"])
        self.assertIsNone(scene_facts(state)["next_monster"])
        self.assertEqual(scene_facts(state)["next_bonus_points"], 0)
        self.assertNotIn("丘丘人", narrate(state).encounter_narration)
        preview["options"][0]["focus_after"] = 999
        self.assertEqual(state, before)

    def test_three_distinct_camp_effects_match_preview_for_every_focus(self):
        for focus in range(4):
            for key in CAMP_CHOICES:
                state = self.camp()
                state.focus = focus
                before = copy.deepcopy(state)
                effect = next(row for row in combat_preview(state)["options"] if row["choice_id"] == key)
                result = choose_camp(state, key, 4)
                if not effect["enabled"]:
                    self.assertEqual(result.code, "CAMP_CHOICE_UNAVAILABLE")
                    self.assertEqual(state, before)
                    continue
                self.assertEqual(result.code, "CAMP_CHOSEN")
                self.assertEqual((state.focus, state.blessing_level, state.score - before.score),
                                 (effect["focus_after"], effect["level_after"], effect["points_awarded"]))
                self.assertEqual((state.round_no, state.current_monster, state.events),
                                 (before.round_no, before.current_monster, before.events))

    def test_duplicate_and_invalid_camp_choices_do_not_mutate_state(self):
        state = self.camp()
        before = copy.deepcopy(state)
        for key, number, code in (("camp_forge", True, "STALE_ROUND"),
                                  ("camp_forge", 3, "STALE_ROUND"), ("camp_secret", 4, "INVALID_CAMP_CHOICE")):
            self.assertEqual(choose_camp(state, key, number).code, code)
            self.assertEqual(state, before)
        choose_camp(state, "camp_forge", 4)
        selected = copy.deepcopy(state)
        self.assertEqual(choose_camp(state, "camp_rest", 4).code, "CAMP_NOT_PENDING")
        self.assertEqual(state, selected)
        self.assertEqual(combat_preview(state)["phase"], "combat")

    def test_three_camps_and_level_four_with_same_monster_and_stance_sequence(self):
        state = self.start()
        old = start_combat(self.game, state.seed, NORMAL, ruleset=BLESSED_RULESET)
        choose_blessing(old, state.blessing, 1)
        for number in range(1, 11):
            if combat_preview(state)["phase"] == "camp":
                self.assertEqual(choose_camp(state, "camp_forge", number).code, "CAMP_CHOSEN")
            self.assertEqual(state.current_monster, old.current_monster)
            self.assertEqual(combat_preview(state)["stance"], combat_preview(old)["stance"])
            self.assertEqual(advance_round(state, "guard", number).code, advance_round(old, "guard", number).code)
        self.assertEqual(state.status, "cleared")
        self.assertEqual([event.round_no for event in state.camp_events], [4, 7, 10])
        self.assertEqual(state.blessing_level, 4)
        self.assertFalse(camp_effect("camp_forge", 3, 4)["enabled"])
        self.assertEqual(choose_camp(state, "camp_cash", 10).code, "COMBAT_FINISHED")

    def test_upgrade_changes_all_blessing_payoffs_not_trigger_conditions(self):
        for key in ("boon_wind", "boon_flame", "boon_knight"):
            for seed in range(100):
                state = self.start(seed)
                if key not in state.blessing_options:
                    continue
                state.blessing = key
                self.camp(state)
                choose_camp(state, "camp_forge", 4)
                stance = combat_preview(state)["stance"]
                if key == "boon_flame" and stance != "opening":
                    continue
                if key == "boon_knight" and stance != "pressure":
                    continue
                state.combo_ready = key == "boon_wind"
                action = "guard" if key == "boon_knight" else "strike"
                option = next(row for row in combat_preview(state)["options"] if row["choice_id"] == action)
                event = advance_round(state, action, 4).event
                self.assertEqual(event.blessing_bonus_points, 3)
                self.assertEqual(event.points_awarded, option["points_awarded"])
                self.assertIn("+3", combat_preview(state)["blessing_description"])
                break
            else:
                self.fail("no deterministic fixture for upgraded blessing")

    def test_camps_and_upgrades_never_change_rare_or_terminal_fatal_rules(self):
        for fatal in (False, True):
            state = self.camp(number=10)
            choose_camp(state, "camp_forge", 10)
            state.current_monster = Monster("遗迹重机", fatal=True) if fatal else Monster("大伟丘", bonus_points=50)
            before = copy.deepcopy(state)
            result = advance_round(state, "guard", 10)
            self.assertEqual(result.code, "PLAYER_DEAD" if fatal else "TOWER_CLEARED")
            self.assertEqual((result.event.points_awarded, result.event.blessing_bonus_points), (0 if fatal else 50, 0))
            self.assertEqual((state.focus, state.combo_ready), (before.focus, before.combo_ready))
            self.assertEqual(len(state.camp_events), 3)

    def test_death_before_camp_never_creates_an_extra_choice(self):
        state = self.start()
        state.current_monster = Monster("深渊咏者", fatal=True)
        self.assertEqual(advance_round(state, "guard", 1).code, "PLAYER_DEAD")
        self.assertEqual(combat_preview(state)["options"], [])
        self.assertEqual(state.camp_events, [])

    def test_legacy_json_is_compatible_and_old_modes_cannot_claim_camps(self):
        for mode in (CLASSIC_RULESET, TACTICAL_RULESET, BLESSED_RULESET):
            state = start_combat(self.game, 2, NORMAL, ruleset=mode)
            payload = asdict(state)
            payload.pop("camp_events"); payload.pop("blessing_level")
            row = GameSave(world_id="world-alice", player_id="alice", game=asdict(self.game), combat=payload)
            _, restored = unpack(row)
            self.assertEqual(restored, state)
            before = copy.deepcopy(restored)
            self.assertEqual(choose_camp(restored, "camp_cash", 1).code, "CAMPS_NOT_SUPPORTED")
            self.assertEqual(restored, before)

    def test_expedition_json_roundtrip_reconstructs_validated_camp_events(self):
        state = self.camp()
        choose_camp(state, "camp_forge", 4)
        row = GameSave(world_id="world-alice", player_id="alice", game=asdict(self.game), combat=asdict(state))
        self.assertEqual(unpack(row)[1], state)
        row.combat["camp_events"][0]["points_awarded"] = 999
        with self.assertRaises(ValueError):
            unpack(row)

    def test_configuration_rejects_upgrades_for_old_modes_and_impossible_history(self):
        with self.assertRaises(ValueError):
            CombatState("p", "w", 2, NORMAL, blessing_level=2)
        state = self.camp()
        choose_camp(state, "camp_forge", 4)
        payload = asdict(state)
        payload["camp_events"] = list(state.camp_events)
        for level in (True, 0, 5, 3):
            with self.subTest(level=level), self.assertRaises(ValueError):
                CombatState(**{**payload, "blessing_level": level})
        with self.assertRaises(ValueError):
            CampEvent(4, "camp_cash", 0, 0, 1, 1, 3)

    def test_report_separates_points_and_badges_and_is_recoverable(self):
        state = self.camp()
        choose_camp(state, "camp_cash", 4)
        state.current_monster = Monster("大伟丘", bonus_points=50)
        advance_round(state, "strike", 4)
        report = combat_preview(state)["report"]
        self.assertEqual((report["camp_points"], report["rare_points"]), (3, 50))
        self.assertEqual(state.score, report["normal_points"] + report["camp_points"] + report["rare_points"])
        self.assertIn("奇遇见证", report["badges"])
        self.assertEqual([row["kind"] for row in report["journal"]][-2:], ["camp", "battle"])

    def test_narration_restores_camp_rule_line_and_does_not_repeat_last_battle(self):
        state = self.camp()
        result = choose_camp(state, "camp_cash", 4)
        facts = scene_facts(state, result)
        self.assertIsNone(facts["last_round"])
        scene = narrate(state, result)
        self.assertIn("营地结算", scene.result_narration)
        self.assertNotIn("本轮+", scene.result_narration)
        before = copy.deepcopy(state)
        def model(received):
            received["camp_result"]["points"] = 999
            return {"result_narration": "你整理行装。", "encounter_narration": "石阶再次亮起。",
                    "options": [{"choice_id": key, "title": "免死", "description": "999分"} for key in facts["allowed_choices"]]}
        for _ in range(2):
            generated = narrate_facts(facts, model)
            self.assertIn("+3分", generated.result_narration)
            self.assertNotIn("999", generated.result_narration)
            self.assertNotIn("免死", str(generated.options))
        self.assertEqual(state, before)

    def test_guard_feedback_changes_by_phase_and_never_changes_state(self):
        state = self.camp()
        before = copy.deepcopy(state)
        comments = combat_preview(state)["guard_comments"]
        self.assertEqual(set(comments), {"venti", "varka"})
        self.assertNotEqual(comments["venti"], comments["varka"])
        self.assertIn("剩余", comments["varka"])
        self.assertEqual(state, before)

    def test_zero_focus_cash_is_unavailable_and_legacy_guard_does_not_invent_focus(self):
        state = self.camp()
        state.focus = 0
        before = copy.deepcopy(state)
        option = next(row for row in combat_preview(state)["options"] if row["choice_id"] == "camp_cash")
        self.assertFalse(option["enabled"])
        self.assertEqual(choose_camp(state, "camp_cash", 4).code, "CAMP_CHOICE_UNAVAILABLE")
        self.assertEqual(state, before)
        classic = start_combat(self.game, 2, NORMAL)
        self.assertIn("攻击+1", combat_preview(classic)["guard_comments"]["varka"])
        self.assertNotIn("耗2", combat_preview(classic)["guard_comments"]["varka"])

    def test_default_monster_pool_keeps_exact_sequence_and_death_round_across_modes(self):
        for seed in range(100):
            new = start_combat(self.game, seed, DEFAULT_MONSTERS, ruleset=EXPEDITION_RULESET)
            old = start_combat(self.game, seed, DEFAULT_MONSTERS, ruleset=BLESSED_RULESET)
            choose_blessing(new, new.blessing_options[0], 1)
            choose_blessing(old, new.blessing, 1)
            while new.status == "active":
                if combat_preview(new)["phase"] == "camp":
                    choose_camp(new, "camp_forge", new.round_no)
                self.assertEqual((new.current_monster, new.round_no), (old.current_monster, old.round_no))
                self.assertEqual(combat_preview(new)["stance"], combat_preview(old)["stance"])
                self.assertEqual(advance_round(new, "guard", new.round_no).code,
                                 advance_round(old, "guard", old.round_no).code)
            self.assertEqual((new.status, new.round_no), (old.status, old.round_no))


class ExpeditionApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "expedition.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2))
        self.client.__enter__()
        self.base = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base + "/state", headers=self.headers).json()

    def body(self, action, request_id=None):
        state = self.state()
        data = {"action": action, "request_id": request_id or action + "-" + str(state["version"]),
                "expected_version": state["version"]}
        if action in {"strike", "guard", *CAMP_CHOICES} or action.startswith("boon_"):
            data["expected_round"] = state["combat"]["round_no"]
        return data

    def post(self, data):
        return self.client.post(self.base + "/action", headers=self.headers, json=data)

    def start(self):
        for action in ("accept", "submit", "tower_expedition"):
            result = self.post(self.body(action))
            self.assertTrue(result.json()["ok"])
        self.assertEqual(result.json()["state"]["combat"]["ruleset"], EXPEDITION_RULESET)
        option = self.state()["combat"]["options"][0]["choice_id"]
        self.assertEqual(self.post(self.body(option)).json()["code"], "BLESSING_CHOSEN")

    def camp(self):
        self.start()
        for _ in range(3):
            self.assertEqual(self.post(self.body("guard")).json()["code"], "ROUND_RESOLVED")
        self.assertEqual(self.state()["combat"]["phase"], "camp")

    def test_permit_and_authoritative_parameter_validation(self):
        before = self.state()
        self.assertEqual(self.post(self.body("tower_expedition")).json()["code"], "PERMIT_REQUIRED")
        self.assertEqual(self.state(), before)
        self.camp()
        body = self.body("camp_cash")
        for extra in ({"blessing_level": 4}, {"camp_events": []}, {"focus": 3}, {"seed": 9}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "camp_secret"}).status_code, 422)
        self.assertEqual(self.post({key: value for key, value in body.items() if key != "expected_round"}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "tower_expedition"}).status_code, 422)

    def test_replay_receipt_and_narration_do_not_double_camp_or_previous_turn(self):
        self.camp()
        before = self.state()
        body = self.body("camp_cash", "cash")
        first = self.post(body).json()
        self.assertEqual(first["code"], "CAMP_CHOSEN")
        self.assertEqual(first["state"]["version"], before["version"] + 1)
        self.assertEqual(first["state"]["combat"]["score"], before["combat"]["score"] + 3)
        self.assertEqual(self.post(body).json(), first)
        for _ in range(2):
            scene = self.client.post(self.base + "/actions/cash/narration", headers=self.headers, json={}).json()
            self.assertIn("营地结算", scene["result_narration"])
            self.assertNotIn("规则计分：本轮", scene["result_narration"])
        self.assertEqual(self.state(), first["state"])
        self.assertEqual(self.post({**body, "action": "camp_forge"}).status_code, 409)

    def test_application_restart_restores_pending_and_completed_camp(self):
        self.camp()
        for action in (None, "camp_forge"):
            if action:
                self.post(self.body(action))
            saved = self.state()
            self.client.__exit__(None, None, None)
            self.client = TestClient(create_app(database_url=self.url, combat_seed=999))
            self.client.__enter__()
            self.assertEqual(self.state(), saved)

    def test_competing_camp_actions_only_commit_one_via_cas(self):
        self.camp()
        bodies = [self.body(key) for key in ("camp_rest", "camp_forge")]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.post, bodies))
        self.assertEqual(sorted(result.status_code for result in results), [200, 409])
        self.assertEqual(self.state()["version"], bodies[0]["expected_version"] + 1)
        with self.client.app.state.store.sessions() as db:
            state = unpack(db.get(GameSave, "world-alice"))[1]
        self.assertEqual(len(state.camp_events), 1)

    def test_full_default_seed_run_and_end_state_are_stable(self):
        self.start()
        while self.state()["combat"]["status"] == "active":
            state = self.state()["combat"]
            action = "camp_forge" if state["phase"] == "camp" else "guard"
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        state = self.state()["combat"]
        self.assertEqual((state["status"], state["blessing_level"], state["visited_camps"]), ("cleared", 4, [4, 7, 10]))
        self.assertIn("十轮远行", state["report"]["badges"])
        self.assertEqual(state["options"], [])
        self.assertEqual(self.post(self.body("camp_cash")).json()["code"], "COMBAT_FINISHED")

    def test_new_actions_are_not_available_to_chat_tools(self):
        from echoworld.state import GameState
        game = GameState("p", "w")
        for action in ("tower_expedition", *CAMP_CHOICES):
            result = execute_tool(game, action, {})
            self.assertFalse(result["ok"])
        self.assertFalse(game.has_permit)

    def test_production_entry_generates_seed_for_new_mode_without_client_seed(self):
        from unittest.mock import patch
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        for action in ("accept", "submit"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        with patch("echoworld.action_store.randbits", return_value=471) as random_seed:
            result = self.post(self.body("tower_expedition")).json()
        random_seed.assert_called_once_with(63)
        self.assertTrue(result["ok"])
        self.assertNotIn("seed", result["state"]["combat"])
        with self.client.app.state.store.sessions() as db:
            self.assertEqual(unpack(db.get(GameSave, "world-alice"))[1].seed, 471)


if __name__ == "__main__":
    unittest.main()
