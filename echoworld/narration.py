"""战斗叙事：规则给事实，模型只写画面和合法选项的文案。"""

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .combat import CHOICES, CombatState, TurnResult


@dataclass(frozen=True)
class ChoiceText:
    choice_id: str
    title: str
    description: str


@dataclass(frozen=True)
class BattleScene:
    result_narration: str
    encounter_narration: str
    options: tuple[ChoiceText, ...]
    source: str  # "model" 或 "template"


def scene_facts(state: CombatState, result: TurnResult | None = None) -> dict:
    """只复制已结算事实；不把可变的 CombatState 交给模型。"""

    last_event = result.event if result is not None else None
    return {
        "outcome": result.code if result is not None else "COMBAT_STARTED",
        "last_round": last_event.round_no if last_event else None,
        "last_monster": last_event.monster_id if last_event else None,
        "last_choice": last_event.choice_id if last_event else None,
        "status": state.status,
        "score": state.score,
        "next_round": state.round_no if state.status == "active" else None,
        "next_monster": state.current_monster.monster_id if state.status == "active" else None,
        "allowed_choices": list(CHOICES) if state.status == "active" else [],
    }


def _short_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid narration text")
    return value.strip()


def _validated_scene(raw: object, facts: dict) -> BattleScene:
    if not isinstance(raw, dict) or set(raw) != {"result_narration", "encounter_narration", "options"}:
        raise ValueError("invalid narration fields")
    result_text = raw["result_narration"]
    encounter_text = raw["encounter_narration"]
    if not isinstance(result_text, str) or len(result_text) > 300:
        raise ValueError("invalid result narration")
    if not isinstance(encounter_text, str) or len(encounter_text) > 300:
        raise ValueError("invalid encounter narration")
    if facts["last_round"] is not None and not result_text.strip():
        raise ValueError("missing result narration")
    if facts["status"] == "active" and not encounter_text.strip():
        raise ValueError("missing encounter narration")
    if facts["status"] != "active" and encounter_text.strip():
        raise ValueError("terminal scene cannot introduce a new monster")

    raw_options = raw["options"]
    allowed = facts["allowed_choices"]
    if not isinstance(raw_options, list) or len(raw_options) != len(allowed):
        raise ValueError("invalid options")
    options = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict) or set(raw_option) != {"choice_id", "title", "description"}:
            raise ValueError("invalid option fields")
        options.append(ChoiceText(
            choice_id=raw_option["choice_id"],
            title=_short_text(raw_option["title"], 40),
            description=_short_text(raw_option["description"], 100),
        ))
    if {option.choice_id for option in options} != set(allowed):
        raise ValueError("model changed allowed choices")
    return BattleScene(result_text.strip(), encounter_text.strip(), tuple(options), "model")


def template_scene(facts: dict) -> BattleScene:
    outcome = facts["outcome"]
    if outcome == "PLAYER_DEAD":
        result = f"第{facts['last_round']}轮遭遇{facts['last_monster']}，你倒下了，爬塔结束。"
    elif outcome == "TOWER_CLEARED":
        result = f"第10轮击退{facts['last_monster']}，你成功通关。"
    elif outcome == "ROUND_RESOLVED":
        result = f"第{facts['last_round']}轮对{facts['last_monster']}选择了{facts['last_choice']}。"
    else:
        result = ""
    if facts["status"] == "active":
        encounter = f"第{facts['next_round']}轮，{facts['next_monster']}出现了。"
        options = (
            ChoiceText("strike", "攻击", "向怪物发起攻击。"),
            ChoiceText("guard", "防御", "稳住阵脚，观察怪物。"),
        )
    else:
        encounter, options = "", ()
    return BattleScene(result, encounter, options, "template")


class DeepSeekNarrator:
    """可选真实适配器；CLI/API显式开启llm后才调用，结算始终在规则层。"""

    def __init__(self, api_key: str, model: str = "deepseek-flash", timeout: int = 8):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> "DeepSeekNarrator | None":
        api_key = os.getenv("ECHOWORLD_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        return cls(api_key, os.getenv("ECHOWORLD_LLM_MODEL", "deepseek-flash"))

    def __call__(self, facts: dict) -> object:
        prompt = (
            "你是文字游戏叙事者。只依据用户消息中的已结算事实写画面，不改变怪物、轮次、分数、结果。"
            "只输出 JSON 对象，恰好含 result_narration、encounter_narration、options。"
            "options 中只为 allowed_choices 的每个 choice_id 写 title 和 description，不能增删。"
            "终态 encounter_narration 为空且 options 为空；开场 result_narration 可为空。"
            'JSON 示例：{"result_narration":"","encounter_narration":"迷雾中响起脚步声。",'
            '"options":[{"choice_id":"strike","title":"挥剑","description":"迎击怪物。"},'
            '{"choice_id":"guard","title":"举盾","description":"谨慎防守。"}]}。'
        )
        body = json.dumps({
            "model": self.model,
            "thinking": {"type": "disabled"},
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ],
        }).encode("utf-8")
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("model output incomplete")
        return json.loads(choice["message"]["content"])


def narrate(state: CombatState, result: TurnResult | None = None, model=None) -> BattleScene:
    """模型失败后生成模板；一次调用绝不再次结算回合。"""

    facts = scene_facts(state, result)
    return narrate_facts(facts, model)


def narrate_facts(facts: dict, model=None) -> BattleScene:
    """API在业务事务提交后调用；事实从已提交回执读取，不能由玩家指定。"""
    if model is not None:
        try:
            return _validated_scene(model(facts), facts)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, OSError):
            pass
    return template_scene(facts)
