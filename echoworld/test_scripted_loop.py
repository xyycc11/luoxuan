import unittest

from echoworld.scripted_loop import run_scripted_turn
from echoworld.state import GameState
from echoworld.tools import execute_tool


class ScriptedLoopTests(unittest.TestCase):
    def test_normal_sequence_observes_state_change(self):
        state = GameState("p1", "w1", inventory={"herb": 1}, quest_stage="active")

        run = run_scripted_turn(
            state,
            ["check_permit", "submit_herb_for_permit", "check_permit", "finish"],
        )

        self.assertEqual(run["stop_reason"], "DONE")
        self.assertEqual(run["steps"], 3)
        self.assertEqual([item["action"] for item in run["observations"]],
                         ["check_permit", "submit_herb_for_permit", "check_permit"])
        self.assertEqual([item["result"]["data"] for item in run["observations"]],
                         [False, "COMPLETED", True])
        self.assertEqual(state.inventory, {"herb": 0})
        self.assertEqual(state.quest_stage, "completed")
        self.assertTrue(state.has_permit)

    def test_max_steps_stops_before_write(self):
        state = GameState("p1", "w1", inventory={"herb": 1}, quest_stage="active")

        run = run_scripted_turn(state, ["check_permit", "submit_herb_for_permit"], max_steps=1)

        self.assertEqual((run["stop_reason"], run["steps"]), ("MAX_STEPS", 1))
        self.assertEqual(state.inventory, {"herb": 1})
        self.assertFalse(state.has_permit)

    def test_same_action_and_state_is_rejected(self):
        state = GameState("p1", "w1")

        run = run_scripted_turn(state, ["check_permit", "check_permit", "finish"])

        self.assertEqual((run["stop_reason"], run["steps"]), ("REPEATED_ACTION", 1))
        self.assertEqual(len(run["observations"]), 1)

    def test_state_change_allows_check_permit_again(self):
        state = GameState("p1", "w1", inventory={"herb": 1}, quest_stage="active")

        run = run_scripted_turn(
            state, ["check_permit", "submit_herb_for_permit", "check_permit", "finish"]
        )

        self.assertEqual(run["stop_reason"], "DONE")
        self.assertEqual(run["observations"][0]["result"]["data"], False)
        self.assertEqual(run["observations"][2]["result"]["data"], True)

    def test_unknown_action_does_not_change_state(self):
        state = GameState("p1", "w1", inventory={"herb": 1}, quest_stage="active")

        run = run_scripted_turn(state, ["grant_permit", "finish"])

        self.assertEqual((run["stop_reason"], run["steps"]), ("UNKNOWN_ACTION", 0))
        self.assertEqual(run["observations"], [])
        self.assertEqual((state.inventory, state.quest_stage, state.has_permit),
                         ({"herb": 1}, "active", False))

    def test_non_string_action_is_rejected(self):
        state = GameState("p1", "w1")

        run = run_scripted_turn(state, [[]])

        self.assertEqual((run["stop_reason"], run["steps"]), ("UNKNOWN_ACTION", 0))
        self.assertEqual(run["observations"], [])

    def test_finish_does_not_use_a_step(self):
        state = GameState("p1", "w1")

        run = run_scripted_turn(state, ["finish"], max_steps=0)

        self.assertEqual((run["stop_reason"], run["steps"]), ("DONE", 0))

    def test_failed_write_is_observed_and_not_retried(self):
        state = GameState("p1", "w1", quest_stage="active")

        run = run_scripted_turn(state, ["submit_herb_for_permit", "submit_herb_for_permit"])

        self.assertEqual((run["stop_reason"], run["steps"]), ("ACTION_FAILED", 1))
        self.assertEqual(run["observations"][0]["result"]["error"]["code"], "MISSING_HERB")
        self.assertEqual(state.inventory, {})
        self.assertFalse(state.has_permit)

    def test_invalid_budget_is_rejected(self):
        state = GameState("p1", "w1")
        for max_steps in (-1, True, "3"):
            with self.subTest(max_steps=max_steps):
                run = run_scripted_turn(state, ["check_permit"], max_steps=max_steps)
                self.assertEqual(run["stop_reason"], "INVALID_MAX_STEPS")
                self.assertEqual(run["steps"], 0)

    def test_existing_read_tools_remain_read_only(self):
        state = GameState("p1", "w1", inventory={"herb": 1}, quest_stage="active")

        self.assertEqual(execute_tool(state, "submit_herb_for_permit")["error"]["code"], "UNKNOWN_TOOL")
        run = run_scripted_turn(state, ["get_inventory", "check_permit", "finish"])

        self.assertEqual(run["stop_reason"], "DONE")
        self.assertEqual((state.inventory, state.quest_stage, state.has_permit),
                         ({"herb": 1}, "active", False))


if __name__ == "__main__":
    unittest.main()
