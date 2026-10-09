"""同一脚本模型驱动Loop/图，验证行为一致；不请求真实模型。"""

import copy
import unittest

from echoworld.agent_graph import run_graph_turn
from echoworld.agent_steps import initial_turn, model_step
from echoworld.npc_agent import run_npc_turn
from echoworld.state import GameState
from echoworld.test_npc_agent import scripted_model, tool_call


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.game = GameState("p", "w", inventory={"herb": 2})
        self.before = copy.deepcopy(self.game)

    def tearDown(self):
        self.assertEqual(self.game, self.before)

    def test_graph_and_loop_match_on_normal_path(self):
        responses = [tool_call(), {"role": "assistant", "content": "没有许可。"}]
        old = run_npc_turn(self.game, "查许可", scripted_model(*responses))
        result = run_graph_turn(self.game, "查许可", scripted_model(*responses))
        self.assertEqual(result.pop("node_trace"), ["model", "tools", "model", "finalize"])
        self.assertEqual(result, old)

    def test_repeat_detection_matches(self):
        result = run_graph_turn(self.game, "查许可", scripted_model(tool_call(), tool_call(call_id="call_2")))
        self.assertEqual(result["stop_reason"], "REPEATED_ACTION")
        self.assertEqual(result["tool_calls"], 1)

    def test_budgets(self):
        result = run_graph_turn(self.game, "test", scripted_model(tool_call()), max_model_calls=1)
        self.assertEqual(result["stop_reason"], "MAX_MODEL_CALLS")
        result = run_graph_turn(self.game, "test", scripted_model(tool_call()), max_tool_calls=0)
        self.assertEqual(result["stop_reason"], "MAX_TOOL_CALLS")

    def test_unavailable_model_is_explicit(self):
        result = run_graph_turn(self.game, "test", None)
        self.assertEqual(result["stop_reason"], "MODEL_UNAVAILABLE")
        self.assertEqual(result["model_calls"], 0)

    def test_timeout_falls_back(self):
        def timeout(messages, tools):
            raise TimeoutError()
        self.assertEqual(run_graph_turn(self.game, "test", timeout)["stop_reason"], "MODEL_ERROR")

    def test_node_update_does_not_mutate_input(self):
        turn = initial_turn("查许可")
        update = model_step(turn, scripted_model(tool_call()))
        self.assertEqual(len(turn["messages"]), 2)
        self.assertEqual(len(update["messages"]), 3)

    def test_forbidden_write_is_an_observation(self):
        result = run_graph_turn(self.game, "test", scripted_model(
            tool_call("submit_herb_for_permit"), {"role": "assistant", "content": "不允许。"}))
        self.assertEqual(result["observations"][0]["result"]["error"]["code"], "UNKNOWN_TOOL")
