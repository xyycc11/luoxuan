"""十回合爬塔的确定性规则内核；模型以后只负责叙事。"""

from dataclasses import dataclass, field
from random import Random

from .state import GameState
from .elements import ELEMENTS_RULESET, ElementState, new_state as new_elements, prepare_floor
from .blessings import BLESSINGS, blessing_effect, offer_blessings
from .expedition import (EXPEDITION_RULESET, CAMP_ROUNDS, CAMP_CHOICES, CAMP_NAMES,
                         MAX_BLESSING_LEVEL, CampEvent, camp_effect, chapter_info, run_report, guard_comments)
from .relics import SPIRE_RULESET, RELICS, RelicEvent, offer_relics, relic_effect, relic_view
from .routes import (ROUTES_RULESET, ROUTE_ROUNDS, ROUTE_CHOICES, RouteEvent,
                     route_option, route_progress, route_bonus)
from .cards import (CARDS_RULESET, CARDS, STARTER, DRAFT_ROUNDS, DeckState, CardDraftEvent,
                    new_deck, base_effect, draw_hand, discard_hand, offer_cards, draft_options, deck_view)


MAX_ROUNDS = 10
CHOICES = ("strike", "guard")
CLASSIC_RULESET = "classic_v1"
TACTICAL_RULESET = "tactics_v1"
BLESSED_RULESET = "blessings_v1"
MAX_FOCUS = 3


def uses_focus(ruleset: str) -> bool:
    return ruleset in {TACTICAL_RULESET, BLESSED_RULESET, EXPEDITION_RULESET, SPIRE_RULESET, ROUTES_RULESET, CARDS_RULESET}


def uses_blessing(ruleset: str) -> bool:
    return ruleset in {BLESSED_RULESET, EXPEDITION_RULESET, SPIRE_RULESET, ROUTES_RULESET, CARDS_RULESET}


def uses_camp(ruleset: str) -> bool:
    return ruleset in {EXPEDITION_RULESET, SPIRE_RULESET, ROUTES_RULESET, CARDS_RULESET}


def uses_relics(ruleset: str) -> bool:
    return ruleset in {SPIRE_RULESET, ROUTES_RULESET, CARDS_RULESET}


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
    relic_bonus_points: int = 0
    relic_triggers: tuple[str, ...] = ()
    route_bonus_points: int = 0
    card_id: str | None = None

    def __post_init__(self):
        if self.card_id is not None and (type(self.card_id) is not str or self.card_id not in CARDS
                                        or CARDS[self.card_id].kind != self.choice_id):
            raise ValueError("invalid played card")
        if type(self.route_bonus_points) is not int or self.route_bonus_points not in {0, 3, 6}:
            raise ValueError("invalid route reward")
        object.__setattr__(self, "relic_triggers", tuple(self.relic_triggers))
        if (type(self.relic_bonus_points) is not int or self.relic_bonus_points < 0
                or len(set(self.relic_triggers)) != len(self.relic_triggers)
                or any(key not in RELICS for key in self.relic_triggers)):
            raise ValueError("invalid relic trigger record")


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
    blessing_level: int = 1
    camp_events: list[CampEvent] = field(default_factory=list)
    relic_events: list[RelicEvent] = field(default_factory=list)
    route_events: list[RouteEvent] = field(default_factory=list)
    cards: DeckState | None = None
    card_drafts: list[CardDraftEvent] = field(default_factory=list)
    elemental: ElementState | None = None

    def __post_init__(self):
        if self.ruleset not in {CLASSIC_RULESET, TACTICAL_RULESET, BLESSED_RULESET, EXPEDITION_RULESET, SPIRE_RULESET, ROUTES_RULESET, CARDS_RULESET, ELEMENTS_RULESET}:
            raise ValueError("unknown combat ruleset")
        if self.ruleset == ELEMENTS_RULESET:
            if not isinstance(self.elemental, ElementState):
                raise ValueError("element mode requires elemental state")
            if (type(self.round_no) is not int or not 1 <= self.round_no <= 10
                    or self.status not in {"active", "dead", "cleared"}
                    or (self.status == "cleared" and (self.round_no != 10 or self.elemental.enemy_hp != 0))):
                raise ValueError("invalid elemental progress")
            if self.elemental.camp_due and self.round_no not in {4, 7, 10}:
                raise ValueError("invalid elemental camp floor")
            e = self.elemental
            if (type(self.score) is not int or self.score != sum(row["points"] for row in e.log)
                    or any(row["floor"] > self.round_no for row in e.log + e.camps)
                    or (self.status == "active" and (e.hp == 0 or e.enemy_hp == 0))):
                raise ValueError("invalid elemental score or lifecycle")
            if ([row.round_no for row in self.events] != list(range(1, len(self.events)+1))
                    or len(self.events) != (10 if self.status == "cleared" else self.round_no-1)):
                raise ValueError("invalid elemental completed floors")
            expected_camp = (self.status == "active" and self.round_no in {4,7,10}
                             and not any(row["floor"] == self.round_no for row in e.camps))
            if e.camp_due != expected_camp:
                raise ValueError("elemental camp does not match current floor")
        elif self.elemental is not None:
            raise ValueError("legacy mode cannot carry elemental state")
        if type(self.focus) is not int or not 0 <= self.focus <= MAX_FOCUS:
            raise ValueError("focus must be an integer between 0 and 3")
        if type(self.combo_ready) is not bool or not isinstance(self.blessing_options, (tuple, list)):
            raise ValueError("invalid blessing state")
        self.blessing_options = tuple(self.blessing_options)  # SQL JSON数组恢复为元组。
        if type(self.blessing_level) is not int or not 1 <= self.blessing_level <= MAX_BLESSING_LEVEL:
            raise ValueError("invalid blessing level")
        if not isinstance(self.camp_events, list) or any(not isinstance(event, CampEvent) for event in self.camp_events):
            raise ValueError("invalid camp events")
        if not uses_camp(self.ruleset) and (self.blessing_level != 1 or self.camp_events):
            raise ValueError("legacy modes do not use camps or upgrades")
        if uses_camp(self.ruleset):
            rounds = [event.round_no for event in self.camp_events]
            if rounds != list(CAMP_ROUNDS[:len(rounds)]) or any(number > self.round_no for number in rounds):
                raise ValueError("camp history is not sequential")
            if self.blessing_level != 1 + sum(event.choice_id == "camp_forge" for event in self.camp_events):
                raise ValueError("blessing level does not match camp history")
            if self.camp_events and self.blessing is None:
                raise ValueError("camps require a chosen blessing")
        if uses_blessing(self.ruleset):
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
        if not isinstance(self.relic_events, list) or any(not isinstance(event, RelicEvent) for event in self.relic_events):
            raise ValueError("invalid relic events")
        if not uses_relics(self.ruleset) and self.relic_events:
            raise ValueError("legacy modes do not use relics")
        if uses_relics(self.ruleset):
            owned = []
            for index, event in enumerate(self.relic_events):
                if (index >= len(self.camp_events) or event.round_no != CAMP_ROUNDS[index]
                        or self.camp_events[index].round_no != event.round_no
                        or event.choice_id not in offer_relics(self.seed, event.round_no, owned)):
                    raise ValueError("relic history does not match offered rewards")
                owned.append(event.choice_id)
            remaining = len(self.camp_events) - len(self.relic_events)
            if remaining not in {0, 1}:
                raise ValueError("missing relic choices")
            if remaining and (self.round_no != self.camp_events[-1].round_no
                              or any(event.round_no == self.round_no for event in self.events)):
                raise ValueError("battle cannot precede a pending relic choice")
        if not isinstance(self.route_events, list) or any(not isinstance(event, RouteEvent) for event in self.route_events):
            raise ValueError("invalid route events")
        if self.ruleset != ROUTES_RULESET:
            if self.route_events or any(event.route_bonus_points for event in self.events):
                raise ValueError("legacy modes do not use routes")
        else:
            rounds = [event.round_no for event in self.route_events]
            if rounds != list(ROUTE_ROUNDS[:len(rounds)]) or any(number > self.round_no for number in rounds):
                raise ValueError("route history is not sequential")
            if self.route_events and self.blessing is None:
                raise ValueError("routes require a blessing")
            for event in self.route_events:
                if event.round_no != 1 and not any(row.round_no == event.round_no for row in self.relic_events):
                    raise ValueError("route cannot precede a relic")
            for event in self.events:
                if not any(row.round_no <= event.round_no <= min(10, row.round_no + 2) for row in self.route_events):
                    raise ValueError("battle cannot precede a route choice")
            # 奖励历史按同一纯函数重新核验，拒绝伪造或重复发奖的JSON。
            for index, event in enumerate(self.events):
                expected = route_bonus(self.route_events, self.events[:index], event.round_no,
                                       event.stance, event.choice_id,
                                       event.points_awarded > 0 and not event.fatal)
                if event.route_bonus_points != expected:
                    raise ValueError("route reward history is invalid")
        if not isinstance(self.card_drafts, list) or any(not isinstance(row, CardDraftEvent) for row in self.card_drafts):
            raise ValueError("invalid card drafts")
        if self.ruleset != CARDS_RULESET:
            if self.cards is not None or self.card_drafts or any(row.card_id is not None for row in self.events):
                raise ValueError("legacy mode cannot use cards")
        else:
            from collections import Counter
            if not isinstance(self.cards, DeckState):
                raise ValueError("card mode needs a deck")
            rounds = [row.round_no for row in self.card_drafts]
            if rounds != list(DRAFT_ROUNDS[:len(rounds)]) or any(number > self.round_no for number in rounds):
                raise ValueError("draft history is not sequential")
            added = []
            for row in self.card_drafts:
                key = row.choice_id.replace("draft_", "card_", 1)
                if not any(event.round_no == row.round_no for event in self.relic_events):
                    raise ValueError("draft cannot precede relic choice")
                if row.choice_id != "draft_skip":
                    if key not in offer_cards(self.seed, row.round_no):
                        raise ValueError("card reward not offered")
                    added.append(key)
            if Counter(self.cards.deck) != Counter((*STARTER, *added)):
                raise ValueError("deck does not match earned cards")
            for event in self.events:
                if event.card_id is None or (event.round_no >= 4 and not any(
                        row.round_no == max(number for number in DRAFT_ROUNDS if number <= event.round_no)
                        for row in self.card_drafts)):
                    raise ValueError("missing played card or draft")
            waiting = self.round_no in DRAFT_ROUNDS and not any(row.round_no == self.round_no for row in self.card_drafts)
            expected_hand = 0 if self.status != "active" or waiting else 3
            if len(self.cards.hand) != expected_hand:
                raise ValueError("hand does not match combat phase")


@dataclass(frozen=True)
class TurnResult:
    code: str
    event: TurnEvent | None = None
    camp_event: CampEvent | None = None
    relic_event: RelicEvent | None = None
    route_event: RouteEvent | None = None
    card_draft: CardDraftEvent | None = None


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
    offers = offer_blessings(seed) if uses_blessing(ruleset) else ()
    state = CombatState(game.player_id, game.world_id, seed, tuple(monsters), ruleset=ruleset,
                        blessing_options=offers, cards=new_deck(seed) if ruleset == CARDS_RULESET else None,
                        elemental=new_elements(seed) if ruleset == ELEMENTS_RULESET else None)
    if ruleset == ELEMENTS_RULESET:
        prepare_floor(state)
    else:
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
    relic_bonus: int = 0
    relic_triggers: tuple[str, ...] = ()
    route_bonus: int = 0


def _choice_effect(state: CombatState, choice_id: str, card_id: str | None = None) -> ChoiceEffect:
    """预览与结算共用：基础效果→可选祝福，整个过程不修改状态。"""
    monster, stance = state.current_monster, _stance(state)
    if monster.fatal:
        return ChoiceEffect(0, state.focus, stance, state.combo_ready)
    if monster.bonus_points:
        return ChoiceEffect(monster.bonus_points, state.focus, stance, state.combo_ready)
    if stance == "classic":
        return ChoiceEffect(1 if choice_id == "strike" else 0, state.focus, stance, False)
    previous = next((event.choice_id for event in reversed(state.events)
                     if event.stance in {"opening", "pressure"}), None)
    if card_id is not None:
        points, after = base_effect(card_id, stance, state.focus, previous)
    elif choice_id == "guard":
        points, after = (1 if stance == "pressure" else 0), min(MAX_FOCUS, state.focus + 1)
    else:
        cost, reward = (1, 3) if stance == "opening" else (2, 4)
        points, after = (reward, state.focus - cost) if state.focus >= cost else (0, state.focus)
    bonus, combo = blessing_effect(state.blessing, stance, choice_id, points, state.combo_ready,
                                  bonus_points=state.blessing_level + 1)
    extra = relic_effect([event.choice_id for event in state.relic_events], stance, choice_id,
                         state.focus, after, points > 0, previous)
    challenge = route_bonus(state.route_events, state.events, state.round_no, stance, choice_id, points > 0)
    return ChoiceEffect(points + bonus + extra.bonus + challenge, extra.focus_after, stance, combo, bonus,
                        extra.bonus, extra.triggers, challenge)


def choose_blessing(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """显式二选一：只改本局祝福，不结算敌人/加分/推进轮次。"""
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if not uses_blessing(state.ruleset):
        return TurnResult("BLESSINGS_NOT_SUPPORTED")
    if state.blessing is not None:
        return TurnResult("BLESSING_ALREADY_CHOSEN")
    if state.round_no != 1 or state.events:
        return TurnResult("BLESSING_TOO_LATE")
    if choice_id not in state.blessing_options:
        return TurnResult("BLESSING_NOT_OFFERED")
    state.blessing = choice_id
    return TurnResult("BLESSING_CHOSEN")


def camp_pending(state: CombatState) -> bool:
    return (uses_camp(state.ruleset) and state.status == "active" and state.blessing is not None
            and state.round_no in CAMP_ROUNDS and not any(event.round_no == state.round_no for event in state.camp_events))


def choose_camp(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """营地选择不耗战斗轮次；只有显式动作才能提交，刷新不能重复领取。"""
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if not uses_camp(state.ruleset):
        return TurnResult("CAMPS_NOT_SUPPORTED")
    if not camp_pending(state):
        return TurnResult("CAMP_NOT_PENDING")
    if choice_id not in CAMP_CHOICES:
        return TurnResult("INVALID_CAMP_CHOICE")
    effect = camp_effect(choice_id, state.focus, state.blessing_level)
    if not effect["enabled"]:
        return TurnResult("CAMP_CHOICE_UNAVAILABLE")
    event = CampEvent(state.round_no, choice_id, state.focus, effect["focus_after"],
                      state.blessing_level, effect["level_after"], effect["points_awarded"])
    state.camp_events.append(event)
    state.focus, state.blessing_level = event.focus_after, event.level_after
    state.score += event.points_awarded
    return TurnResult("CAMP_CHOSEN", camp_event=event)


def relic_pending(state: CombatState) -> bool:
    return (uses_relics(state.ruleset) and state.status == "active"
            and any(event.round_no == state.round_no for event in state.camp_events)
            and not any(event.round_no == state.round_no for event in state.relic_events))


def choose_relic(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """仅服务器候选可以领取，领取不推进轮次、不加分、不改变敌人。"""
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if not uses_relics(state.ruleset):
        return TurnResult("RELICS_NOT_SUPPORTED")
    if not relic_pending(state):
        return TurnResult("RELIC_NOT_PENDING")
    offered = offer_relics(state.seed, state.round_no, [event.choice_id for event in state.relic_events])
    if type(choice_id) is not str or choice_id not in offered:
        return TurnResult("RELIC_NOT_OFFERED")
    event = RelicEvent(state.round_no, choice_id)
    state.relic_events.append(event)
    return TurnResult("RELIC_CHOSEN", relic_event=event)


def route_pending(state: CombatState) -> bool:
    return (state.ruleset == ROUTES_RULESET and state.status == "active" and state.blessing is not None
            and state.round_no in ROUTE_ROUNDS and not camp_pending(state) and not relic_pending(state)
            and not any(event.round_no == state.round_no for event in state.route_events))


def choose_route(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if state.ruleset != ROUTES_RULESET:
        return TurnResult("ROUTES_NOT_SUPPORTED")
    if not route_pending(state):
        return TurnResult("ROUTE_NOT_PENDING")
    if type(choice_id) is not str or choice_id not in ROUTE_CHOICES:
        return TurnResult("INVALID_ROUTE_CHOICE")
    effect = route_option(choice_id, state.focus, state.round_no)
    if not effect["enabled"]:
        return TurnResult("ROUTE_CHOICE_UNAVAILABLE")
    event = RouteEvent(state.round_no, choice_id, state.focus, effect["focus_after"])
    state.route_events.append(event)
    state.focus = event.focus_after
    return TurnResult("ROUTE_CHOSEN", route_event=event)


def combat_preview(state: CombatState) -> dict:
    """玩家可见的当前规则提示；不暴露seed/未来怪物，不进行结算。"""
    if state.ruleset == ELEMENTS_RULESET:
        from .elements import preview
        return preview(state)
    tactical = uses_focus(state.ruleset)
    blessing = BLESSINGS.get(state.blessing)
    preview = {"ruleset": state.ruleset, "focus": state.focus if tactical else None,
               "max_focus": MAX_FOCUS if tactical else None, "stance": None,
               "hint": "本场试炼已结束。", "options": [], "phase": "finished",
               "blessing_name": blessing.name if blessing else None,
               "blessing_description": blessing.description.replace("+2", f"+{state.blessing_level + 1}") if blessing else None,
               "combo_ready": state.combo_ready,
               "blessing_level": state.blessing_level if uses_blessing(state.ruleset) else None,
               "chapter": chapter_info(state.round_no),
               "report": run_report(state.events, state.camp_events, state.status, state.score),
               "camp_rounds": list(CAMP_ROUNDS) if uses_camp(state.ruleset) else [],
               "visited_camps": [event.round_no for event in state.camp_events],
               "relics": [relic_view(event.choice_id) for event in state.relic_events],
               "routes": [route_progress(event, state.events, state.round_no, state.status)
                          for event in state.route_events],
               "deck": deck_view(state.cards) if state.cards else None,
               "guard_comments": {}}
    if uses_relics(state.ruleset):
        report = preview["report"]
        report["relic_points"] = sum(event.relic_bonus_points for event in state.events)
        report["relic_triggers"] = sum(len(event.relic_triggers) for event in state.events)
        report["relic_counts"] = {key: sum(key in event.relic_triggers for event in state.events)
                                  for key in RELICS if any(key in event.relic_triggers for event in state.events)}
        if report["relic_triggers"] >= 3:
            report["badges"].append("遗物共鸣")
        journal = [{**row, "relic_bonus": next((event.relic_bonus_points for event in state.events
                    if row["kind"] == "battle" and event.round_no == row["round_no"]), 0)}
                   for row in report["journal"]]
        journal += [{"round_no": event.round_no, "kind": "relic", "title": "营地遗物",
                     "choice": RELICS[event.choice_id].name, "points": 0, "fatal": False, "bonus": 0,
                     "relic_bonus": 0} for event in state.relic_events]
        journal.sort(key=lambda row: (row["round_no"], {"camp": 0, "relic": 1, "battle": 2}[row["kind"]]))
        report["journal"] = journal[-6:]
    if state.ruleset == ROUTES_RULESET:
        report = preview["report"]
        report["route_points"] = sum(event.route_bonus_points for event in state.events)
        report["routes_completed"] = sum(row["earned_points"] > 0 for row in preview["routes"])
        journal = [{**row, "route_bonus": next((event.route_bonus_points for event in state.events
                    if row["kind"] == "battle" and event.round_no == row["round_no"]), 0)}
                   for row in report["journal"]]
        journal += [{"round_no": row["round_no"], "kind": "route", "title": row["title"],
                     "choice": f"专注{row['focus_before']}→{row['focus_after']} · {row['status']}",
                     "points": 0, "fatal": False, "bonus": 0} for row in preview["routes"]]
        journal.sort(key=lambda row: (row["round_no"], {"camp": 0, "relic": 1, "route": 2, "battle": 3}[row["kind"]]))
        report["journal"] = journal[-6:]
        if report["routes_completed"] >= 2:
            report["badges"].append("试锋行者")
    if state.cards:
        report = preview["report"]
        report["cards_played"] = len(state.events)
        report["deck_size"] = len(state.cards.deck)
        report["drafts_skipped"] = sum(row.choice_id == "draft_skip" for row in state.card_drafts)
        for row in report["journal"]:
            event = next((event for event in state.events if row["kind"] == "battle" and row["round_no"] == event.round_no), None)
            if event:
                row["choice"] = CARDS[event.card_id].name
        report["journal"] += [{"round_no": row.round_no, "kind": "draft", "title": "营地选牌",
                               "choice": "跳过选牌" if row.choice_id == "draft_skip" else CARDS[row.choice_id.replace("draft_", "card_", 1)].name,
                               "points": 0, "fatal": False, "bonus": 0} for row in state.card_drafts]
        report["journal"].sort(key=lambda row: (row["round_no"], {"camp": 0, "relic": 1, "draft": 2, "battle": 3}[row["kind"]]))
        report["journal"] = report["journal"][-6:]
    def finish_preview():
        preview["guard_comments"] = guard_comments(preview, state.status)
        return preview
    if state.status != "active":
        return finish_preview()
    if uses_blessing(state.ruleset) and state.blessing is None:
        preview.update(phase="blessing", hint="符文石亮起两道光：本局只选一种祝福，不消耗轮次，也不改变怪物概率。")
        preview["options"] = [{"choice_id": key, "title": BLESSINGS[key].name,
                                "description": BLESSINGS[key].description,
                                "points_awarded": 0, "focus_after": state.focus}
                               for key in state.blessing_options]
        return finish_preview()
    if camp_pending(state):
        preview.update(phase="camp", hint=f"{CAMP_NAMES[state.round_no]}：先做一次营地选择，再揭晓第{state.round_no}轮敌人。剩余{MAX_ROUNDS - state.round_no + 1}轮；不改变怪物概率，也不提供免死。")
        preview["options"] = [camp_effect(key, state.focus, state.blessing_level) for key in CAMP_CHOICES]
        return finish_preview()
    if relic_pending(state):
        owned = [event.choice_id for event in state.relic_events]
        preview.update(phase="relic", hint="营地遗物三选一：先看祝福与打法，再选本局遗物；选定不加分、不耗轮次。下一轮敌人仍未揭晓。遗物只作用于普通怪，不免死。")
        preview["options"] = [{**relic_view(key), "focus_after": state.focus}
                              for key in offer_relics(state.seed, state.round_no, owned)]
        return finish_preview()
    if route_pending(state):
        preview.update(phase="route", hint="选择本章节路线：补资源，或先付专注挑战进攻目标。敌人尚未揭晓；两路抽怪与即死概率完全相同，不是生存安全/危险分支。")
        preview["options"] = [route_option(key, state.focus, state.round_no) for key in ROUTE_CHOICES]
        return finish_preview()
    if draft_pending(state):
        preview.update(phase="draft", hint="选一张新卡加入牌库顶，下轮保证入手；也可跳过保持精简。选牌不加分、不耗专注或战斗轮次，敌人与新手牌尚未揭晓。")
        preview["options"] = draft_options(state.seed, state.round_no, state.focus)
        return finish_preview()
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
    if state.cards:
        preview["hint"] = ("每轮抽3张、选1张完成遭遇，其余一起弃置。卡牌效果与专注成本见下方；弃牌洗回继续抽取。"
                           if stance in {"opening", "pressure"} else hints[stance])
        preview["hint"] += " 当前架势：" + {"opening": "破绽", "pressure": "蓄势", "rare": "稀有", "fatal": "即死"}[stance] + "。"
    if state.combo_ready:
        preview["hint"] += f" 风之余韵已蓄势：下一次成功普通攻击额外+{state.blessing_level + 1}；稀有/即死怪不触发。"
    choices = list(dict.fromkeys(state.cards.hand)) if state.cards else list(CHOICES)
    if state.cards and stance in {"opening", "pressure"} and not any(CARDS[key].cost <= state.focus for key in choices):
        choices.append("card_breathe")
    for action_id in choices:
        card = CARDS[action_id] if state.cards else None
        choice_id = card.kind if card else action_id
        title = card.name if card else {"strike": "攻击", "guard": "防御"}[choice_id]
        effect = _choice_effect(state, choice_id, action_id if card else None)
        enabled = card is None or stance in {"fatal", "rare"} or card.cost <= state.focus
        points, after = effect.points, effect.focus_after
        if stance == "fatal":
            description = "结束爬塔，+0分；此遭遇无法通过防御躲避。"
        elif tactical:
            description = f"本轮+{points}分；专注{state.focus}→{after}/{MAX_FOCUS}。"
            if choice_id == "strike" and stance in {"opening", "pressure"} and points == 0:
                description += "专注不足，攻击不得分；建议先蓄力。"
            if effect.bonus:
                description += f"已含祝福额外+{effect.bonus}分。"
            if effect.relic_triggers:
                names = "、".join(RELICS[key].name for key in effect.relic_triggers)
                detail = f"额外+{effect.relic_bonus}分" if effect.relic_bonus else "专注恢复已计入"
                description += f"遗物【{names}】已计入：{detail}。"
            if effect.route_bonus:
                description += f"本击完成章节挑战，已含契约额外+{effect.route_bonus}分；不会再次领取。"
            if state.blessing == "boon_wind" and choice_id == "guard" and stance in {"opening", "pressure"}:
                description += f"为下一次成功普通攻击蓄势+{state.blessing_level + 1}，不叠加。"
        else:
            description = f"本轮+{points}分。"
        if card:
            description = card.description + " " + description
            if not enabled:
                description = "专注不足，当前不可出牌。" + card.description
            if stance in {"rare", "fatal"}:
                description = ("本遭遇忽略牌面消耗与加成，不改专注。" + description)
        preview["options"].append({"choice_id": action_id, "title": title,
                                   "description": description, "points_awarded": points,
                                   "enabled": enabled,
                                   **({"cost": card.cost, "card_kind": card.kind, "copies": state.cards.hand.count(action_id)} if card else {}),
                                   "focus_after": after if tactical else None,
                                   "blessing_bonus_points": effect.bonus,
                                   "relic_bonus_points": effect.relic_bonus,
                                   "route_bonus_points": effect.route_bonus,
                                   "relic_triggers": list(effect.relic_triggers)})
    return finish_preview()


def advance_round(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """验证后结算一次；失败不改状态，死亡优先于第十轮通关。"""

    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if state.cards:
        return TurnResult("CARD_REQUIRED")
    if state.elemental:
        return TurnResult("ELEMENT_CARD_REQUIRED")
    if choice_id not in CHOICES:
        return TurnResult("INVALID_CHOICE")
    if uses_blessing(state.ruleset) and state.blessing is None:
        return TurnResult("BLESSING_REQUIRED")
    if camp_pending(state):
        return TurnResult("CAMP_REQUIRED")
    if relic_pending(state):
        return TurnResult("RELIC_REQUIRED")
    if route_pending(state):
        return TurnResult("ROUTE_REQUIRED")
    return _resolve_round(state, choice_id)


def draft_pending(state):
    return (state.ruleset == CARDS_RULESET and state.status == "active" and state.round_no in DRAFT_ROUNDS
            and not camp_pending(state) and not relic_pending(state)
            and not any(row.round_no == state.round_no for row in state.card_drafts))


def choose_card(state, choice_id, expected_round):
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if not draft_pending(state):
        return TurnResult("DRAFT_NOT_PENDING")
    offered = {row["choice_id"] for row in draft_options(state.seed, state.round_no, state.focus)}
    if type(choice_id) is not str or choice_id not in offered:
        return TurnResult("CARD_NOT_OFFERED")
    event = CardDraftEvent(state.round_no, choice_id)
    if choice_id != "draft_skip":
        key = choice_id.replace("draft_", "card_", 1)
        state.cards.deck.append(key)
        state.cards.draw_pile.insert(0, key)
    state.card_drafts.append(event)
    draw_hand(state.cards, state.seed)
    return TurnResult("CARD_DRAFTED", card_draft=event)


def play_card(state, card_id, expected_round):
    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if state.ruleset != CARDS_RULESET:
        return TurnResult("CARDS_NOT_SUPPORTED")
    view = combat_preview(state)
    if view["phase"] != "combat":
        return TurnResult("CARD_NOT_PENDING")
    option = next((row for row in view["options"] if row["choice_id"] == card_id), None)
    if option is None:
        return TurnResult("CARD_NOT_IN_HAND")
    if not option["enabled"]:
        return TurnResult("CARD_UNAFFORDABLE")
    return _resolve_round(state, CARDS[card_id].kind, card_id)


def _resolve_round(state, choice_id, card_id=None):
    """仅由已校验的按钮/出牌路径调用；预览与结算共用效果。"""

    monster = state.current_monster
    if monster is None:
        raise RuntimeError("active combat has no monster")
    effect = _choice_effect(state, choice_id, card_id)
    points, after, stance = effect.points, effect.focus_after, effect.stance
    tactical = uses_focus(state.ruleset)
    event = TurnEvent(state.round_no, monster.monster_id, choice_id, monster.fatal, points,
                      stance if tactical else None, state.focus if tactical else None,
                      after if tactical else None, effect.bonus,
                      state.combo_ready if uses_blessing(state.ruleset) else None,
                      effect.combo_after if uses_blessing(state.ruleset) else None,
                      effect.relic_bonus, effect.relic_triggers, effect.route_bonus, card_id)
    state.events.append(event)
    if state.cards:
        discard_hand(state.cards)
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
    if state.cards and not camp_pending(state):
        draw_hand(state.cards, state.seed)
    return TurnResult("ROUND_RESOLVED", event)
