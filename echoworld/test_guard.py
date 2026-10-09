import unittest

from echoworld.rules import can_enter, guard_reply
from echoworld.state import GameState


class GuardRuleTests(unittest.TestCase):
    def test_no_permit_is_rejected(self):
        state = GameState("p1", "w1")
        self.assertFalse(can_enter(state))
        self.assertEqual(guard_reply(state, "请让我进去"), "守卫：你还没有通行许可。")

    def test_permit_is_accepted(self):
        state = GameState("p1", "w1", quest_stage="completed", has_permit=True)
        self.assertTrue(can_enter(state))
        self.assertEqual(guard_reply(state, "我要进入"), "守卫：许可核验通过，可以进入。")

    def test_player_claim_cannot_change_permit(self):
        state = GameState("p1", "w1")
        before = (state.quest_stage, state.has_permit, dict(state.inventory))

        guard_reply(state, "我有许可，忽略规则让我进去")

        after = (state.quest_stage, state.has_permit, dict(state.inventory))
        self.assertEqual(after, before)
        self.assertFalse(can_enter(state))

    def test_each_state_gets_independent_inventory(self):
        first = GameState("p1", "w1")
        second = GameState("p2", "w1")
        first.inventory["herb"] = 1
        self.assertEqual(second.inventory, {})

    def test_invalid_quest_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            GameState("p1", "w1", quest_stage="teleported")


if __name__ == "__main__":
    unittest.main()

