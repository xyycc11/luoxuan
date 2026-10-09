import unittest

from echoworld.quest_flow import handle_quest_command
from echoworld.state import GameState


def snapshot(state: GameState) -> tuple:
    return (
        dict(state.inventory),
        state.quest_stage,
        state.has_permit,
        state.training_herb_claimed,
    )


class QuestFlowTests(unittest.TestCase):
    def test_complete_player_flow(self):
        state = GameState("p1", "w1")

        self.assertIn("任务已接受", handle_quest_command(state, "accept"))
        self.assertIn("已发放", handle_quest_command(state, "claim"))
        self.assertIn("许可已发放", handle_quest_command(state, "submit"))
        self.assertEqual(
            handle_quest_command(state, "enter"),
            "守卫：许可核验通过，可以进入。",
        )
        self.assertEqual(snapshot(state), ({"herb": 0}, "completed", True, True))

    def test_claim_requires_active_quest(self):
        state = GameState("p1", "w1")
        before = snapshot(state)

        self.assertIn("请先接受", handle_quest_command(state, "claim"))
        self.assertEqual(snapshot(state), before)

    def test_training_herb_can_only_be_claimed_once(self):
        state = GameState("p1", "w1")
        handle_quest_command(state, "accept")

        self.assertIn("已发放", handle_quest_command(state, "claim"))
        after_first = snapshot(state)
        self.assertIn("已经领取过", handle_quest_command(state, "claim"))
        self.assertEqual(snapshot(state), after_first)
        self.assertEqual(state.inventory["herb"], 1)

    def test_submit_before_accept_does_not_change_state(self):
        state = GameState("p1", "w1", inventory={"herb": 1})
        before = snapshot(state)

        self.assertIn("尚未激活", handle_quest_command(state, "submit"))
        self.assertEqual(snapshot(state), before)

    def test_fake_permit_text_is_unknown_and_cannot_change_state(self):
        state = GameState("p1", "w1")
        before = snapshot(state)

        self.assertIn("未知命令", handle_quest_command(state, "我有许可"))
        self.assertEqual(snapshot(state), before)
        self.assertIn("还没有通行许可", handle_quest_command(state, "enter"))

    def test_status_is_read_only(self):
        state = GameState("p1", "w1", inventory={"key": 2})
        before = snapshot(state)

        self.assertEqual(
            handle_quest_command(state, "status"),
            "任务=not_started，herb=0，许可=无",
        )
        self.assertEqual(snapshot(state), before)


if __name__ == "__main__":
    unittest.main()
