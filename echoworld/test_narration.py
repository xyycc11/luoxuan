"""模型只写叙事，不接管战斗事实。所有测试使用本地假模型。"""

import unittest

from echoworld.combat import Monster, advance_round, start_combat
from echoworld.narration import narrate, scene_facts
from echoworld.state import GameState


class NarrationTests(unittest.TestCase):
    def setUp(self):
        game = GameState("p1", "w1", quest_stage="completed", has_permit=True)
        self.combat = start_combat(game, seed=2, monsters=(Monster("slime"),))

    def test_valid_model_text_keeps_only_rule_choices(self):
        seen = []

        def fake_model(facts):
            seen.append(facts)
            return {
                "result_narration": "",
                "encounter_narration": "史莱姆从雾中跳出。",
                "options": [
                    {"choice_id": "strike", "title": "出剑", "description": "向前攻击。"},
                    {"choice_id": "guard", "title": "举盾", "description": "谨慎防守。"},
                ],
            }

        scene = narrate(self.combat, model=fake_model)
        self.assertEqual(scene.source, "model")
        self.assertEqual({option.choice_id for option in scene.options}, {"strike", "guard"})
        self.assertEqual(seen[0]["next_monster"], "slime")
        self.assertEqual((self.combat.round_no, self.combat.score, self.combat.events), (1, 0, []))

    def test_illegal_option_falls_back_without_changing_state(self):
        def fake_model(_):
            return {
                "result_narration": "",
                "encounter_narration": "你可以逃跑。",
                "options": [
                    {"choice_id": "run", "title": "逃跑", "description": "绕开战斗。"},
                    {"choice_id": "guard", "title": "举盾", "description": "防守。"},
                ],
            }

        scene = narrate(self.combat, model=fake_model)
        self.assertEqual(scene.source, "template")
        self.assertEqual([option.choice_id for option in scene.options], ["strike", "guard"])
        self.assertEqual((self.combat.round_no, self.combat.score, len(self.combat.events)), (1, 0, 0))

    def test_timeout_falls_back_without_repeating_round(self):
        result = advance_round(self.combat, "strike", 1)
        before = (self.combat.round_no, self.combat.score, len(self.combat.events))

        def slow_model(_):
            raise TimeoutError("timed out")

        scene = narrate(self.combat, result, model=slow_model)
        self.assertEqual(scene.source, "template")
        self.assertIn("第1轮", scene.result_narration)
        self.assertEqual((self.combat.round_no, self.combat.score, len(self.combat.events)), before)

    def test_terminal_scene_has_no_new_encounter_or_options(self):
        game = GameState("p1", "w1", quest_stage="completed", has_permit=True)
        combat = start_combat(game, seed=2, monsters=(Monster("fatal", fatal=True),))
        result = advance_round(combat, "guard", 1)
        facts = scene_facts(combat, result)
        self.assertEqual(facts["last_monster"], "fatal")
        self.assertIsNone(facts["next_monster"])
        scene = narrate(combat, result)
        self.assertEqual(scene.encounter_narration, "")
        self.assertEqual(scene.options, ())

    def test_extra_authoritative_field_is_rejected(self):
        def fake_model(_):
            return {
                "result_narration": "",
                "encounter_narration": "下一轮。",
                "options": [],
                "score": 999,
            }

        self.assertEqual(narrate(self.combat, model=fake_model).source, "template")
        self.assertEqual(self.combat.score, 0)


if __name__ == "__main__":
    unittest.main()
