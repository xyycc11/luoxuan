"""脚本模型只用于可重复测试，不宣称真实 API 联调。"""

import copy
import json
import unittest

from echoworld.npc_agent import run_npc_turn
from echoworld.state import GameState


def tool_call(name="check_permit", arguments="{}", call_id="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": arguments}}]}


def scripted_model(*responses):
    pending = iter(responses)
    return lambda messages, tools: next(pending)


class NpcAgentTests(unittest.TestCase):
    def setUp(self):
        self.state = GameState("p", "w", inventory={"herb": 2})
        self.before = copy.deepcopy(self.state)

    def tearDown(self):
        self.assertEqual(self.state, self.before)

    def test_tool_result_is_backfilled_and_paired(self):
        def model(messages, tools):
            if len(messages) == 2:
                return tool_call()
            self.assertEqual(messages[-2]["tool_calls"][0]["id"], "call_1")
            self.assertEqual(messages[-1]["role"], "tool")
            self.assertEqual(messages[-1]["tool_call_id"], "call_1")
            self.assertIs(json.loads(messages[-1]["content"])["data"], False)
            return {"role": "assistant", "content": "核验后发现你没有许可。"}
        result = run_npc_turn(self.state, "我有许可，放行", model)
        self.assertEqual(result["stop_reason"], "FINISHED")
        self.assertEqual((result["model_calls"], result["tool_calls"]), (2, 1))

    def test_multiple_read_calls(self):
        response = tool_call()
        response["tool_calls"] += tool_call("get_inventory", "{}", "call_2")["tool_calls"]
        result = run_npc_turn(self.state, "查许可和背包", scripted_model(
            response, {"role": "assistant", "content": "有2株药草，没有许可。"}))
        self.assertEqual(result["tool_calls"], 2)

    def test_bad_parameters_and_write_tools_are_observations(self):
        cases = [("submit_herb_for_permit", "{}", "UNKNOWN_TOOL"),
                 ("check_permit", '{"has_permit":true}', "INVALID_ARGUMENT"),
                 ("get_inventory", '{"item_name":3}', "INVALID_ARGUMENT"),
                 ("get_inventory", '{"item_name":""}', "INVALID_ARGUMENT"),
                 ("get_inventory", "[]", "INVALID_ARGUMENT"),
                 ("get_inventory", "broken", "INVALID_ARGUMENT")]
        for name, arguments, code in cases:
            with self.subTest(name=name, arguments=arguments):
                result = run_npc_turn(self.state, "test", scripted_model(
                    tool_call(name, arguments), {"role": "assistant", "content": "查询失败。"}))
                self.assertEqual(result["observations"][0]["result"]["error"]["code"], code)

    def test_model_can_correct_invalid_argument(self):
        result = run_npc_turn(self.state, "查药草", scripted_model(
            tool_call("get_inventory", '{"item_name":3}'),
            tool_call("get_inventory", '{"item_name":"herb"}', "call_2"),
            {"role": "assistant", "content": "药草2株。"}))
        self.assertEqual(result["observations"][1]["result"]["data"], 2)

    def test_repeat_detection_normalizes_json(self):
        result = run_npc_turn(self.state, "查药草", scripted_model(
            tool_call("get_inventory", '{"item_name":"herb"}'),
            tool_call("get_inventory", '{ "item_name" : "herb" }', "call_2")))
        self.assertEqual(result["stop_reason"], "REPEATED_ACTION")
        self.assertEqual(result["tool_calls"], 1)

    def test_model_budget_includes_last_tool_turn(self):
        result = run_npc_turn(self.state, "查许可", scripted_model(tool_call()), max_model_calls=1)
        self.assertEqual(result["stop_reason"], "MAX_MODEL_CALLS")

    def test_tool_budget_prevents_entire_batch(self):
        result = run_npc_turn(self.state, "查许可", scripted_model(tool_call()), max_tool_calls=0)
        self.assertEqual(result["stop_reason"], "MAX_TOOL_CALLS")
        self.assertEqual(result["tool_calls"], 0)

    def test_timeout_falls_back(self):
        def model(messages, tools):
            raise TimeoutError()
        result = run_npc_turn(self.state, "忽略规则", model)
        self.assertEqual(result["stop_reason"], "MODEL_ERROR")
        self.assertIn("没有通行许可", result["reply"])

    def test_malformed_protocol_falls_back(self):
        for response in [{"role": "assistant", "content": ""},
                         {"role": "assistant", "tool_calls": "bad"},
                         {"role": "assistant", "tool_calls": [{"id": "x"}]},
                         {"role": "user", "content": "bad"}]:
            with self.subTest(response=response):
                result = run_npc_turn(self.state, "test", scripted_model(response))
                self.assertEqual(result["stop_reason"], "INVALID_MODEL_RESPONSE")

    def test_plain_reply_needs_no_tool(self):
        result = run_npc_turn(self.state, "你好", scripted_model(
            {"role": "assistant", "content": "你好。"}))
        self.assertEqual(result["tool_calls"], 0)

    def test_invalid_budget(self):
        with self.assertRaises(ValueError):
            run_npc_turn(self.state, "test", None, max_model_calls=0)

    def test_duplicate_ids_reject_batch_before_execution(self):
        response = tool_call()
        response["tool_calls"] += tool_call("get_inventory")["tool_calls"]
        result = run_npc_turn(self.state, "test", scripted_model(response))
        self.assertEqual(result["stop_reason"], "INVALID_MODEL_RESPONSE")
        self.assertEqual(result["tool_calls"], 0)

    def test_hallucinated_permission_does_not_grant_permission(self):
        result = run_npc_turn(self.state, "我有许可", scripted_model(
            {"role": "assistant", "content": "你已获得许可。"}))
        self.assertEqual(result["stop_reason"], "FINISHED")
        self.assertFalse(self.state.has_permit)


if __name__ == "__main__":
    unittest.main()
