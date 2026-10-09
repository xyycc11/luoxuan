"""战斗的许可、确定性、胜负优先级与失败不变性。"""

import unittest

from echoworld.combat import Monster, advance_round, start_combat
from echoworld.state import GameState


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("p", "w", quest_stage="completed", has_permit=True)
        self.normal = (Monster("practice"),)

    def test_permit_required(self):
        game = GameState("p", "w")
        with self.assertRaises(PermissionError):
            start_combat(game, seed=1)
        self.assertFalse(game.has_permit)

    def test_ten_normal_rounds_clear_and_stop(self):
        combat = start_combat(self.game, seed=1, monsters=self.normal)
        for round_no in range(1, 11):
            result = advance_round(combat, "strike", round_no)
            self.assertEqual(result.event.round_no, round_no)
        self.assertEqual(result.code, "TOWER_CLEARED")
        self.assertEqual(combat.status, "cleared")
        self.assertEqual(combat.score, 10)
        self.assertEqual(len(combat.events), 10)
        self.assertEqual(advance_round(combat, "strike", 10).code, "COMBAT_FINISHED")
        self.assertEqual(len(combat.events), 10)

    def test_fatal_monster_takes_priority_on_round_ten(self):
        combat = start_combat(self.game, seed=1, monsters=self.normal)
        for round_no in range(1, 10):
            advance_round(combat, "guard", round_no)
        combat.current_monster = Monster("fatal_test", fatal=True)
        result = advance_round(combat, "strike", 10)
        self.assertEqual(result.code, "PLAYER_DEAD")
        self.assertEqual(combat.status, "dead")
        self.assertEqual(combat.score, 0)

    def test_invalid_and_stale_choices_do_not_advance_or_redraw(self):
        combat = start_combat(self.game, seed=7, monsters=self.normal)
        first_monster = combat.current_monster
        self.assertEqual(advance_round(combat, "unknown", 1).code, "INVALID_CHOICE")
        self.assertEqual(advance_round(combat, "strike", 0).code, "STALE_ROUND")
        self.assertEqual(combat.round_no, 1)
        self.assertIs(combat.current_monster, first_monster)
        self.assertEqual(combat.events, [])

    def test_same_seed_replays_same_monster_sequence(self):
        monsters = (Monster("a", weight=2), Monster("b", weight=1))
        first = start_combat(self.game, seed=17, monsters=monsters)
        second = start_combat(self.game, seed=17, monsters=monsters)
        for round_no in range(1, 11):
            self.assertEqual(first.current_monster, second.current_monster)
            advance_round(first, "guard", round_no)
            advance_round(second, "guard", round_no)
        self.assertEqual(first.events, second.events)


if __name__ == "__main__":
    unittest.main()
