"""原创小型牌组：抽3选1、弃牌洗回、营地可跳过的选牌；不改变生死规则。"""
from collections import Counter
from dataclasses import dataclass, field
from random import Random

CARDS_RULESET = "cards_v1"
DRAFT_ROUNDS = (4, 7, 10)


@dataclass(frozen=True)
class Card:
    name: str
    kind: str
    cost: int
    opening: int = 0
    pressure: int = 0
    recovery: int = 0
    description: str = ""


CARDS = {
    "card_jab": Card("风切", "strike", 1, 3, 2, description="耗1专注；破绽基础3分，蓄势基础2分。"),
    "card_heavy": Card("破阵", "strike", 2, 4, 6, description="耗2专注；破绽基础4分，蓄势基础6分。"),
    "card_guard": Card("架盾", "guard", 0, 0, 1, 1, "回1专注；破绽基础0分，蓄势基础1分。"),
    "card_meditate": Card("调息", "guard", 0, 0, 0, 2, "回2专注，基础0分；优先补充后续出手资源。"),
    "card_lunge": Card("逐风刺", "strike", 1, 4, 1, description="耗1专注；破绽基础4分，蓄势仅1分。"),
    "card_counter": Card("磐岩阵", "guard", 0, 0, 2, 1, "回1专注；蓄势基础2分，破绽0分。"),
    "card_riposte": Card("回响斩", "strike", 1, 2, 2, description="耗1专注，基础2分；上次普通行动为防御则再+3。"),
    "card_charge": Card("倾风一击", "strike", 3, 8, 8, description="耗满3专注，基础8分；需要先蓄力。"),
    "card_breathe": Card("应急整备", "guard", 0, 0, 0, 1, "仅手牌全部不可用时提供；回1专注，基础0分，本轮手牌仍全部弃置。"),
}
STARTER = ("card_jab", "card_jab", "card_guard", "card_guard", "card_meditate", "card_heavy")
REWARD_CARDS = ("card_lunge", "card_counter", "card_riposte", "card_charge")
DRAFT_CHOICES = tuple(key.replace("card_", "draft_", 1) for key in REWARD_CARDS) + ("draft_skip",)


@dataclass
class DeckState:
    deck: list[str]
    draw_pile: list[str] = field(default_factory=list)
    discard_pile: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    shuffle_count: int = 0

    def __post_init__(self):
        for values in (self.deck, self.draw_pile, self.discard_pile, self.hand):
            if (not isinstance(values, list) or any(type(key) is not str or key not in CARDS
                                                   or key == "card_breathe" for key in values)):
                raise ValueError("invalid deck cards")
        if not 6 <= len(self.deck) <= 9 or len(self.hand) not in {0, 3}:
            raise ValueError("invalid deck or hand size")
        if Counter(self.deck) != Counter(self.draw_pile + self.discard_pile + self.hand):
            raise ValueError("card conservation violated")
        if type(self.shuffle_count) is not int or not 1 <= self.shuffle_count <= 20:
            raise ValueError("invalid shuffle count")


def new_deck(seed):
    pile = list(STARTER)
    Random(f"echoworld:{CARDS_RULESET}:{seed}:0").shuffle(pile)
    return DeckState(list(STARTER), pile[3:], [], pile[:3], 1)


def draw_hand(cards, seed):
    """仅真实结算/选牌后调用；读取预览和刷新不得抽牌。"""
    if cards.hand:
        raise ValueError("cannot redraw a live hand")
    while len(cards.hand) < 3:
        if not cards.draw_pile:
            cards.draw_pile = list(cards.discard_pile)
            cards.discard_pile.clear()
            Random(f"echoworld:{CARDS_RULESET}:{seed}:{cards.shuffle_count}").shuffle(cards.draw_pile)
            cards.shuffle_count += 1
        if not cards.draw_pile:
            raise ValueError("not enough cards to draw")
        cards.hand.append(cards.draw_pile.pop(0))


def discard_hand(cards):
    cards.discard_pile.extend(cards.hand)
    cards.hand.clear()


def base_effect(key, stance, focus, previous):
    card = CARDS[key]
    if card.cost > focus:
        return 0, focus
    points = card.opening if stance == "opening" else card.pressure
    if key == "card_riposte" and previous == "guard":
        points += 3
    return points, min(3, focus - card.cost + card.recovery)


def offer_cards(seed, round_no):
    return tuple(Random(f"echoworld:{CARDS_RULESET}:reward:{seed}:{round_no}").sample(REWARD_CARDS, 2))


@dataclass(frozen=True)
class CardDraftEvent:
    round_no: int
    choice_id: str

    def __post_init__(self):
        if type(self.round_no) is not int or self.round_no not in DRAFT_ROUNDS:
            raise ValueError("invalid draft round")
        if type(self.choice_id) is not str or self.choice_id not in DRAFT_CHOICES:
            raise ValueError("invalid draft choice")


def draft_options(seed, round_no, focus):
    options = [{"choice_id": key.replace("card_", "draft_", 1), "title": CARDS[key].name,
                "description": CARDS[key].description + " 加入牌库顶，下一手保证抽到；牌组变厚可能降低其他卡的抽取频率。",
                "enabled": True, "focus_after": focus, "points_awarded": 0,
                "card_kind": CARDS[key].kind, "cost": CARDS[key].cost}
               for key in offer_cards(seed, round_no)]
    return options + [{"choice_id": "draft_skip", "title": "跳过选牌", "description": "不新增卡，维持当前牌组；不加分、不扣专注。",
                       "enabled": True, "focus_after": focus, "points_awarded": 0}]


def deck_view(cards):
    return {"size": len(cards.deck), "draw_count": len(cards.draw_pile),
            "discard_count": len(cards.discard_pile), "hand_count": len(cards.hand),
            "shuffle_count": cards.shuffle_count,
            "composition": [{"card_id": key, "title": CARDS[key].name, "count": count,
                             "description": CARDS[key].description} for key, count in Counter(cards.deck).items()]}
