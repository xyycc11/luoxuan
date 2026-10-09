"""请求协议的本地替身测试；不连接网络。"""

import io
import json
import unittest
from unittest.mock import patch

from echoworld.deepseek_agent import DeepSeekAgentModel
from echoworld.npc_agent import TOOL_SCHEMAS


class DeepSeekAdapterTests(unittest.TestCase):
    def test_request_protocol_and_response(self):
        body = {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "你好"}}]}
        with patch("echoworld.deepseek_agent.urlopen", return_value=io.BytesIO(
            json.dumps(body).encode())) as fake:
            result = DeepSeekAgentModel("test-placeholder")(
                [{"role": "user", "content": "你好"}], TOOL_SCHEMAS)
        request = fake.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["tools"], TOOL_SCHEMAS)
        self.assertEqual(fake.call_args.kwargs["timeout"], 8)
        self.assertEqual(result["content"], "你好")

    def test_incomplete_response_is_rejected(self):
        body = {"choices": [{"finish_reason": "length", "message": {}}]}
        with patch("echoworld.deepseek_agent.urlopen", return_value=io.BytesIO(
            json.dumps(body).encode())):
            with self.assertRaises(ValueError):
                DeepSeekAgentModel("test-placeholder")([], TOOL_SCHEMAS)

    def test_empty_choices_are_rejected(self):
        with patch("echoworld.deepseek_agent.urlopen", return_value=io.BytesIO(b'{"choices":[]}')):
            with self.assertRaises(ValueError):
                DeepSeekAgentModel("test-placeholder")([], TOOL_SCHEMAS)


if __name__ == "__main__":
    unittest.main()
