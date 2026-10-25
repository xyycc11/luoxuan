"""七元素原创同人规则：代码结算反应、伤害与首领；不调用模型。

旧版即死/计分模式不迁移。新模式独立保存牌堆与每一步记录。
"""
from collections import Counter
from copy import copy
from dataclasses import dataclass, field
from random import Random

ELEMENTS_RULESET = "elements_v1"
MAX_HP = 44
ENERGY = 3
BOSS_HP = 90
BOSS_BARRIER = 20
SECRET_NAME = "莎芙（龙龙最可爱）"
SECRET_HP, SECRET_BARRIER = 210, 32
SECRET_ENERGY, SECRET_HAND, SECRET_POWER = 5, 6, 2
SECRET_ACTIONS = ("element_secret_enter", "element_secret_decline")
ELEMENT_NAMES = {"pyro": "火", "hydro": "水", "electro": "雷", "cryo": "冰",
                 "dendro": "草", "anemo": "风", "geo": "岩"}


@dataclass(frozen=True)
class ElementCard:
    name: str
    cost: int
    damage: int
    block: int = 0
    heal: int = 0
    text: str = ""


CARDS = {
    "pyro": ElementCard("火 · 烈焰重奏", 2, 7, text="高基础伤害；接水蒸发、接冰融化，或引爆草原核。"),
    "hydro": ElementCard("水 · 清泉回响", 1, 3, heal=2, text="恢复2生命；挂水可接蒸发、冻结、感电或绽放。"),
    "electro": ElementCard("雷 · 紫电追击", 2, 5, text="可触发感电/激化/超载；有草原核时触发超绽放。"),
    "cryo": ElementCard("冰 · 霜华屏障", 1, 3, block=2, text="获得2护盾；冻结使本次敌方攻击降低4。"),
    "dendro": ElementCard("草 · 萌芽之种", 1, 3, text="接水产生草原核；接火燃烧，接雷激化。"),
    "anemo": ElementCard("风 · 聚风之诗", 1, 2, text="扩散火水雷冰额外3伤、返还1能量，保留附着。"),
    "geo": ElementCard("岩 · 磐石守誓", 1, 2, block=5, text="获得5护盾；结晶额外4护盾与1伤，保留附着。"),
}
DECK = tuple(CARDS)
HOLD_ACTIONS = tuple("element_hold_" + key for key in DECK)
PARRY_ACTIONS = tuple("element_parry_" + key for key in DECK)
DOCTRINES = {
    "assault": ("烈风破阵", "每回合首次触发元素反应时，额外造成3伤害。"),
    "bastion": ("磐岩守誓", "每回合首次触发元素反应时，获得3护盾。"),
    "flow": ("清泉流转", "每回合首次触发元素反应时，返还1能量（上限3）。"),
}
DOCTRINE_ACTIONS = tuple("element_doctrine_" + key for key in DOCTRINES)
REFINEMENTS = {
    "pyro": ("烈焰升格", "火牌基础伤害+4，仍消耗2能量。"),
    "hydro": ("清泉滋养", "水牌治疗由2提高至5，不超过生命上限。"),
    "electro": ("雷鸣引爆", "雷牌触发任意元素反应时，额外造成4伤害。"),
    "cryo": ("霜华加护", "冰牌自身护盾由2提高至6，冻结效果不变。"),
    "dendro": ("生息萌芽", "草牌触发元素反应时，额外获得4护盾。"),
    "anemo": ("余风回响", "风牌触发扩散时额外伤害+4，返能量规则不变。"),
    "geo": ("磐岩坚壁", "岩牌自身护盾由5提高至9，结晶效果另算。"),
}
REFINE_ACTIONS = tuple("element_refine_" + key for key in DECK)
CAMP_ACTIONS = ("element_rest", "element_attune") + DOCTRINE_ACTIONS + REFINE_ACTIONS
ACTIONS = tuple("element_" + key for key in DECK) + HOLD_ACTIONS + PARRY_ACTIONS + ("element_end",) + CAMP_ACTIONS + SECRET_ACTIONS
ENEMY_STYLES = {
    "丘丘人": ("疾攻", (4, 4, 7), "连续快攻；适合用冻结或结晶稳住节奏。"),
    "木盾丘丘暴徒": ("盾击", (3, 5, 8), "开场6屏障；反应额外削盾4，破盾削弱下一击4。"),
    "火史莱姆": ("烈焰", (5, 3, 8), "开场附着火；可用水/冰反应或风/岩利用附着，无官方元素免疫。"),
    "风丘丘萨满": ("风袭", (2, 8, 4), "先试探再强攻；第二拍是重击，留防护牌应对。"),
    "遗迹守卫": ("机巧", (2, 5, 12), "第三拍蓄力重击；提前准备冻结、护盾或斩杀。"),
}
REACTIONS = {
    frozenset(("pyro", "hydro")): ("蒸发", 5),
    frozenset(("pyro", "cryo")): ("融化", 6),
    frozenset(("pyro", "electro")): ("超载", 5),
    frozenset(("hydro", "electro")): ("感电", 3),
    frozenset(("hydro", "cryo")): ("冻结", 2),
    frozenset(("cryo", "electro")): ("超导", 3),
    frozenset(("pyro", "dendro")): ("燃烧", 2),
    frozenset(("electro", "dendro")): ("激化", 4),
    frozenset(("hydro", "dendro")): ("绽放", 1),
}
REACTION_GUIDE = ["火+水 蒸发 +5；火+冰 融化 +6；火+雷 超载 +5、削弱攻击2。",
    "水+雷 感电 +3、回合末再2伤；水+冰 冻结 +2、削弱攻击4。",
    "冰+雷 超导 +3、下一张伤害+2；草+雷 激化 +4、下一张伤害+3。",
    "草+火 燃烧 +2、回合末再3伤；草+水 绽放 +1、产生1草原核（最多2）。",
    "草原核+火/雷 烈/超绽放 +8并消耗1核，可与本击附着反应并存。",
    "风扩散火水雷冰 +3并返1能量；岩结晶火水雷冰 +1并加4护盾；二者保留附着。",
    "其他组合无反应。反应通常消耗附着；非反应火水雷冰草覆盖单一附着。数值均为项目原创简化。"]


@dataclass
class ElementState:
    hp: int = MAX_HP
    energy: int = ENERGY
    block: int = 0
    power: int = 0
    turn: int = 1
    enemy_hp: int = 12
    enemy_max_hp: int = 12
    barrier: int = 0
    aura: str | None = None
    cores: int = 0
    dot: int = 0
    weaken: int = 0
    next_bonus: int = 0
    hand: list[str] = field(default_factory=list)
    draw: list[str] = field(default_factory=list)
    discard: list[str] = field(default_factory=list)
    shuffles: int = 0
    camp_due: bool = False
    camps: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    # 缺字段的旧JSON默认初版；只有新开局显式选择新版，不热改旧局。
    edition: int = 1
    held: str | None = None
    hold_used: bool = False
    # 第三版才允许营地学习一种流派；触发次数在本回合内保存。
    doctrine: str | None = None
    doctrine_used: bool = False
    # 第四版隐藏挑战。十层结算血量单独保存，不因后续治疗重新解锁。
    secret: str = "none"
    base_clear_hp: int | None = None
    # 第五版营地精炼：保持七张独立牌，不复制卡牌；每张最多精炼一次。
    refinements: list[str] = field(default_factory=list)

    def __post_init__(self):
        if type(self.edition) is not int or self.edition not in {1, 2, 3, 4, 5}:
            raise ValueError("invalid elemental edition")
        if self.secret not in {"none", "offered", "declined", "active", "won", "lost"}:
            raise ValueError("invalid secret challenge")
        if self.base_clear_hp is not None and (type(self.base_clear_hp) is not int or not 1 <= self.base_clear_hp <= MAX_HP):
            raise ValueError("invalid base clear hp")
        if self.secret != "none" and (self.edition < 4 or self.base_clear_hp is None or self.base_clear_hp <= 30):
            raise ValueError("secret requires qualified fourth-edition clear")
        if type(self.hold_used) is not bool:
            raise ValueError("invalid hold usage")
        if self.held is not None and (type(self.held) is not str or self.held not in self.hand or not self.hold_used):
            raise ValueError("held card must exist in hand and consume hold choice")
        if self.edition == 1 and (self.held is not None or self.hold_used):
            raise ValueError("first edition cannot hold cards")
        if self.doctrine is not None and (type(self.doctrine) is not str or self.doctrine not in DOCTRINES):
            raise ValueError("invalid elemental doctrine")
        if type(self.doctrine_used) is not bool or (self.doctrine_used and self.doctrine is None):
            raise ValueError("invalid doctrine trigger")
        if self.edition < 3 and (self.doctrine is not None or self.doctrine_used):
            raise ValueError("old editions cannot learn doctrines")
        if (not isinstance(self.refinements, list) or len(self.refinements) > 3
                or any(type(k) is not str or k not in REFINEMENTS for k in self.refinements)
                or len(set(self.refinements)) != len(self.refinements)
                or (self.edition < 5 and self.refinements)):
            raise ValueError("invalid elemental refinements")
        hidden = self.secret in {"active", "won", "lost"}
        bounds = {"hp": (0, MAX_HP), "energy": (0, SECRET_ENERGY if hidden else ENERGY), "block": (0, 30),
                  "power": (0, 3), "turn": (1, 5 if hidden else 12), "enemy_hp": (0, SECRET_HP if hidden else BOSS_HP),
                  "enemy_max_hp": (12, SECRET_HP if hidden else BOSS_HP), "barrier": (0, SECRET_BARRIER if hidden else BOSS_BARRIER), "cores": (0, 2),
                  "dot": (0, 3), "weaken": (0, 6), "next_bonus": (0, 3), "shuffles": (1, 200)}
        for key, (low, high) in bounds.items():
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid elemental " + key)
        if self.aura is not None and self.aura not in {"pyro", "hydro", "electro", "cryo", "dendro"}:
            raise ValueError("invalid aura")
        if self.enemy_hp > self.enemy_max_hp or type(self.camp_due) is not bool:
            raise ValueError("invalid enemy/camp")
        if any(not isinstance(pile, list) or any(type(k) is not str or k not in CARDS for k in pile)
               for pile in (self.hand, self.draw, self.discard)):
            raise ValueError("invalid elemental piles")
        if len(self.hand) > (SECRET_HAND if hidden else 4) or Counter(self.hand + self.draw + self.discard) != Counter(DECK):
            raise ValueError("element cards must conserve exactly seven unique cards")
        if (not isinstance(self.log, list) or len(self.log) > 600 or any(not isinstance(x, dict) for x in self.log)
                or not isinstance(self.camps, list) or len(self.camps) > 3):
            raise ValueError("invalid elemental history")
        for row in self.log:
            if (not {"floor", "turn", "action", "text", "points"} <= row.keys()
                    or type(row["floor"]) is not int or not 1 <= row["floor"] <= (11 if self.edition >= 4 else 10)
                    or type(row["turn"]) is not int or not 1 <= row["turn"] <= 12
                    or row["action"] not in (*ACTIONS, "rare_reward", "defeat")
                    or type(row["text"]) is not str or len(row["text"]) > 300
                    or type(row["points"]) is not int or not 0 <= row["points"] <= SECRET_HP):
                raise ValueError("invalid elemental log entry")
        for row in self.camps:
            if (not isinstance(row, dict) or set(row) != {"floor", "action"}
                    or type(row["floor"]) is not int or row["floor"] not in {4,7,10}
                    or row["action"] not in CAMP_ACTIONS
                    or (self.edition < 3 and row["action"] in DOCTRINE_ACTIONS)
                    or (self.edition < 5 and row["action"] in REFINE_ACTIONS)):
                raise ValueError("invalid elemental camp record")
        if [row["floor"] for row in self.camps] != [4,7,10][:len(self.camps)]:
            raise ValueError("elemental camps must be sequential")
        if self.power != sum(row["action"] == "element_attune" for row in self.camps):
            raise ValueError("elemental power does not match earned upgrades")
        learned = [row["action"].removeprefix("element_doctrine_") for row in self.camps
                   if row["action"] in DOCTRINE_ACTIONS]
        if learned != ([self.doctrine] if self.doctrine is not None else []):
            raise ValueError("doctrine must match exactly one earned camp choice")
        refined = [row["action"].removeprefix("element_refine_") for row in self.camps
                   if row["action"] in REFINE_ACTIONS]
        if refined != self.refinements:
            raise ValueError("refinements must match earned camp choices")


def energy_cap(e):
    return SECRET_ENERGY if e.secret in {"active", "won", "lost"} else ENERGY


def hand_cap(e):
    return SECRET_HAND if e.secret in {"active", "won", "lost"} else 4


def refinement_offers(state):
    """只展示当前营地候选；由独立种子固定，不触碰战斗抽牌随机流。"""
    e = state.elemental
    if e.edition < 5 or not e.camp_due or state.status != "active":
        return []
    choices = [k for k in DECK if k not in e.refinements]
    return Random(f"refine:{state.seed}:{state.round_no}").sample(choices, min(3, len(choices)))


def refinement_text(e, key):
    return ("精炼【" + REFINEMENTS[key][0] + "】：" + REFINEMENTS[key][1]) if key in e.refinements else ""


def card_text(e, key):
    """牌面说明使用当前数值，避免精炼后的预览仍显示旧治疗/护盾。"""
    text = CARDS[key].text
    if e.edition >= 5 and key in e.refinements:
        replacements = {"hydro": ("恢复2生命", "恢复5生命"),
                        "cryo": ("获得2护盾", "获得6护盾"),
                        "geo": ("获得5护盾", "获得9护盾")}
        if key in replacements:
            text = text.replace(*replacements[key])
    return text


def _draw(e, seed, floor):
    while len(e.hand) < hand_cap(e):
        if not e.draw:
            e.draw, e.discard = e.discard, []
            Random(f"elements:{seed}:{floor}:{e.shuffles}").shuffle(e.draw)
            e.shuffles += 1
        if not e.draw:
            break
        e.hand.append(e.draw.pop(0))


def new_state(seed, edition=1):
    pile = list(DECK)
    Random(f"elements:{seed}:start").shuffle(pile)
    return ElementState(hand=pile[:4], draw=pile[4:], shuffles=1, edition=edition)


def enemy_spec(seed, floor):
    """前九层没有致死怪；大伟丘严格1%，第十层必定首领。"""
    if floor == 10:
        return "深渊咏者 · 七曜试炼", BOSS_HP, BOSS_BARRIER, False
    if Random(f"elements:rare:{seed}:{floor}").randrange(100) == 0:
        return "大伟丘", 12, 0, True
    names = ("丘丘人", "木盾丘丘暴徒", "火史莱姆", "风丘丘萨满", "遗迹守卫")
    name = Random(f"elements:enemy:{seed}:{floor}").choice(names)
    return name, 10 + floor * 2, 0, False


def prepare_floor(state):
    e = state.elemental
    name, hp, barrier, rare = enemy_spec(state.seed, state.round_no)
    from .combat import Monster
    state.current_monster = Monster(name, bonus_points=50 if rare else 0)
    e.enemy_hp = e.enemy_max_hp = hp
    e.barrier = barrier
    e.aura, e.cores, e.dot, e.weaken, e.next_bonus = None, 0, 0, 0, 0
    e.held, e.hold_used = None, False
    e.doctrine_used = False
    if e.edition >= 2 and not rare and state.round_no != 10:
        if name == "木盾丘丘暴徒": e.barrier = 6
        if name == "火史莱姆": e.aura = "pyro"
    e.turn, e.energy, e.block = 1, ENERGY, 0
    e.discard.extend(e.hand)
    e.hand.clear()
    e.camp_due = state.round_no in {4, 7, 10}
    _draw(e, state.seed, state.round_no)


def enemy_intent(state, turn=None):
    e = state.elemental
    turn = e.turn if turn is None else turn
    if state.current_monster.bonus_points:
        return "友好展示", 0
    if e.secret in {"active", "won", "lost"}:
        return ("滑板状态 · 无视护盾秒杀", MAX_HP) if turn >= 5 else ("龙龙突袭", (8, 12, 16, 20)[turn-1])
    if state.round_no == 10:
        name, amount = (("试探斩", 7), ("深渊冲击", 12), ("蓄力重击", 20))[(turn - 1) % 3]
        return name, amount + max(0, turn - 6) * 2
    if e.edition >= 2:
        name, values, _ = ENEMY_STYLES[state.current_monster.monster_id]
        return f"{name} · 第{(turn-1)%3+1}拍", values[(turn-1)%3] + state.round_no // 4
    return ("蓄力重击", 7 + state.round_no // 3) if turn % 3 == 0 else ("普通攻击", 3 + state.round_no // 3)


def _damage(e, amount, reaction=False):
    # 反应额外削韧4；护盾和生命分别结算，破盾只削弱下一次攻击一次。
    shield_before = e.barrier
    absorbed = min(e.barrier, amount)
    e.barrier -= absorbed
    if reaction and e.barrier:
        e.barrier = max(0, e.barrier - 4)
    damage = min(e.enemy_hp, amount - absorbed)
    e.enemy_hp -= damage
    if shield_before and not e.barrier:
        e.weaken = min(6, e.weaken + 4)
    return damage, shield_before - e.barrier


def card_effect(e, key):
    """对副本结算：预览与实打完全同源；不改调用者。"""
    e = copy(e)
    # 历史条目只追加不原地改写；复制容器即可，避免每个牌面预览深拷贝整局日志。
    for name in ("hand", "draw", "discard", "log", "camps", "refinements"):
        setattr(e, name, list(getattr(e, name)))
    card, previous = CARDS[key], e.aura
    reaction, extra = "", 0
    old_bonus = e.next_bonus
    e.next_bonus = 0
    e.energy -= card.cost
    e.block = min(30, e.block + card.block)
    refined = e.edition >= 5 and key in e.refinements
    if refined and key in {"cryo", "geo"}: e.block = min(30, e.block + 4)
    heal = min(MAX_HP - e.hp, card.heal + (3 if refined and key == "hydro" else 0))
    e.hp += heal
    if key in {"anemo", "geo"} and previous in {"pyro", "hydro", "cryo", "electro"}:
        reaction = "扩散" if key == "anemo" else "结晶"
        extra = 3 if key == "anemo" else 1
        if key == "anemo":
            e.energy = min(energy_cap(e), e.energy + 1)
        else:
            e.block = min(30, e.block + 4)
    elif key not in {"anemo", "geo"}:
        reaction, extra = REACTIONS.get(frozenset((previous, key)), ("", 0))
        e.aura = None if reaction else key
        if reaction == "感电": e.dot = max(e.dot, 2)
        if reaction == "燃烧": e.dot = 3
        if reaction == "冻结": e.weaken = min(6, e.weaken + 4)
        if reaction == "超载": e.weaken = min(6, e.weaken + 2)
        if reaction == "超导": e.next_bonus = 2
        if reaction == "激化": e.next_bonus = 3
        if reaction == "绽放": e.cores = min(2, e.cores + 1)
    if e.cores and key in {"pyro", "electro"}:
        e.cores -= 1
        reaction = (reaction + " + " if reaction else "") + ("烈绽放" if key == "pyro" else "超绽放")
        extra += 8
    if refined:
        if key == "electro" and reaction: extra += 4
        if key == "anemo" and reaction == "扩散": extra += 4
        if key == "dendro" and reaction: e.block = min(30, e.block + 4)
    doctrine_trigger = ""
    if e.edition >= 3 and reaction and e.doctrine and not e.doctrine_used:
        e.doctrine_used = True
        doctrine_trigger = e.doctrine
        if e.doctrine == "assault": extra += 3
        if e.doctrine == "bastion": e.block = min(30, e.block + 3)
        if e.doctrine == "flow": e.energy = min(energy_cap(e), e.energy + 1)
    buff = SECRET_POWER if e.secret in {"active", "won", "lost"} else 0
    hp_damage, shield_damage = _damage(e, card.damage + e.power + buff + old_bonus + extra
                                      + (4 if refined and key == "pyro" else 0), bool(reaction))
    return e, {"reaction": reaction, "damage": hp_damage, "barrier_damage": shield_damage,
               "heal": heal, "block_after": e.block, "energy_after": e.energy,
               "bonus_damage": extra, "aura_after": e.aura, "doctrine_trigger": doctrine_trigger,
               "refinement": REFINEMENTS[key][0] if refined else ""}


def _record(state, action, **values):
    entry = {"floor": state.round_no, "turn": state.elemental.turn,
             "action": action, "enemy": state.current_monster.monster_id, **values}
    state.elemental.log.append(entry)
    return entry


def _finish_floor(state):
    from .combat import TurnEvent
    e = state.elemental
    rare = state.current_monster.bonus_points
    # 稀有只在击败时额外发50，所有伤害分已逐步入账，绝不在此重复累计。
    state.score += rare
    if rare:
        _record(state, "rare_reward", points=50, text="大伟丘展示完成，额外+50分（仅一次）。")
    points = sum(row.get("points", 0) for row in e.log if row["floor"] == state.round_no)
    state.events.append(TurnEvent(state.round_no, state.current_monster.monster_id, "strike", False,
                                  points, "rare" if rare else "opening"))
    if state.round_no == 10:
        state.status = "cleared"
        if e.edition >= 4:
            e.base_clear_hp = e.hp
            if e.hp > 30:
                e.secret = "offered"
        return "TOWER_CLEARED"
    if state.round_no == 11:
        e.secret, state.status = "won", "cleared"
        return "SECRET_CLEARED"
    state.round_no += 1
    prepare_floor(state)
    return "ROUND_RESOLVED"


def perform(state, action, expected_round):
    """只有玩家确认的动作能进来；返回码，失败绝不修改任何状态。"""
    if state.ruleset == ELEMENTS_RULESET and action in SECRET_ACTIONS:
        return choose_secret(state, action, expected_round)
    if state.status != "active": return "COMBAT_FINISHED"
    if type(expected_round) is not int or expected_round != state.round_no: return "STALE_ROUND"
    if state.ruleset != ELEMENTS_RULESET: return "ELEMENTS_NOT_SUPPORTED"
    if type(action) is not str or action not in ACTIONS: return "UNKNOWN_ELEMENT_ACTION"
    e = state.elemental
    if action in DOCTRINE_ACTIONS and e.edition < 3: return "DOCTRINE_NOT_SUPPORTED"
    if action in PARRY_ACTIONS and e.edition < 3: return "PARRY_NOT_SUPPORTED"
    if action in REFINE_ACTIONS and e.edition < 5: return "REFINEMENT_NOT_SUPPORTED"
    if e.camp_due:
        if action not in CAMP_ACTIONS: return "ELEMENT_CAMP_REQUIRED"
        if action == "element_attune" and e.power >= 3: return "UPGRADE_MAXED"
        if action in DOCTRINE_ACTIONS and e.doctrine is not None: return "DOCTRINE_ALREADY_LEARNED"
        if action in REFINE_ACTIONS and action.removeprefix("element_refine_") not in refinement_offers(state):
            return "REFINEMENT_NOT_OFFERED"
        before = e.hp
        if action == "element_rest": e.hp = min(MAX_HP, e.hp + 12)
        elif action == "element_attune": e.power += 1
        elif action in REFINE_ACTIONS: e.refinements.append(action.removeprefix("element_refine_"))
        else: e.doctrine = action.removeprefix("element_doctrine_")
        e.camps.append({"floor": state.round_no, "action": action})
        e.camp_due = False
        reward = ("休整恢复" + str(e.hp-before) + "生命" if action == "element_rest" else
                  "所有牌基础伤害永久+1" if action == "element_attune" else
                  refinement_text(e, e.refinements[-1]) if action in REFINE_ACTIONS else
                  "学习【" + DOCTRINES[e.doctrine][0] + "】：" + DOCTRINES[e.doctrine][1])
        _record(state, action, points=0, text=f"营地：{reward.rstrip('。')}。本次不再领取其他营地奖励。")
        return "ELEMENT_CAMP_CHOSEN"
    if action in CAMP_ACTIONS: return "ELEMENT_CAMP_NOT_PENDING"
    if action in PARRY_ACTIONS:
        if e.hold_used: return "TACTIC_ALREADY_USED"
        key = action.removeprefix("element_parry_")
        if key not in e.hand: return "ELEMENT_NOT_IN_HAND"
        if e.block >= 30: return "BLOCK_ALREADY_FULL"
        before = e.block
        e.hand.remove(key)
        e.discard.append(key)
        e.block = min(30, e.block + 4)
        e.hold_used = True
        _record(state, action, points=0, block_added=e.block-before,
                text=f"弃【{CARDS[key].name}】招架：获得{e.block-before}护盾，不耗能量、不挂元素、不抽牌。已使用本回合机变，不能再留牌或招架。")
        return "ELEMENT_PARRIED"
    if action in HOLD_ACTIONS:
        if e.edition < 2: return "HOLD_NOT_SUPPORTED"
        if e.hold_used: return "HOLD_ALREADY_USED"
        key = action.removeprefix("element_hold_")
        if key not in e.hand: return "ELEMENT_NOT_IN_HAND"
        e.held, e.hold_used = key, True
        _record(state, action, points=0, text=f"保留【{CARDS[key].name}】至下一回合；不花能量、不抽新牌；本回合不能再留第二张。")
        return "ELEMENT_HELD"
    if action == "element_end":
        dot = e.dot
        e.dot = 0
        damage, _ = _damage(e, dot)
        state.score += damage
        if e.enemy_hp == 0:
            _record(state, action, points=damage, text=f"持续伤害{damage}，敌人被击败，不再反击。")
            return _finish_floor(state)
        label, incoming = enemy_intent(state)
        skateboard = e.secret == "active" and e.turn == 5
        actual = e.hp if skateboard else max(0, incoming - e.weaken - e.block)
        e.hp = max(0, e.hp - actual)
        limit = 5 if e.secret == "active" else 12 if state.round_no == 10 else 8
        _record(state, action, points=damage, hp_lost=actual, text=f"{label}预告{incoming}，削弱{e.weaken}、护盾{e.block}，实际失去{actual}生命；持续伤害{damage}。")
        if e.hp == 0 or e.turn >= limit:
            state.status = "dead"
            if e.secret == "active": e.secret = "lost"
            _record(state, "defeat", points=0, text="莎芙进入滑板状态，无视护盾与削弱秒杀！隐藏挑战失败，原十层通关记录保留。" if skateboard else "生命耗尽，试炼结束。" if e.hp == 0 else f"超过{limit}回合时限，撤离试炼；不是直接致死。")
            return "PLAYER_DEAD"
        e.turn += 1
        e.energy, e.block, e.weaken = energy_cap(e), 0, 0
        e.discard.extend(key for key in e.hand if key != e.held)
        e.hand = [e.held] if e.held is not None else []
        e.held, e.hold_used = None, False
        e.doctrine_used = False
        _draw(e, state.seed, state.round_no)
        return "ELEMENT_TURN_ENDED"
    key = action.removeprefix("element_")
    if key not in e.hand: return "ELEMENT_NOT_IN_HAND"
    if CARDS[key].cost > e.energy: return "ELEMENT_UNAFFORDABLE"
    updated, effect = card_effect(e, key)
    updated.hand.remove(key)
    updated.discard.append(key)
    if updated.held == key:
        updated.held = None  # 已打出的保留牌不再复制；本回合留牌次数仍然消耗。
    state.elemental = e = updated
    state.score += effect["damage"]
    _record(state, action, points=effect["damage"], **effect,
            text=f"{CARDS[key].name}：{effect['reaction'] or '无反应'}，生命伤害{effect['damage']}、削盾{effect['barrier_damage']}，治疗{effect['heal']}，护盾{e.block}。"
                 + (f"【{DOCTRINES[effect['doctrine_trigger']][0]}】本回合首次反应已触发。" if effect['doctrine_trigger'] else "")
                 + (f"精炼【{effect['refinement']}】已生效。" if effect.get('refinement') else ""))
    return _finish_floor(state) if e.enemy_hp == 0 else "ELEMENT_PLAYED"


def choose_secret(state, action, expected_round):
    """邀请只由十层规则产生，显式选择一次；不解析聊天或开放模型写工具。"""
    e = state.elemental
    if type(expected_round) is not int or expected_round != state.round_no: return "STALE_ROUND"
    if (e is None or e.edition < 4 or state.status != "cleared" or state.round_no != 10
            or e.secret != "offered" or e.base_clear_hp is None or e.base_clear_hp <= 30):
        return "SECRET_NOT_OFFERED"
    if action == "element_secret_decline":
        e.secret = "declined"
        _record(state, action, points=0, text="温迪：那就让风记住这次胜利吧。已保留十层通关，隐藏邀请关闭。")
        return "SECRET_DECLINED"
    from .combat import Monster
    e.secret, state.status, state.round_no = "active", "active", 11
    state.current_monster = Monster(SECRET_NAME)
    e.enemy_hp = e.enemy_max_hp = SECRET_HP
    e.barrier, e.energy, e.turn, e.block = SECRET_BARRIER, SECRET_ENERGY, 1, 0
    e.aura, e.cores, e.dot, e.weaken, e.next_bonus = None, 0, 0, 0, 0
    e.held, e.hold_used, e.doctrine_used, e.camp_due = None, False, False, False
    e.discard.extend(e.hand)
    e.hand.clear()
    _draw(e, state.seed, 11)
    _record(state, action, points=0, text="温迪：意气风发啊旅行者。隐藏挑战开始！每回合5能量、抽至6张、基础伤害+2；第5回合结束前击败莎芙，否则滑板秒杀。")
    return "SECRET_ENTERED"


def preview(state):
    e = state.elemental
    phase = "secret_offer" if e.secret == "offered" else "finished" if state.status != "active" else "camp" if e.camp_due else "combat"
    options = []
    if phase == "secret_offer":
        options = [
            {"choice_id": "element_secret_enter", "title": "是 · 迎战莎芙", "enabled": True,
             "description": f"{SECRET_HP}生命/{SECRET_BARRIER}屏障；获得5能量、6张手牌、基础伤害+2。第五回合结束时Boss仍存活将无视护盾秒杀。进入时尝试播放阿黛《雨爱》DJ纯背景音乐；可随时关闭，原十层通关保留。"},
            {"choice_id": "element_secret_decline", "title": "否 · 带着胜利归来", "enabled": True,
             "description": "关闭本次邀请，保留十层通关与分数；不会扣除奖励。"}]
    elif phase == "camp":
        options = [{"choice_id": "element_rest", "title": "风起地休整", "description": f"恢复至多12生命：{e.hp}→{min(MAX_HP,e.hp+12)}；本次不强化。", "enabled": True},
                   {"choice_id": "element_attune", "title": "七曜共鸣", "description": f"七张卡基础伤害永久+1：Lv.{e.power}→{min(3,e.power+1)}；不恢复生命。", "enabled": e.power < 3}]
        if e.edition >= 3 and e.doctrine is None:
            options += [{"choice_id": "element_doctrine_" + key, "title": "流派 · " + title,
                         "description": text + "本局只能学一种且不能更换；本次放弃回血和永久加伤。",
                         "enabled": True, "kind": "doctrine"} for key, (title, text) in DOCTRINES.items()]
        options += [{"choice_id": "element_refine_" + key, "title": "精炼 · " + CARDS[key].name,
                     "description": REFINEMENTS[key][1] + "本局永久生效；此牌不能重复精炼，本次放弃回血、共鸣和流派。",
                     "enabled": True, "kind": "refine", "element": key} for key in refinement_offers(state)]
    elif phase == "combat":
        for key in e.hand:
            card = CARDS[key]
            enabled = card.cost <= e.energy
            after, effect = card_effect(e, key) if enabled else (e, {})
            options.append({"choice_id": "element_" + key, "title": card.name + (" · 精炼" if key in e.refinements else ""),
                "description": (f"{effect['reaction'] or '无反应'}：伤害{effect['damage']}、削盾{effect['barrier_damage']}；治疗{effect['heal']}，护盾→{after.block}。 " if enabled else "能量不足。 ")
                               + (f"触发【{DOCTRINES[effect['doctrine_trigger']][0]}】，结算后能量{after.energy}。 " if effect.get('doctrine_trigger') else "") + card_text(e, key)
                               + (" " + refinement_text(e, key) if key in e.refinements else ""),
                "enabled": enabled, "cost": card.cost, "element": key, "points_awarded": effect.get("damage", 0),
                "focus_after": after.energy, "refined": key in e.refinements, **effect})
        if e.edition >= 2 and not e.hold_used:
            options += [{"choice_id": "element_hold_" + key, "title": "留牌 · " + CARDS[key].name,
                         "description": "本回合限一次，不花能量；这张未使用的牌留到下回合，再抽至4张。若现在打出它，则不再保留。不能带到下一层。",
                         "enabled": True, "kind": "hold", "cost": 0, "element": key,
                         "points_awarded": 0, "focus_after": e.energy} for key in e.hand]
            if e.edition >= 3:
                options += [{"choice_id": "element_parry_" + key, "title": "招架 · " + CARDS[key].name,
                             "description": f"弃掉此牌，获得{min(4,30-e.block)}护盾；不耗能量、不触发元素反应。与留牌共用每回合一次机变，无法再为下一手留牌。",
                             "enabled": e.block < 30, "kind": "parry", "cost": 0, "element": key,
                             "points_awarded": 0, "focus_after": e.energy,
                             "block_after": min(30,e.block+4), "damage": 0} for key in e.hand]
        label, incoming = enemy_intent(state)
        projected = max(0, incoming - e.block - e.weaken)
        options.append({"choice_id": "element_end", "title": "结束回合 · 敌方行动",
            "description": f"持续伤害{e.dot}先结算；若未击败敌人，{label}{incoming}伤，按当前护盾/削弱预计失去{projected}生命。之后{'保留已选牌、弃其余，抽至4张' if e.edition>=2 else '弃掉余牌、抽4'}，能量回3，护盾清零。",
            "enabled": True, "points_awarded": 0, "focus_after": 3, "cost": 0})
    for row in options:
        row.setdefault("points_awarded", 0)
        row.setdefault("focus_after", e.energy)
    rare_points = sum(row.get("points", 0) for row in e.log if row["action"] == "rare_reward")
    report = {"score": state.score, "normal_points": state.score-rare_points, "rare_points": rare_points,
              "camp_points": 0, "successful_attacks": sum(row["action"].startswith("element_") and row.get("damage", 0)>0 for row in e.log),
              "pressure_guards": 0, "blessing_triggers": 0, "completed_rounds": len(state.events),
              "badges": (["七曜征服者"] if state.status == "cleared" else []),
              "reactions": sum(bool(row.get("reaction")) for row in e.log),
              "parries": sum(row["action"] in PARRY_ACTIONS for row in e.log),
              "doctrine_triggers": sum(bool(row.get("doctrine_trigger")) for row in e.log),
              "journal": [{"round_no": row["floor"], "kind": "battle", "title": f"第{row['turn']}回合",
                           "choice": row["text"], "points": row.get("points", 0), "fatal": False, "bonus": 0} for row in e.log[-6:]]}
    label, incoming = enemy_intent(state)
    last = e.log[-1]["text"] if e.log else "七元素已就绪：先看敌人附着和攻击预告，再搭配出牌。"
    trait = ("第五回合结束时滑板秒杀。" if state.round_no==11 else "反应额外削盾4；攻击循环7/12/20，第7回合起每回合再+2。" if state.round_no==10 else
             "不攻击；击败额外50分一次。" if state.current_monster.bonus_points else
             ENEMY_STYLES[state.current_monster.monster_id][2] if e.edition>=2 else "初版普通敌人节奏，原规则保持。")
    forecast = [{"turn": turn, "title": enemy_intent(state, turn)[0], "damage": enemy_intent(state, turn)[1]}
                for turn in range(e.turn, min(e.turn+3, 13 if state.round_no==10 else 9))] if e.edition>=2 and phase=="combat" else []
    view = {"ruleset": ELEMENTS_RULESET, "focus": e.energy, "max_focus": energy_cap(e),
        "stance": "boss" if state.round_no == 10 else "elements", "phase": phase,
        "hint": (f"营地选一次：回血、加伤{'或学习本局唯一流派' if e.edition>=3 and e.doctrine is None else ''}；首领{BOSS_HP}生命/{BOSS_BARRIER}护盾。" if phase == "camp" else
                 ("机变二选一：留1张牌到下回合，或弃1张获得4盾；共用每回合一次机会。 " if e.edition>=3 else
                  "每回合可留1张牌到下回合；看敌人特性与后续攻击预告，准备连招或防重击。 " if e.edition==2 else "") +
                 "每回合抽至4、能量3，可连续出不同元素；结束回合才反击。护盾本回合有效，附着跨回合保留。第10层固定首领，无随机即死。"),
        "options": options, "report": report, "chapter": {"index": 4 if state.round_no==10 else (state.round_no-1)//3+1, "total": 4, "title": "七曜终章" if state.round_no==10 else "元素试炼"},
        "blessing_name": None, "blessing_description": None, "blessing_level": None,
        "combo_ready": False, "relics": [], "routes": [], "deck": None,
        "camp_rounds": [4,7,10], "visited_camps": [row["floor"] for row in e.camps],
        "guard_comments": {"venti": last, "varka": "先看预告。冻结/结晶/护盾能少掉血；攒好附着再爆发。" if state.status=="active" else last},
        "elemental": {"hp": e.hp, "max_hp": MAX_HP, "energy": e.energy, "power": e.power,
            "edition": e.edition, "held": CARDS[e.held].name if e.held else None, "hold_used": e.hold_used,
            "doctrine": ({"key": e.doctrine, "title": DOCTRINES[e.doctrine][0],
                          "description": DOCTRINES[e.doctrine][1], "used": e.doctrine_used} if e.doctrine else None),
            "enemy_trait": None if phase=="camp" else trait, "forecast": forecast,
            "block": e.block, "turn": e.turn, "turn_limit": 12 if state.round_no==10 else 8,
            "enemy_hp": None if phase=="camp" else e.enemy_hp, "enemy_max_hp": None if phase=="camp" else e.enemy_max_hp, "barrier": None if phase=="camp" else e.barrier,
            "aura": ELEMENT_NAMES.get(e.aura, "无"), "cores": e.cores, "dot": e.dot,
            "weaken": e.weaken, "next_bonus": e.next_bonus, "intent": "尚未揭晓" if phase=="camp" else label, "incoming": 0 if phase=="camp" else incoming,
            "last": last, "draw_count": len(e.draw), "discard_count": len(e.discard),
            "hand_count": len(e.hand), "guide": list(REACTION_GUIDE),
            "cards": [{"element": k, "title": c.name, "cost": c.cost, "damage": c.damage+e.power+(SECRET_POWER if state.round_no==11 else 0),
                       "description": card_text(e, k)} for k,c in CARDS.items()]}}
    hidden = e.secret in {"active", "won", "lost"}
    if e.edition >= 5:
        view["elemental"]["refinements"] = [{"element": key, "title": REFINEMENTS[key][0],
                                              "description": REFINEMENTS[key][1]} for key in e.refinements]
        if phase == "camp":
            view["hint"] += "本营地另有固定3个精炼候选，可选1张永久强化；每牌限一次，与其他奖励互斥。"
        for row in view["elemental"]["cards"]:
            key = row["element"]
            row["refined"] = key in e.refinements
            if row["refined"]:
                row["title"] += " · 精炼"
                row["description"] += " " + refinement_text(e, key)
                if key == "pyro": row["damage"] += 4
    view["elemental"].update(secret=e.secret, base_clear_hp=e.base_clear_hp,
                             max_energy=energy_cap(e), max_hand=hand_cap(e),
                             damage_buff=SECRET_POWER if hidden else 0,
                             base_cleared=e.base_clear_hp is not None,
                             turn_limit=5 if hidden else 12 if state.round_no==10 else 8)
    if e.base_clear_hp is not None and "七曜征服者" not in view["report"]["badges"]:
        view["report"]["badges"].append("七曜征服者")
    if e.secret == "won": view["report"]["badges"].append("龙龙挑战征服者")
    if phase == "secret_offer":
        invitation = f"温迪：旅行者，通关后还剩{e.base_clear_hp}生命，要不要进入隐藏关卡？莎芙（龙龙最可爱）正在等你。"
        view["hint"] = invitation
        view["guard_comments"]["venti"] = invitation
        view["elemental"]["last"] = invitation
    if hidden:
        warning = "第5回合结束时若仍存活，莎芙进入滑板状态，无视护盾与削弱秒杀。持续伤害先结算，先击败则获胜。"
        view.update(stance="secret_boss", chapter={"index":5,"total":5,"title":"龙龙隐藏终章"},
                    hint=f"每回合{SECRET_ENERGY}能量、抽至{SECRET_HAND}张，卡牌基础伤害额外+{SECRET_POWER}。"+warning)
        view["elemental"]["enemy_trait"] = warning
        view["elemental"]["forecast"] = ([{"turn":t,"title":enemy_intent(state,t)[0],"damage":enemy_intent(state,t)[1]}
                                            for t in range(e.turn,6)] if phase=="combat" else [])
        if e.doctrine == "flow":
            view["elemental"]["doctrine"]["description"] = "每回合首次反应返还1能量，隐藏关上限5。"
        for row in options:
            if row["choice_id"].startswith("element_hold_"):
                row["description"] = row["description"].replace("抽至4张", "抽至6张")
            if row["choice_id"] == "element_end":
                row["description"] = (warning if e.turn==5 else row["description"].replace("抽至4张", "抽至6张").replace("能量回3", "能量回5"))
                row["focus_after"] = SECRET_ENERGY
    return view
