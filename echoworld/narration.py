"""战斗叙事：规则给事实，模型只写画面和合法选项的文案。"""

import json
import os
from copy import deepcopy
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .combat import CombatState, TurnResult, combat_preview, uses_focus


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
    preview = combat_preview(state)
    return {
        "setting": "提瓦特·蒙德附近的风之试炼塔（项目原创同人副本，非官方剧情）",
        "outcome": result.code if result is not None else "COMBAT_STARTED",
        "last_round": last_event.round_no if last_event else None,
        "last_monster": last_event.monster_id if last_event else None,
        "last_choice": last_event.choice_id if last_event else None,
        "last_points_awarded": last_event.points_awarded if last_event else 0,
        "status": state.status,
        "score": state.score,
        "next_round": state.round_no if state.status == "active" else None,
        "next_monster": state.current_monster.monster_id if preview["phase"] == "combat" else None,
        "next_bonus_points": state.current_monster.bonus_points if preview["phase"] == "combat" else 0,
        "allowed_choices": [row["choice_id"] for row in preview["options"]],
        "ruleset": state.ruleset,
        "focus": preview["focus"],
        "rule_hint": preview["hint"],
        "rule_options": preview["options"],
        "last_stance": last_event.stance if last_event else None,
        "last_focus_before": last_event.focus_before if last_event else None,
        "last_focus_after": last_event.focus_after if last_event else None,
        "phase": preview["phase"],
        "blessing_name": preview["blessing_name"],
        "blessing_description": preview["blessing_description"],
        "combo_ready": preview["combo_ready"],
        "last_blessing_bonus_points": last_event.blessing_bonus_points if last_event else 0,
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
        result = f"第10轮通过{facts['last_monster']}的遭遇，你成功通关。"
    elif outcome == "ROUND_RESOLVED":
        result = f"第{facts['last_round']}轮对{facts['last_monster']}选择了{facts['last_choice']}。"
    elif outcome == "BLESSING_CHOSEN":
        result = f"你选择了【{facts['blessing_name']}】，本局打法由此改变：{facts['blessing_description']}"
    else:
        result = ""
    if facts["status"] == "active":
        encounter = ("塔门前的符文石亮起：两道不同的祝福等待你的选择。"
                     if facts.get("phase") == "blessing" else
                     f"风之试炼塔第{facts['next_round']}轮，{facts['next_monster']}出现了。")
        if uses_focus(facts.get("ruleset")):
            encounter += "\n" + facts["rule_hint"]
            encounter += f" 当前专注：{facts['focus']}/3。"
        elif facts.get("next_bonus_points", 0):
            encounter += f"稀有惊喜！完成本轮攻击或防御可获得{facts['next_bonus_points']}分。"
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
            "你是原神主题同人文字游戏叙事者，场景在提瓦特·蒙德附近的风之试炼塔。"
            "塔与许可任务是项目原创副本，不冒充官方剧情；可以写风、元素与冒险的画面。"
            "只依据用户消息中的已结算事实写画面，不改变怪物、轮次、分数、结果。"
            "大伟丘为本项目非致死稀有遭遇，奖励以last_points_awarded为准，不能自定分数或掉落。"
            "专注与架势是本项目原创策略计分机制，必须服从rule_hint和rule_options；防御不免除禁忌怪死亡。"
            "phase为blessing时尚未揭晓怪物，只写塔门符文选择，不虚构敌人。"
            "本局祝福只能描述，不能更换/增发/叠加；收益严格取已结算事实。"
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
            return _with_score_line(_validated_scene(model(deepcopy(facts)), facts), facts)
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError, OSError):
            pass
    return _with_score_line(template_scene(facts), facts)


def _with_score_line(scene, facts):
    # 奖励行取已提交的回执事实，不让模型自由文案代替规则计分。
    if uses_focus(facts.get("ruleset")):
        # 模型可写气氛，但选项数值和效果必须来自规则，不能说“防御复活”。
        options = tuple(ChoiceText(row["choice_id"], row["title"], row["description"])
                        for row in facts.get("rule_options", []))
        encounter = scene.encounter_narration
        if facts["status"] == "active" and scene.source == "model":
            encounter += f"\n规则提示：{facts['rule_hint']} 当前专注：{facts['focus']}/3。"
        scene = replace(scene, encounter_narration=encounter, options=options)
    if facts.get("last_round") is None or "last_points_awarded" not in facts:
        return scene  # 旧回执不猜测/补发奖励，保持可读取。
    points = facts["last_points_awarded"]
    line = f"规则计分：本轮+{points}分，累计{facts['score']}分。"
    if facts.get("last_focus_before") is not None:
        line += f" 专注：{facts['last_focus_before']}→{facts['last_focus_after']}/3。"
    if facts.get("last_blessing_bonus_points", 0):
        line += f" 已含【{facts['blessing_name']}】额外+{facts['last_blessing_bonus_points']}分。"
    return replace(scene, result_narration=scene.result_narration + "\n" + line)
