"""十回合爬塔的确定性规则内核；模型以后只负责叙事。"""

from dataclasses import dataclass, field
from random import Random

from .state import GameState
from .blessings import BLESSINGS, blessing_effect, offer_blessings


MAX_ROUNDS = 10
CHOICES = ("strike", "guard")
CLASSIC_RULESET = "classic_v1"
TACTICAL_RULESET = "tactics_v1"
BLESSED_RULESET = "blessings_v1"
MAX_FOCUS = 3


def uses_focus(ruleset: str) -> bool:
    return ruleset in {TACTICAL_RULESET, BLESSED_RULESET}


@dataclass(frozen=True)
class Monster:
    monster_id: str
    fatal: bool = False
    weight: int = 1
    bonus_points: int = 0  # 完成合法回合的固定奖励；与普通攻击分互斥。

    def __post_init__(self):
        if not isinstance(self.monster_id, str) or not self.monster_id.strip():
            raise ValueError("monster_id must be non-empty text")
        if type(self.fatal) is not bool or type(self.weight) is not int or self.weight <= 0:
            raise ValueError("monster fatal/weight is invalid")
        if type(self.bonus_points) is not int or self.bonus_points < 0 or (self.fatal and self.bonus_points):
            raise ValueError("bonus must be non-negative and only for non-fatal encounters")


DEFAULT_MONSTERS = (
    Monster("丘丘人", weight=30),
    Monster("木盾丘丘暴徒", weight=20),
    Monster("火史莱姆", weight=15),
    Monster("风丘丘萨满", weight=10),
    Monster("遗迹守卫", weight=8),
    Monster("大伟丘", weight=1, bonus_points=50),
    # 即死是本项目同人玩法，不是对原神官方战斗机制的描述。
    Monster("遗迹重机", fatal=True, weight=8),
    Monster("深渊咏者", fatal=True, weight=8),
)


@dataclass(frozen=True)
class TurnEvent:
    round_no: int
    monster_id: str
    choice_id: str
    fatal: bool
    points_awarded: int = 0  # 旧存档事件缺此字段时仍可读取。
    stance: str | None = None
    focus_before: int | None = None
    focus_after: int | None = None
    blessing_bonus_points: int = 0
    combo_before: bool | None = None
    combo_after: bool | None = None


@dataclass
class CombatState:
    player_id: str
    world_id: str
    seed: int
    monsters: tuple[Monster, ...]
    round_no: int = 1
    status: str = "active"
    score: int = 0
    current_monster: Monster | None = None
    events: list[TurnEvent] = field(default_factory=list)
    # 缺少新字段的旧JSON继续按经典规则结算，不追溯改分。
    ruleset: str = CLASSIC_RULESET
    focus: int = 2
    blessing_options: tuple[str, ...] = ()
    blessing: str | None = None
    combo_ready: bool = False

    def __post_init__(self):
        if self.ruleset not in {CLASSIC_RULESET, TACTICAL_RULESET, BLESSED_RULESET}:
            raise ValueError("unknown combat ruleset")
        if type(self.focus) is not int or not 0 <= self.focus <= MAX_FOCUS:
            raise ValueError("focus must be an integer between 0 and 3")
        if type(self.combo_ready) is not bool or not isinstance(self.blessing_options, (tuple, list)):
            raise ValueError("invalid blessing state")
        self.blessing_options = tuple(self.blessing_options)  # SQL JSON数组恢复为元组。
        if self.ruleset == BLESSED_RULESET:
            if any(type(key) is not str or key not in BLESSINGS for key in self.blessing_options):
                raise ValueError("unknown offered blessing")
            if len(self.blessing_options) != 2 or len(set(self.blessing_options)) != 2:
                raise ValueError("blessed mode requires two distinct offers")
            if self.blessing is not None and (type(self.blessing) is not str or self.blessing not in self.blessing_options):
                raise ValueError("selected blessing was not offered")
            if self.combo_ready and self.blessing != "boon_wind":
                raise ValueError("only wind can store a combo")
        elif self.blessing_options or self.blessing is not None or self.combo_ready:
            raise ValueError("legacy modes do not use blessings")


@dataclass(frozen=True)
class TurnResult:
    code: str
    event: TurnEvent | None = None


def _draw_monster(seed: int, round_no: int, monsters: tuple[Monster, ...]) -> Monster:
    # 每一轮只依赖固定seed和轮次；无效选择不会消耗随机数或换怪。
    rng = Random(seed + round_no)
    return rng.choices(monsters, weights=[monster.weight for monster in monsters], k=1)[0]


def start_combat(
    game: GameState,
    seed: int,
    monsters: tuple[Monster, ...] = DEFAULT_MONSTERS,
    *, ruleset: str = CLASSIC_RULESET,
) -> CombatState:
    """许可由服务端状态核验，玩家输入不能在这里伪造。"""

    if not game.has_permit:
        raise PermissionError("PERMIT_REQUIRED")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not monsters or any(type(monster.weight) is not int or monster.weight <= 0 for monster in monsters):
        raise ValueError("monsters must have positive integer weights")
    offers = offer_blessings(seed) if ruleset == BLESSED_RULESET else ()
    state = CombatState(game.player_id, game.world_id, seed, tuple(monsters), ruleset=ruleset,
                        blessing_options=offers)
    state.current_monster = _draw_monster(seed, 1, state.monsters)
    return state


def _stance(state: CombatState) -> str:
    monster = state.current_monster
    if monster.fatal:
        return "fatal"
    if monster.bonus_points:
        return "rare"
    if state.ruleset == CLASSIC_RULESET:
        return "classic"
    # 独立随机源：架势不消费/改变原来的怪物抽取序列，预览也不改状态。
    return Random(f"echoworld:{TACTICAL_RULESET}:{state.seed}:{state.round_no}").choice(
        ("opening", "pressure"))


@dataclass(frozen=True)
class ChoiceEffect:
    points: int
    focus_after: int
    stance: str
    combo_after: bool
    bonus: int = 0


def _choice_effect(state: CombatState, choice_id: str) -> ChoiceEffect:
    """预览与结算共用：基础效果→可选祝福，整个过程不修改状态。"""
    monster, stance = state.current_monster, _stance(state)
    if monster.fatal:
        return ChoiceEffect(0, state.focus, stance, state.combo_ready)
    if monster.bonus_points:
        return ChoiceEffect(monster.bonus_points, state.focus, stance, state.combo_ready)
    if stance == "classic":
        return ChoiceEffect(1 if choice_id == "strike" else 0, state.focus, stance, False)
    if choice_id == "guard":
        points, after = (1 if stance == "pressure" else 0), min(MAX_FOCUS, state.focus + 1)
    else:
        cost, reward = (1, 3) if stance == "opening" else (2, 4)
        points, after = (reward, state.focus - cost) if state.focus >= cost else (0, state.focus)
    bonus, combo = blessing_effect(state.blessing, stance, choice_id, points, state.combo_ready)
    return ChoiceEffect(points + bonus, after, stance, combo, bonus)


def choose_blessing(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """显式二选一：只改本局祝福，不结算敌人/加分/推进轮次。"""
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if state.ruleset != BLESSED_RULESET:
        return TurnResult("BLESSINGS_NOT_SUPPORTED")
    if state.blessing is not None:
        return TurnResult("BLESSING_ALREADY_CHOSEN")
    if state.round_no != 1 or state.events:
        return TurnResult("BLESSING_TOO_LATE")
    if choice_id not in state.blessing_options:
        return TurnResult("BLESSING_NOT_OFFERED")
    state.blessing = choice_id
    return TurnResult("BLESSING_CHOSEN")


def combat_preview(state: CombatState) -> dict:
    """玩家可见的当前规则提示；不暴露seed/未来怪物，不进行结算。"""
    tactical = uses_focus(state.ruleset)
    blessing = BLESSINGS.get(state.blessing)
    preview = {"ruleset": state.ruleset, "focus": state.focus if tactical else None,
               "max_focus": MAX_FOCUS if tactical else None, "stance": None,
               "hint": "本场试炼已结束。", "options": [], "phase": "finished",
               "blessing_name": blessing.name if blessing else None,
               "blessing_description": blessing.description if blessing else None,
               "combo_ready": state.combo_ready}
    if state.status != "active":
        return preview
    if state.ruleset == BLESSED_RULESET and state.blessing is None:
        preview.update(phase="blessing", hint="符文石亮起两道光：本局只选一种祝福，不消耗轮次，也不改变怪物概率。")
        preview["options"] = [{"choice_id": key, "title": BLESSINGS[key].name,
                                "description": BLESSINGS[key].description,
                                "points_awarded": 0, "focus_after": state.focus}
                               for key in state.blessing_options]
        return preview
    if state.current_monster is None:
        raise RuntimeError("active combat has no monster")
    stance = _stance(state)
    hints = {
        "opening": "破绽显露：花1专注攻击可得3分；也可防御蓄力，准备下一轮。",
        "pressure": "怪物蓄势：防御得1分并回1专注；花2专注抢攻可得4分。",
        "rare": f"稀有惊喜！完成攻击或防御都恰好+{state.current_monster.bonus_points}分，不消耗专注。",
        "fatal": "禁忌遭遇：按本项目即死规则，攻击和防御都会结束爬塔，防御不能免死。",
        "classic": "经典旧规则：攻击+1分，防御+0分；本场不使用专注。",
    }
    preview.update(phase="combat", stance=stance, hint=hints[stance])
    if state.combo_ready:
        preview["hint"] += " 风之余韵已蓄势：下一次成功普通攻击额外+2；稀有/即死怪不触发。"
    for choice_id, title in (("strike", "攻击"), ("guard", "防御")):
        effect = _choice_effect(state, choice_id)
        points, after = effect.points, effect.focus_after
        if stance == "fatal":
            description = "结束爬塔，+0分；此遭遇无法通过防御躲避。"
        elif tactical:
            description = f"本轮+{points}分；专注{state.focus}→{after}/{MAX_FOCUS}。"
            if choice_id == "strike" and stance in {"opening", "pressure"} and points == 0:
                description += "专注不足，攻击不得分；建议先蓄力。"
            if effect.bonus:
                description += f"已含祝福额外+{effect.bonus}分。"
            if state.blessing == "boon_wind" and choice_id == "guard" and stance in {"opening", "pressure"}:
                description += "为下一次成功普通攻击蓄势+2，不叠加。"
        else:
            description = f"本轮+{points}分。"
        preview["options"].append({"choice_id": choice_id, "title": title,
                                   "description": description, "points_awarded": points,
                                   "focus_after": after if tactical else None,
                                   "blessing_bonus_points": effect.bonus})
    return preview


def advance_round(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """验证后结算一次；失败不改状态，死亡优先于第十轮通关。"""

    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if choice_id not in CHOICES:
        return TurnResult("INVALID_CHOICE")
    if state.ruleset == BLESSED_RULESET and state.blessing is None:
        return TurnResult("BLESSING_REQUIRED")

    monster = state.current_monster
    if monster is None:
        raise RuntimeError("active combat has no monster")
    effect = _choice_effect(state, choice_id)
    points, after, stance = effect.points, effect.focus_after, effect.stance
    tactical = uses_focus(state.ruleset)
    event = TurnEvent(state.round_no, monster.monster_id, choice_id, monster.fatal, points,
                      stance if tactical else None, state.focus if tactical else None,
                      after if tactical else None, effect.bonus,
                      state.combo_ready if state.ruleset == BLESSED_RULESET else None,
                      effect.combo_after if state.ruleset == BLESSED_RULESET else None)
    state.events.append(event)
    if monster.fatal:
        state.status = "dead"
        return TurnResult("PLAYER_DEAD", event)

    state.focus = after
    state.combo_ready = effect.combo_after
    state.score += points
    if state.round_no == MAX_ROUNDS:
        state.status = "cleared"
        return TurnResult("TOWER_CLEARED", event)

    state.round_no += 1
    state.current_monster = _draw_monster(state.seed, state.round_no, state.monsters)
    return TurnResult("ROUND_RESOLVED", event)
