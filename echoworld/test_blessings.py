"""原创祝福与API回执边界；无真实模型/向量调用，仅临时存档。"""
import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.blessings import BLESSINGS, blessing_effect, offer_blessings
from echoworld.combat import (
    BLESSED_RULESET, CLASSIC_RULESET, TACTICAL_RULESET, CombatState, Monster,
    advance_round, choose_blessing, combat_preview, start_combat,
)
from echoworld.database import GameSave, pack, unpack
from echoworld.game_actions import public_state
from echoworld.narration import narrate, narrate_facts, scene_facts
from echoworld.state import GameState


NORMAL = (Monster("丘丘人"),)


class BlessingRuleTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("alice", "world-alice", has_permit=True, quest_stage="completed")

    def selected(self, blessing, stance=None):
        for seed in range(200):
            combat = start_combat(self.game, seed, NORMAL, ruleset=BLESSED_RULESET)
            if blessing not in combat.blessing_options:
                continue
            choose_blessing(combat, blessing, 1)
            if stance is None or combat_preview(combat)["stance"] == stance:
                return combat
        self.fail("fixed seeds do not cover requested fixture")

    def test_offers_are_two_unique_repeatable_and_cover_every_pair(self):
        pairs = set()
        for seed in range(100):
            offered = offer_blessings(seed)
            self.assertEqual(offered, offer_blessings(seed))
            self.assertEqual(len(set(offered)), 2)
            self.assertTrue(set(offered) <= BLESSINGS.keys())
            pairs.add(frozenset(offered))
        self.assertEqual(len(pairs), 3)

    def test_pending_preview_is_pure_hides_enemy_and_blocks_battle(self):
        combat = start_combat(self.game, 2, NORMAL, ruleset=BLESSED_RULESET)
        before = copy.deepcopy(combat)
        self.assertEqual(combat_preview(combat)["phase"], "blessing")
        self.assertIsNone(public_state(self.game, combat)["combat"]["monster_id"])
        self.assertIsNone(scene_facts(combat)["next_monster"])
        self.assertEqual(set(scene_facts(combat)["allowed_choices"]), set(combat.blessing_options))
        self.assertEqual(advance_round(combat, "strike", 1).code, "BLESSING_REQUIRED")
        self.assertEqual(combat, before)

    def test_choose_once_without_turn_score_focus_or_monster_change(self):
        combat = start_combat(self.game, 2, NORMAL, ruleset=BLESSED_RULESET)
        before = copy.deepcopy(combat)
        absent = next(key for key in BLESSINGS if key not in combat.blessing_options)
        self.assertEqual(choose_blessing(combat, absent, 1).code, "BLESSING_NOT_OFFERED")
        self.assertEqual(choose_blessing(combat, combat.blessing_options[0], True).code, "STALE_ROUND")
        self.assertEqual(combat, before)
        key = combat.blessing_options[0]
        result = choose_blessing(combat, key, 1)
        self.assertEqual(result.code, "BLESSING_CHOSEN")
        before.blessing = key
        self.assertEqual(combat, before)
        self.assertEqual(choose_blessing(combat, combat.blessing_options[1], 1).code, "BLESSING_ALREADY_CHOSEN")
        self.assertEqual(combat, before)
        self.assertIn(BLESSINGS[key].name, narrate(combat, result).result_narration)

    def test_configuration_rejects_bad_offers_and_impossible_combo(self):
        for offers, selected, ready in ((["boon_wind"], None, False),
                (["boon_wind", "boon_wind"], None, False),
                (["boon_wind", "unknown"], None, False),
                ([{}, "boon_wind"], None, False),
                (["boon_wind", "boon_flame"], "boon_knight", False),
                (["boon_wind", "boon_flame"], None, True)):
            with self.subTest(offers=offers), self.assertRaises(ValueError):
                CombatState("p", "w", 2, NORMAL, ruleset=BLESSED_RULESET,
                            blessing_options=offers, blessing=selected, combo_ready=ready)

    def test_wind_combo_does_not_stack_or_get_consumed_by_failed_strike(self):
        combat = self.selected("boon_wind")
        advance_round(combat, "guard", 1)
        advance_round(combat, "guard", 2)
        self.assertTrue(combat.combo_ready)
        combat.focus = 0
        score = combat.score
        failed = advance_round(combat, "strike", 3)
        self.assertEqual((combat.score, failed.event.blessing_bonus_points), (score, 0))
        self.assertTrue(combat.combo_ready)
        combat.focus = 3
        preview = next(row for row in combat_preview(combat)["options"] if row["choice_id"] == "strike")
        result = advance_round(combat, "strike", 4)
        self.assertEqual(result.event.blessing_bonus_points, 2)
        self.assertEqual(result.event.points_awarded, preview["points_awarded"])
        self.assertFalse(combat.combo_ready)
        combat.focus = 3
        self.assertEqual(advance_round(combat, "strike", 5).event.blessing_bonus_points, 0)

    def test_flame_and_knight_have_different_conditional_payoffs(self):
        flame = self.selected("boon_flame", "opening")
        self.assertEqual(advance_round(flame, "strike", 1).event.points_awarded, 5)
        flame = self.selected("boon_flame", "pressure")
        self.assertEqual(advance_round(flame, "strike", 1).event.points_awarded, 4)
        knight = self.selected("boon_knight", "pressure")
        self.assertEqual(advance_round(knight, "guard", 1).event.points_awarded, 3)
        knight = self.selected("boon_knight", "opening")
        self.assertEqual(advance_round(knight, "guard", 1).event.points_awarded, 0)

    def test_each_blessing_preview_matches_actual_for_all_resources_and_choices(self):
        for blessing in BLESSINGS:
            for stance in ("opening", "pressure"):
                for focus in range(4):
                    for action in ("strike", "guard"):
                        with self.subTest(blessing=blessing, stance=stance, focus=focus, action=action):
                            combat = self.selected(blessing, stance)
                            combat.focus = focus
                            combat.combo_ready = blessing == "boon_wind"
                            before = copy.deepcopy(combat)
                            option = next(row for row in combat_preview(combat)["options"] if row["choice_id"] == action)
                            self.assertEqual(combat, before)
                            result = advance_round(combat, action, 1)
                            self.assertEqual((result.event.points_awarded, combat.focus),
                                             (option["points_awarded"], option["focus_after"]))
                            self.assertEqual(result.event.blessing_bonus_points, option["blessing_bonus_points"])

    def test_rare_and_fatal_never_receive_bonus_or_consume_combo(self):
        for blessing in BLESSINGS:
            for monster in (Monster("大伟丘", bonus_points=50), Monster("遗迹重机", fatal=True)):
                for action in ("strike", "guard"):
                    combat = self.selected(blessing)
                    combat.current_monster = monster
                    combat.round_no = 10
                    combat.combo_ready = blessing == "boon_wind"
                    before = combat.combo_ready
                    result = advance_round(combat, action, 10)
                    points = 0 if monster.fatal else 50
                    self.assertEqual((combat.score, combat.focus, combat.combo_ready), (points, 2, before))
                    self.assertEqual(result.event.blessing_bonus_points, 0)
                    self.assertEqual(result.code, "PLAYER_DEAD" if monster.fatal else "TOWER_CLEARED")
                    snapshot = copy.deepcopy(combat)
                    self.assertEqual(advance_round(combat, action, 10).code, "COMBAT_FINISHED")
                    self.assertEqual(combat, snapshot)

    def test_mode_does_not_change_stances_or_monster_sequence(self):
        for seed in range(5):
            old = start_combat(self.game, seed, NORMAL, ruleset=TACTICAL_RULESET)
            new = start_combat(self.game, seed, NORMAL, ruleset=BLESSED_RULESET)
            choose_blessing(new, new.blessing_options[0], 1)
            for number in range(1, 11):
                self.assertEqual(old.current_monster, new.current_monster)
                self.assertEqual(combat_preview(old)["stance"], combat_preview(new)["stance"])
                advance_round(old, "guard", number)
                advance_round(new, "guard", number)

    def test_classic_and_tactics_json_without_new_fields_keep_old_mode(self):
        for mode in (CLASSIC_RULESET, TACTICAL_RULESET):
            old = start_combat(self.game, 2, NORMAL, ruleset=mode)
            advance_round(old, "guard", 1)
            payload = asdict(old)
            for key in ("blessing_options", "blessing", "combo_ready"):
                payload.pop(key)
            for event in payload["events"]:
                for key in ("blessing_bonus_points", "combo_before", "combo_after"):
                    event.pop(key)
            row = GameSave(world_id="world-alice", player_id="alice", game=asdict(self.game), combat=payload)
            _, restored = unpack(row)
            self.assertEqual(restored, old)
            self.assertEqual(choose_blessing(restored, "boon_wind", 2).code, "BLESSINGS_NOT_SUPPORTED")

    def test_model_cannot_rewrite_offers_and_timeout_does_not_reselect(self):
        combat = start_combat(self.game, 2, NORMAL, ruleset=BLESSED_RULESET)
        before = copy.deepcopy(combat)
        facts = scene_facts(combat)
        def model(received):
            received["rule_options"][0]["description"] = "999分和免死"
            return {"result_narration": "", "encounter_narration": "符文在风中轻鸣。",
                    "options": [{"choice_id": key, "title": "免费复活", "description": "奖励999分"}
                                for key in facts["allowed_choices"]]}
        scene = narrate_facts(facts, model)
        self.assertEqual([asdict(row) for row in scene.options],
                         [{key: row[key] for key in ("choice_id", "title", "description")}
                          for row in facts["rule_options"]])
        def timeout(_):
            raise TimeoutError()
        for _ in range(3):
            self.assertEqual(narrate(combat, model=timeout).source, "template")
        self.assertEqual(combat, before)


class BlessingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "blessings.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        self.base = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base + "/state", headers=self.headers).json()

    def post(self, body):
        return self.client.post(self.base + "/action", headers=self.headers, json=body)

    def start(self):
        for action in ("accept", "submit", "tower_blessed"):
            response = self.post({"action": action, "request_id": action,
                                  "expected_version": self.state()["version"]})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
        return response.json()

    def selection(self, request_id="selection", index=0):
        state = self.state()
        return {"action": state["combat"]["options"][index]["choice_id"],
                "request_id": request_id, "expected_round": 1, "expected_version": state["version"]}

    def test_new_mode_random_seed_and_no_permit_bypass(self):
        before = self.state()
        result = self.post({"action": "tower_blessed", "request_id": "no-permit", "expected_version": 0}).json()
        self.assertEqual(result["code"], "PERMIT_REQUIRED")
        self.assertEqual(self.state(), before)
        opening = self.start()
        self.assertEqual(opening["state"]["combat"]["phase"], "blessing")
        with self.client.app.state.store.sessions() as db:
            _, combat = unpack(db.get(GameSave, "world-alice"))
        self.assertIs(type(combat.seed), int)
        self.assertNotIn("seed", opening["state"]["combat"])

    def test_receipt_replay_restart_and_narration_keep_selection_and_round(self):
        opening = self.start()
        body = self.selection()
        first = self.post(body).json()
        self.assertEqual(first["code"], "BLESSING_CHOSEN")
        self.assertEqual(first["state"]["version"], opening["state"]["version"] + 1)
        self.assertEqual((first["state"]["combat"]["round_no"], first["state"]["combat"]["score"]), (1, 0))
        self.assertEqual(self.post(body).json(), first)
        name = first["state"]["combat"]["blessing_name"]
        scene = self.client.post(self.base + "/actions/selection/narration", headers=self.headers, json={}).json()
        self.assertIn(name, scene["result_narration"])
        saved = self.state()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        self.assertEqual(self.state(), saved)
        self.assertEqual(self.post(body).json(), first)

    def test_pending_offers_survive_application_restart_without_reroll(self):
        self.start()
        before = self.state()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        self.assertEqual(self.state(), before)

    def test_unoffered_unknown_authoritative_and_wrong_round_choices_do_not_write(self):
        self.start()
        before = self.state()
        body = self.selection()
        offered = {row["choice_id"] for row in before["combat"]["options"]}
        absent = next(key for key in BLESSINGS if key not in offered)
        self.assertEqual(self.post({**body, "action": absent}).json()["code"], "BLESSING_NOT_OFFERED")
        self.assertEqual(self.post({**body, "request_id": "wrong-round", "expected_round": 2}).json()["code"], "STALE_ROUND")
        for extra in ({"blessing": body["action"]}, {"combo_ready": True}, {"seed": 2}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "boon_secret"}).status_code, 422)
        self.assertEqual(self.state(), before)

    def test_competing_selection_uses_cas_and_only_one_is_committed(self):
        self.start()
        bodies = [self.selection("pick-" + str(index), index) for index in (0, 1)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(self.post, bodies))
        self.assertEqual(sorted(row.status_code for row in responses), [200, 409])
        saved = self.state()
        self.assertEqual(saved["combat"]["phase"], "combat")
        self.assertEqual(saved["version"], bodies[0]["expected_version"] + 1)
        other = next(key for key in BLESSINGS if BLESSINGS[key].name != saved["combat"]["blessing_name"])
        result = self.post({"action": other, "request_id": "change-later", "expected_round": 1,
                            "expected_version": saved["version"]})
        self.assertEqual(result.json()["code"], "BLESSING_ALREADY_CHOSEN")
        self.assertEqual(self.state(), saved)


if __name__ == "__main__":
    unittest.main()
