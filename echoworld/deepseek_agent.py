"""只读 NPC 的 DeepSeek 适配器；网络失败交给调用端降级，不自动重试。"""

import json
import os
from urllib.request import Request, urlopen


class DeepSeekAgentModel:
    def __init__(self, api_key: str, model: str = "deepseek-flash", timeout: float = 8):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls):
        key = os.getenv("ECHOWORLD_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if not key:
            return None
        return cls(key, os.getenv("ECHOWORLD_LLM_MODEL", "deepseek-flash"))

    def __call__(self, messages: list, tools: list) -> dict:
        payload = {"model": self.model, "messages": messages, "tools": tools,
                   "thinking": {"type": "disabled"}, "temperature": 0.2,
                   "max_tokens": 500}
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        # urllib的timeout是网络操作超时，不等于整个Agent的严格总截止时间。
        with urlopen(request, timeout=self.timeout) as response:
            body = json.load(response)
        if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
            raise ValueError("模型响应缺少choices")
        choice = body["choices"][0]
        if not isinstance(choice, dict):
            raise ValueError("模型响应格式错误")
        if choice.get("finish_reason") not in ("stop", "tool_calls"):
            raise ValueError("模型输出未完整结束")
        return choice["message"]
