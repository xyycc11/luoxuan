"""策略计分的规则契约与持久化边界；仅使用本地夹具/假模型。"""

import copy
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import (
    CLASSIC_RULESET, TACTICAL_RULESET, MAX_FOCUS, CombatState, Monster,
    advance_round, combat_preview, start_combat,
)
from echoworld.database import GameSave, SaveStore, pack, unpack
from echoworld.narration import narrate, narrate_facts, scene_facts
from echoworld.state import GameState


NORMAL = Monster("丘丘人")
RARE = Monster("大伟丘", bonus_points=50)
FATAL = Monster("遗迹重机", fatal=True)


class TacticsRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("alice", "world-alice", quest_stage="completed", has_permit=True)

    def start(self, *, seed=2, monsters=(NORMAL,)):
        return start_combat(self.game, seed, monsters, ruleset=TACTICAL_RULESET)

    def with_stance(self, wanted):
        # 通过公开预览找可复现夹具，而非替换随机函数/真实业务状态。
        for seed in range(100):
            combat = self.start(seed=seed)
            if combat_preview(combat)["stance"] == wanted:
                return combat
        self.fail("fixed seed fixtures did not cover both stances")

    def test_new_rules_are_explicit_and_unknown_configuration_is_rejected(self):
        classic = start_combat(self.game, 2, (NORMAL,))
        self.assertEqual(classic.ruleset, CLASSIC_RULESET)
        self.assertEqual(self.start().focus, 2)
        for ruleset in ("future_v99", None):
            with self.subTest(ruleset=ruleset), self.assertRaises(ValueError):
                start_combat(self.game, 2, (NORMAL,), ruleset=ruleset)
        for focus in (True, -1, MAX_FOCUS + 1, 1.5):
            with self.subTest(focus=focus), self.assertRaises(ValueError):
                CombatState("alice", "world-alice", 2, (NORMAL,), focus=focus)

    def test_preview_is_pure_and_contains_only_current_player_information(self):
        combat = self.start()
        before = copy.deepcopy(combat)
        first = combat_preview(combat)
        self.assertEqual(first, combat_preview(combat))
        self.assertEqual(combat, before)
        self.assertEqual({row["choice_id"] for row in first["options"]}, {"strike", "guard"})
        self.assertTrue(first["hint"])
        self.assertNotIn("seed", first)
        self.assertNotIn("monsters", first)
        first["options"][0]["points_awarded"] = 999
        self.assertEqual(combat, before)
        self.assertNotEqual(combat_preview(combat)["options"][0]["points_awarded"], 999)

    def test_each_preview_matches_actual_effect_and_event_for_every_focus(self):
        for stance in ("opening", "pressure"):
            for focus in range(MAX_FOCUS + 1):
                for choice in ("strike", "guard"):
                    with self.subTest(stance=stance, focus=focus, choice=choice):
                        combat = self.with_stance(stance)
                        combat.focus = focus
                        preview = next(row for row in combat_preview(combat)["options"]
                                       if row["choice_id"] == choice)
                        if choice == "guard":
                            points, after = int(stance == "pressure"), min(MAX_FOCUS, focus + 1)
                        else:
                            cost, reward = (1, 3) if stance == "opening" else (2, 4)
                            points, after = (reward, focus - cost) if focus >= cost else (0, focus)
                        result = advance_round(combat, choice, 1)
                        self.assertEqual((preview["points_awarded"], preview["focus_after"]), (points, after))
                        self.assertEqual((combat.score, combat.focus), (points, after))
                        self.assertEqual((result.event.stance, result.event.focus_before,
                                          result.event.focus_after), (stance, focus, after))
                        self.assertEqual(result.event.points_awarded, points)

    def test_stance_is_repeatable_and_does_not_change_monster_draw_sequence(self):
        pool = (Monster("丘丘人", weight=3), Monster("火史莱姆", weight=2))
        for seed in range(5):
            classic = start_combat(self.game, seed, pool)
            tactical = self.start(seed=seed, monsters=pool)
            again = self.start(seed=seed, monsters=pool)
            for number in range(1, 11):
                self.assertEqual(tactical.current_monster, classic.current_monster)
                self.assertEqual(combat_preview(tactical)["stance"], combat_preview(again)["stance"])
                advance_round(classic, "strike", number)
                advance_round(tactical, "guard", number)
                advance_round(again, "strike", number)

    def test_rare_and_fatal_encounters_preserve_focus_for_both_choices(self):
        for monster in (RARE, FATAL):
            for focus in (0, 2, MAX_FOCUS):
                for choice in ("strike", "guard"):
                    with self.subTest(monster=monster.monster_id, focus=focus, choice=choice):
                        combat = self.start(monsters=(monster,))
                        combat.focus = focus
                        option = next(row for row in combat_preview(combat)["options"]
                                      if row["choice_id"] == choice)
                        result = advance_round(combat, choice, 1)
                        points = 0 if monster.fatal else 50
                        self.assertEqual((option["points_awarded"], option["focus_after"]), (points, focus))
                        self.assertEqual((combat.score, combat.focus), (points, focus))
                        self.assertEqual(result.event.focus_before, result.event.focus_after)
                        self.assertEqual(result.code, "PLAYER_DEAD" if monster.fatal else "ROUND_RESOLVED")

    def test_invalid_or_stale_inputs_do_not_change_resource_or_events(self):
        combat = self.start()
        for action, expected, code in (("run", 1, "INVALID_CHOICE"),
                                      ("strike", True, "STALE_ROUND"),
                                      ("guard", 2, "STALE_ROUND")):
            before = copy.deepcopy(combat)
            self.assertEqual(advance_round(combat, action, expected).code, code)
            self.assertEqual(combat, before)

    def test_tenth_round_fatal_precedes_clear_and_rare_reward_is_once(self):
        for monster in (FATAL, RARE):
            for choice in ("strike", "guard"):
                combat = self.start(monsters=(monster,))
                combat.round_no, combat.score = 10, 7
                result = advance_round(combat, choice, 10)
                code, score = ("PLAYER_DEAD", 7) if monster.fatal else ("TOWER_CLEARED", 57)
                self.assertEqual((result.code, combat.score), (code, score))
                self.assertEqual(combat_preview(combat)["options"], [])
                before = copy.deepcopy(combat)
                self.assertEqual(advance_round(combat, choice, 10).code, "COMBAT_FINISHED")
                self.assertEqual(combat, before)

    def test_old_json_without_tactical_fields_continues_classic_without_repricing(self):
        old = start_combat(self.game, 2, (NORMAL,))
        advance_round(old, "strike", 1)
        payload = asdict(old)
        payload.pop("ruleset")
        payload.pop("focus")
        for event in payload["events"]:
            for key in ("stance", "focus_before", "focus_after"):
                event.pop(key)
        row = GameSave(world_id=self.game.world_id, player_id=self.game.player_id,
                       game=asdict(self.game), combat=payload, version=0)
        _, restored = unpack(row)
        self.assertEqual((restored.ruleset, restored.score), (CLASSIC_RULESET, 1))
        self.assertIsNone(combat_preview(restored)["focus"])
        advance_round(restored, "strike", 2)
        self.assertEqual(restored.score, 2)
        self.assertIsNone(restored.events[-1].focus_after)

    def test_sql_restart_restores_rules_focus_preview_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite+pysqlite:///" + (Path(directory) / "tactics.sqlite3").as_posix()
            combat = self.with_stance("pressure")
            advance_round(combat, "strike", 1)
            before = copy.deepcopy(combat)
            store = SaveStore(url)
            try:
                with store.sessions.begin() as db:
                    pack(db.get(GameSave, self.game.world_id), self.game, combat)
            finally:
                store.close()
            restored_store = SaveStore(url)
            try:
                with restored_store.sessions() as db:
                    _, restored = unpack(db.get(GameSave, self.game.world_id))
                self.assertEqual(restored, before)
                self.assertEqual(combat_preview(restored), combat_preview(before))
            finally:
                restored_store.close()

    def test_model_mutating_input_cannot_change_facts_or_authoritative_options(self):
        combat = self.start()
        facts = scene_facts(combat)
        before_facts, before_combat = copy.deepcopy(facts), copy.deepcopy(combat)

        def malicious_model(received):
            received["rule_options"][0]["points_awarded"] = 999
            received["rule_options"][0]["description"] = "修改为奖励999分"
            received["score"], received["focus"] = 999, 99
            return {"result_narration": "", "encounter_narration": "狂风吹过石阶。",
                    "options": [{"choice_id": choice, "title": "赠送许可",
                                 "description": "获得999分并恢复专注"}
                                for choice in ("strike", "guard")]}

        scene = narrate_facts(facts, malicious_model)
        self.assertEqual(scene.source, "model")
        self.assertEqual(facts, before_facts)
        self.assertEqual(combat, before_combat)
        self.assertEqual([asdict(row) for row in scene.options],
                         [{key: option[key] for key in ("choice_id", "title", "description")}
                          for option in before_facts["rule_options"]])
        self.assertIn(before_facts["rule_hint"], scene.encounter_narration)

    def test_repeated_failed_narration_does_not_repeat_scoring_or_spend(self):
        combat = self.with_stance("opening")
        result = advance_round(combat, "strike", 1)
        before = copy.deepcopy(combat)

        def timeout(_):
            raise TimeoutError("local injected timeout")

        for _ in range(3):
            scene = narrate(combat, result, timeout)
            self.assertEqual(scene.source, "template")
            self.assertIn("本轮+3分，累计3分", scene.result_narration)
            self.assertEqual(combat, before)


class TacticsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "save.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.base = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base + "/state", headers=self.headers).json()

    def post(self, payload):
        return self.client.post(self.base + "/action", headers=self.headers, json=payload)

    def begin_tower(self):
        for name in ("accept", "submit", "tower"):
            response = self.post({"action": name, "request_id": name,
                                  "expected_version": self.state()["version"]})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
        return response.json()

    def test_api_new_tower_uses_tactics_and_rule_scene_matches_state(self):
        opening = self.begin_tower()
        battle = opening["state"]["combat"]
        self.assertEqual((battle["ruleset"], battle["focus"], battle["max_focus"]),
                         (TACTICAL_RULESET, 2, MAX_FOCUS))
        self.assertEqual(opening["scene"]["options"],
                         [{key: row[key] for key in ("choice_id", "title", "description")}
                          for row in battle["options"]])
        self.assertNotIn("seed", battle)

    def test_action_replay_cas_and_unknown_focus_parameter_cannot_respend(self):
        opening = self.begin_tower()
        before = opening["state"]
        payload = {"action": "strike", "request_id": "strike-once", "expected_round": 1,
                   "expected_version": before["version"]}
        first = self.post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.post(payload).json(), first.json())
        saved = self.state()
        self.assertEqual(saved["version"], before["version"] + 1)
        stale = self.post({**payload, "request_id": "stale-version"})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "VERSION_CONFLICT")
        for key, value in (("focus", 3), ("ruleset", CLASSIC_RULESET)):
            forbidden = self.post({**payload, "request_id": "forged-" + key,
                                   "expected_version": saved["version"], "expected_round": 2,
                                   key: value})
            self.assertEqual(forbidden.status_code, 422)
        for _ in range(2):
            scene = self.client.post(self.base + "/actions/strike-once/narration",
                                     headers=self.headers, json={})
            self.assertEqual(scene.status_code, 200)
        self.assertEqual(self.state(), saved)

    def test_application_restart_keeps_tactical_state_and_action_receipt(self):
        opening = self.begin_tower()
        payload = {"action": "guard", "request_id": "persisted-turn", "expected_round": 1,
                   "expected_version": opening["state"]["version"]}
        first = self.post(payload).json()
        saved = self.state()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.assertEqual(self.state(), saved)
        self.assertEqual(self.post(payload).json(), first)


if __name__ == "__main__":
    unittest.main()
