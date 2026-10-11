"""原神主题怪物与稀有奖励：规则/保存/回执/叙事一致，不调用真实模型。"""
import copy
import unittest
from dataclasses import asdict
from random import Random

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import DEFAULT_MONSTERS, Monster, advance_round, start_combat
from echoworld.database import GameSave, SaveStore, pack, unpack
from echoworld.lore_rag import load_lore
from echoworld.narration import narrate, narrate_facts, scene_facts
from echoworld.state import GameState


RARE = next(monster for monster in DEFAULT_MONSTERS if monster.monster_id == "大伟丘")


class GenshinTowerTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("alice", "world-alice", quest_stage="completed", has_permit=True)

    def test_pool_names_weights_and_exact_rare_probability(self):
        self.assertEqual(sum(monster.weight for monster in DEFAULT_MONSTERS), 100)
        self.assertEqual(RARE.weight, 1)
        self.assertFalse(RARE.fatal)
        self.assertEqual(RARE.bonus_points, 50)
        self.assertEqual({monster.monster_id for monster in DEFAULT_MONSTERS if monster.fatal}, {"遗迹重机", "深渊咏者"})
        self.assertEqual(len({monster.monster_id for monster in DEFAULT_MONSTERS}), 8)

    def test_seed_sampling_rare_is_low_frequency(self):
        # 固定种子频率只是机制校验，不宣称每场/每100次必刷一次。
        count = sum(Random(seed + 1).choices(DEFAULT_MONSTERS,
            weights=[monster.weight for monster in DEFAULT_MONSTERS], k=1)[0] == RARE for seed in range(10000))
        self.assertTrue(50 <= count <= 150, count)

    def test_rare_strike_and_guard_each_award_exactly_fifty(self):
        for action in ("strike", "guard"):
            combat = start_combat(self.game, seed=1, monsters=(RARE,))
            self.assertEqual(combat.score, 0)  # 刷出不提前加分。
            result = advance_round(combat, action, 1)
            self.assertEqual(result.code, "ROUND_RESOLVED")
            self.assertEqual((combat.score, result.event.points_awarded), (50, 50))
            self.assertEqual(combat.status, "active")

    def test_invalid_or_duplicate_round_does_not_award(self):
        combat = start_combat(self.game, seed=1, monsters=(RARE,))
        before = copy.deepcopy(combat)
        self.assertEqual(advance_round(combat, "run", 1).code, "INVALID_CHOICE")
        self.assertEqual(combat, before)
        advance_round(combat, "strike", 1)
        before = copy.deepcopy(combat)
        self.assertEqual(advance_round(combat, "guard", 1).code, "STALE_ROUND")
        self.assertEqual(combat, before)

    def test_rare_on_tenth_round_awards_before_clear_and_never_again(self):
        combat = start_combat(self.game, seed=1, monsters=(Monster("丘丘人"),))
        for round_no in range(1, 10):
            advance_round(combat, "strike", round_no)
        combat.current_monster = RARE
        result = advance_round(combat, "strike", 10)
        self.assertEqual((result.code, combat.score), ("TOWER_CLEARED", 59))
        self.assertEqual(advance_round(combat, "strike", 10).code, "COMBAT_FINISHED")
        self.assertEqual(combat.score, 59)

    def test_new_fatal_monsters_still_kill_both_choices_without_points(self):
        for monster in (row for row in DEFAULT_MONSTERS if row.fatal):
            for action in ("strike", "guard"):
                combat = start_combat(self.game, seed=1, monsters=(monster,))
                result = advance_round(combat, action, 1)
                self.assertEqual((result.code, combat.score, result.event.points_awarded), ("PLAYER_DEAD", 0, 0))

    def test_reward_configuration_rejects_bool_negative_and_fatal_bonus(self):
        for kwargs in ({"bonus_points": True}, {"bonus_points": -1}, {"bonus_points": 1.5},
                       {"bonus_points": 50, "fatal": True}, {"weight": 0}):
            with self.assertRaises(ValueError):
                Monster("bad", **kwargs)

    def test_old_saved_monster_pool_and_events_remain_readable(self):
        old = start_combat(self.game, seed=1, monsters=(Monster("mistling"),))
        advance_round(old, "strike", 1)
        payload = asdict(old)
        for monster in payload["monsters"]:
            monster.pop("bonus_points")
        payload["current_monster"].pop("bonus_points")
        for event in payload["events"]:
            event.pop("points_awarded")
        row = GameSave(world_id=self.game.world_id, player_id=self.game.player_id,
                       game=asdict(self.game), combat=payload, version=0)
        game, restored = unpack(row)
        self.assertEqual(restored.score, 1)
        self.assertEqual(restored.current_monster.monster_id, "mistling")
        advance_round(restored, "strike", 2)
        self.assertEqual(restored.score, 2)
        self.assertEqual(game, self.game)

    def test_reward_persists_in_sql_and_event(self):
        store = SaveStore()
        try:
            combat = start_combat(self.game, seed=1, monsters=(RARE,))
            advance_round(combat, "guard", 1)
            with store.sessions.begin() as db:
                pack(db.get(GameSave, self.game.world_id), self.game, combat)
            with store.sessions() as db:
                _, restored = unpack(db.get(GameSave, self.game.world_id))
            self.assertEqual((restored.score, restored.events[0].points_awarded), (50, 50))
        finally:
            store.close()

    def test_scene_and_template_include_rule_reward(self):
        combat = start_combat(self.game, seed=1, monsters=(RARE,))
        opening = narrate(combat)
        self.assertIn("大伟丘", opening.encounter_narration)
        self.assertIn("50分", opening.encounter_narration)
        result = advance_round(combat, "guard", 1)
        facts = scene_facts(combat, result)
        self.assertEqual(facts["last_points_awarded"], 50)
        scene = narrate(combat, result)
        self.assertIn("规则计分：本轮+50分，累计50分", scene.result_narration)

    def test_model_failure_or_repeated_narration_cannot_award_again(self):
        combat = start_combat(self.game, seed=1, monsters=(RARE,))
        result = advance_round(combat, "strike", 1)
        def broken(_):
            raise TimeoutError()
        for _ in range(3):
            scene = narrate(combat, result, broken)
            self.assertIn("+50分", scene.result_narration)
        self.assertEqual((combat.score, len(combat.events)), (50, 1))

    def test_old_narration_receipt_does_not_invent_points(self):
        combat = start_combat(self.game, seed=1, monsters=(Monster("old"),))
        result = advance_round(combat, "strike", 1)
        facts = scene_facts(combat, result)
        facts.pop("last_points_awarded")
        facts.pop("next_bonus_points")
        self.assertNotIn("规则计分", narrate_facts(facts).result_narration)

    def test_lore_is_genshin_themed_and_marks_original_gameplay(self):
        text = "\n".join(row["text"] for row in load_lore())
        for word in ("提瓦特", "蒙德", "同人", "大伟丘", "50分", "不是原神官方"):
            self.assertIn(word, text)
        self.assertNotIn("浮空城邦", text)


class RareRewardApiTests(unittest.TestCase):
    def test_idempotent_reward_receipt_narration_and_version_checks(self):
        with TestClient(create_app()) as client:
            base, headers = "/api/v1/worlds/world-alice", {"Authorization": "Bearer demo-alice"}
            store = client.app.state.store
            game = GameState("alice", "world-alice", quest_stage="completed", has_permit=True)
            combat = start_combat(game, seed=1, monsters=(RARE,))
            with store.sessions.begin() as db:
                pack(db.get(GameSave, "world-alice"), game, combat)
            payload = {"action": "guard", "expected_round": 1, "expected_version": 0, "request_id": "rare1"}
            first = client.post(base + "/action", headers=headers, json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["state"]["combat"]["score"], 50)
            self.assertEqual(client.post(base + "/action", headers=headers, json=payload).json(), first.json())
            for _ in range(2):
                response = client.post(base + "/actions/rare1/narration", headers=headers, json={}).json()
                self.assertIn("+50分", response["result_narration"])
            stale = {**payload, "request_id": "rare2"}
            self.assertEqual(client.post(base + "/action", headers=headers, json=stale).status_code, 409)
            self.assertEqual(client.get(base + "/state", headers=headers).json()["combat"]["score"], 50)


if __name__ == "__main__":
    unittest.main()
