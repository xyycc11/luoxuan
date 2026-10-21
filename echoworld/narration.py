"""战斗叙事：规则给事实，模型只写画面和合法选项的文案。"""

import json
import os
from copy import deepcopy
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .combat import CombatState, TurnResult, combat_preview, uses_focus
from .expedition import camp_effect
from .relics import RELICS
from .routes import ROUTE_NAMES
from .cards import CARDS


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
    camp = result.camp_event if result is not None else None
    relic = result.relic_event if result is not None else None
    route = result.route_event if result is not None else None
    draft = result.card_draft if result is not None else None
    preview = combat_preview(state)
    return {
        "setting": "提瓦特·蒙德附近的风之试炼塔（项目原创同人副本，非官方剧情）",
        "outcome": result.code if result is not None else "COMBAT_STARTED",
        "last_round": last_event.round_no if last_event else None,
        "last_monster": last_event.monster_id if last_event else None,
        "last_choice": last_event.choice_id if last_event else None,
        "last_card": CARDS[last_event.card_id].name if last_event and last_event.card_id else None,
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
        "last_relic_bonus_points": last_event.relic_bonus_points if last_event else 0,
        "last_relic_names": [RELICS[key].name for key in last_event.relic_triggers] if last_event else [],
        "last_route_bonus_points": last_event.route_bonus_points if last_event else 0,
        "routes": preview["routes"],
        "route_result": ({"title": ROUTE_NAMES[route.choice_id], "focus_before": route.focus_before,
                          "focus_after": route.focus_after} if route else None),
        "relics": preview["relics"],
        "deck": preview["deck"],
        "card_draft_result": ({"title": "跳过选牌" if draft.choice_id == "draft_skip" else CARDS[draft.choice_id.replace("draft_", "card_", 1)].name,
                               "deck_size": len(state.cards.deck)} if draft else None),
        "relic_result": relic_view_for_fact(relic),
        "blessing_level": preview["blessing_level"],
        "chapter": preview["chapter"],
        "camp_result": ({"title": camp_effect(camp.choice_id, camp.focus_before, camp.level_before)["title"],
                         "points": camp.points_awarded, "focus_before": camp.focus_before,
                         "focus_after": camp.focus_after, "level_before": camp.level_before,
                         "level_after": camp.level_after} if camp else None),
    }


def relic_view_for_fact(event):
    if event is None:
        return None
    return {"name": RELICS[event.choice_id].name, "description": RELICS[event.choice_id].description}


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
        choice = {"strike": "攻击", "guard": "防御"}.get(facts["last_choice"], facts["last_choice"])
        result = f"第{facts['last_round']}轮，你对{facts['last_monster']}选择了{choice}。"
    elif outcome == "BLESSING_CHOSEN":
        result = f"你选择了【{facts['blessing_name']}】，本局打法由此改变：{facts['blessing_description']}"
    elif outcome == "CAMP_CHOSEN":
        result = "你在营地作出了选择，下一段石阶在风中显现。"
    elif outcome == "RELIC_CHOSEN":
        result = "你收好一件遗物，让它与本局祝福一同奏响回声。"
    elif outcome == "ROUTE_CHOSEN":
        result = "你在分岔路标前作出了选择，下一段旅程即将揭晓。"
    elif outcome == "CARD_DRAFTED":
        result = "你整理好招式牌组，准备踏上下一段石阶。"
    else:
        result = ""
    if facts["status"] == "active":
        if facts.get("phase") == "blessing":
            encounter = "塔门前的符文石亮起：两道不同的祝福等待你的选择。"
        elif facts.get("phase") == "camp":
            encounter = f"你抵达【{facts['chapter']['title']}】前的休整处。下一轮的敌人尚未揭晓。"
        elif facts.get("phase") == "relic":
            encounter = "篝火旁浮现三件遗物：只能选一件，下一轮敌人尚未揭晓。"
        elif facts.get("phase") == "route":
            encounter = "风息小径与试锋险径在眼前分开。下一轮敌人尚未揭晓。"
        elif facts.get("phase") == "draft":
            encounter = "两张新招式摆在篝火旁：选一张，或者跳过，下一轮敌人尚未揭晓。"
        else:
            encounter = f"风之试炼塔第{facts['next_round']}轮，{facts['next_monster']}出现了。"
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
            "phase为camp时只描绘营地和合法取舍，不虚构下一轮敌人；营地不消耗轮次，效果以camp_result为准。"
            "phase为relic时只描绘遗物三选一，不揭晓下一轮敌人；不能增发、更换遗物，不产生即时分数或免死。"
            "phase为route时只描绘当前章节路线，不虚构未来敌人；选择只改变专注和契约，不能修改抽怪或发放未达标奖励。"
            "牌组模式每轮仅打一张合法手牌；不增发卡牌或多次出手，选牌可跳过；卡牌分数与专注仍以rule_options为准。"
            "本局祝福只能描述，不能更换/增发/叠加；远征仅能通过显式营地选择强化，收益严格取已结算事实。"
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
    if facts.get("card_draft_result"):
        draft = facts["card_draft_result"]
        line = f"选牌已记录：【{draft['title']}】，牌组共{draft['deck_size']}张。不加分、不耗专注或战斗轮次。"
        return replace(scene, result_narration=scene.result_narration + "\n" + line)
    if facts.get("route_result"):
        route = facts["route_result"]
        line = (f"路线已选：【{route['title']}】专注{route['focus_before']}→{route['focus_after']}/3。"
                "选择不加分、不消耗战斗轮次；挑战只按实际成功普通攻击结算，不免死。")
        return replace(scene, result_narration=scene.result_narration + "\n" + line)
    if facts.get("relic_result"):
        relic = facts["relic_result"]
        line = f"遗物已收下：【{relic['name']}】{relic['description']} 领取不加分、不消耗战斗轮次。"
        return replace(scene, result_narration=scene.result_narration + "\n" + line)
    if facts.get("camp_result"):
        camp = facts["camp_result"]
        line = (f"营地结算：【{camp['title']}】+{camp['points']}分，累计{facts['score']}分；"
                f"专注{camp['focus_before']}→{camp['focus_after']}/3；"
                f"祝福Lv.{camp['level_before']}→Lv.{camp['level_after']}。不消耗战斗轮次。")
        return replace(scene, result_narration=scene.result_narration + "\n" + line)
    if facts.get("last_round") is None or "last_points_awarded" not in facts:
        return scene  # 旧回执不猜测/补发奖励，保持可读取。
    points = facts["last_points_awarded"]
    line = f"规则计分：本轮+{points}分，累计{facts['score']}分。"
    if facts.get("last_card"):
        line += f" 本轮使用【{facts['last_card']}】，手牌全部弃置；不再结算其他手牌。"
    if facts.get("last_focus_before") is not None:
        line += f" 专注：{facts['last_focus_before']}→{facts['last_focus_after']}/3。"
    if facts.get("last_blessing_bonus_points", 0):
        line += f" 已含【{facts['blessing_name']}】额外+{facts['last_blessing_bonus_points']}分。"
    if facts.get("last_relic_names"):
        line += f" 遗物【{'、'.join(facts['last_relic_names'])}】已计入，额外+{facts['last_relic_bonus_points']}分。"
    if facts.get("last_route_bonus_points"):
        line += f" 章节挑战达标，额外+{facts['last_route_bonus_points']}分已含在本轮分数中，不重复领取。"
    return replace(scene, result_narration=scene.result_narration + "\n" + line)
