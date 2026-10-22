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
CAMP_ACTIONS = ("element_rest", "element_attune") + DOCTRINE_ACTIONS
ACTIONS = tuple("element_" + key for key in DECK) + HOLD_ACTIONS + PARRY_ACTIONS + ("element_end",) + CAMP_ACTIONS
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
    # 缺字段的旧JSON默认初版；只有新开局显式用第二版，不热改旧局。
    edition: int = 1
    held: str | None = None
    hold_used: bool = False
    # 第三版才允许营地学习一种流派；触发次数在本回合内保存。
    doctrine: str | None = None
    doctrine_used: bool = False

    def __post_init__(self):
        if type(self.edition) is not int or self.edition not in {1, 2, 3}:
            raise ValueError("invalid elemental edition")
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
        bounds = {"hp": (0, MAX_HP), "energy": (0, ENERGY), "block": (0, 30),
                  "power": (0, 3), "turn": (1, 12), "enemy_hp": (0, BOSS_HP),
                  "enemy_max_hp": (12, BOSS_HP), "barrier": (0, BOSS_BARRIER), "cores": (0, 2),
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
        if len(self.hand) > 4 or Counter(self.hand + self.draw + self.discard) != Counter(DECK):
            raise ValueError("element cards must conserve exactly seven unique cards")
        if (not isinstance(self.log, list) or len(self.log) > 600 or any(not isinstance(x, dict) for x in self.log)
                or not isinstance(self.camps, list) or len(self.camps) > 3):
            raise ValueError("invalid elemental history")
        for row in self.log:
            if (not {"floor", "turn", "action", "text", "points"} <= row.keys()
                    or type(row["floor"]) is not int or not 1 <= row["floor"] <= 10
                    or type(row["turn"]) is not int or not 1 <= row["turn"] <= 12
                    or row["action"] not in (*ACTIONS, "rare_reward", "defeat")
                    or type(row["text"]) is not str or len(row["text"]) > 300
                    or type(row["points"]) is not int or not 0 <= row["points"] <= BOSS_HP):
                raise ValueError("invalid elemental log entry")
        for row in self.camps:
            if (not isinstance(row, dict) or set(row) != {"floor", "action"}
                    or type(row["floor"]) is not int or row["floor"] not in {4,7,10}
                    or row["action"] not in CAMP_ACTIONS
                    or (self.edition < 3 and row["action"] in DOCTRINE_ACTIONS)):
                raise ValueError("invalid elemental camp record")
        if [row["floor"] for row in self.camps] != [4,7,10][:len(self.camps)]:
            raise ValueError("elemental camps must be sequential")
        if self.power != sum(row["action"] == "element_attune" for row in self.camps):
            raise ValueError("elemental power does not match earned upgrades")
        learned = [row["action"].removeprefix("element_doctrine_") for row in self.camps
                   if row["action"] in DOCTRINE_ACTIONS]
        if learned != ([self.doctrine] if self.doctrine is not None else []):
            raise ValueError("doctrine must match exactly one earned camp choice")


def _draw(e, seed, floor):
    while len(e.hand) < 4:
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
    for name in ("hand", "draw", "discard", "log", "camps"):
        setattr(e, name, list(getattr(e, name)))
    card, previous = CARDS[key], e.aura
    reaction, extra = "", 0
    old_bonus = e.next_bonus
    e.next_bonus = 0
    e.energy -= card.cost
    e.block = min(30, e.block + card.block)
    heal = min(MAX_HP - e.hp, card.heal)
    e.hp += heal
    if key in {"anemo", "geo"} and previous in {"pyro", "hydro", "cryo", "electro"}:
        reaction = "扩散" if key == "anemo" else "结晶"
        extra = 3 if key == "anemo" else 1
        if key == "anemo":
            e.energy = min(ENERGY, e.energy + 1)
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
    doctrine_trigger = ""
    if e.edition >= 3 and reaction and e.doctrine and not e.doctrine_used:
        e.doctrine_used = True
        doctrine_trigger = e.doctrine
        if e.doctrine == "assault": extra += 3
        if e.doctrine == "bastion": e.block = min(30, e.block + 3)
        if e.doctrine == "flow": e.energy = min(ENERGY, e.energy + 1)
    hp_damage, shield_damage = _damage(e, card.damage + e.power + old_bonus + extra, bool(reaction))
    return e, {"reaction": reaction, "damage": hp_damage, "barrier_damage": shield_damage,
               "heal": heal, "block_after": e.block, "energy_after": e.energy,
               "bonus_damage": extra, "aura_after": e.aura, "doctrine_trigger": doctrine_trigger}


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
        return "TOWER_CLEARED"
    state.round_no += 1
    prepare_floor(state)
    return "ROUND_RESOLVED"


def perform(state, action, expected_round):
    """只有玩家确认的动作能进来；返回码，失败绝不修改任何状态。"""
    if state.status != "active": return "COMBAT_FINISHED"
    if type(expected_round) is not int or expected_round != state.round_no: return "STALE_ROUND"
    if state.ruleset != ELEMENTS_RULESET: return "ELEMENTS_NOT_SUPPORTED"
    if type(action) is not str or action not in ACTIONS: return "UNKNOWN_ELEMENT_ACTION"
    e = state.elemental
    if action in DOCTRINE_ACTIONS and e.edition < 3: return "DOCTRINE_NOT_SUPPORTED"
    if action in PARRY_ACTIONS and e.edition < 3: return "PARRY_NOT_SUPPORTED"
    if e.camp_due:
        if action not in CAMP_ACTIONS: return "ELEMENT_CAMP_REQUIRED"
        if action == "element_attune" and e.power >= 3: return "UPGRADE_MAXED"
        if action in DOCTRINE_ACTIONS and e.doctrine is not None: return "DOCTRINE_ALREADY_LEARNED"
        before = e.hp
        if action == "element_rest": e.hp = min(MAX_HP, e.hp + 12)
        elif action == "element_attune": e.power += 1
        else: e.doctrine = action.removeprefix("element_doctrine_")
        e.camps.append({"floor": state.round_no, "action": action})
        e.camp_due = False
        reward = ("休整恢复" + str(e.hp-before) + "生命" if action == "element_rest" else
                  "所有牌基础伤害永久+1" if action == "element_attune" else
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
        actual = max(0, incoming - e.weaken - e.block)
        e.hp = max(0, e.hp - actual)
        limit = 12 if state.round_no == 10 else 8
        _record(state, action, points=damage, hp_lost=actual, text=f"{label}预告{incoming}，削弱{e.weaken}、护盾{e.block}，实际失去{actual}生命；持续伤害{damage}。")
        if e.hp == 0 or e.turn >= limit:
            state.status = "dead"
            _record(state, "defeat", points=0, text="生命耗尽，试炼结束。" if e.hp == 0 else f"超过{limit}回合时限，撤离试炼；不是直接致死。")
            return "PLAYER_DEAD"
        e.turn += 1
        e.energy, e.block, e.weaken = ENERGY, 0, 0
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
                 + (f"【{DOCTRINES[effect['doctrine_trigger']][0]}】本回合首次反应已触发。" if effect['doctrine_trigger'] else ""))
    return _finish_floor(state) if e.enemy_hp == 0 else "ELEMENT_PLAYED"


def preview(state):
    e = state.elemental
    phase = "finished" if state.status != "active" else "camp" if e.camp_due else "combat"
    options = []
    if phase == "camp":
        options = [{"choice_id": "element_rest", "title": "风起地休整", "description": f"恢复至多12生命：{e.hp}→{min(MAX_HP,e.hp+12)}；本次不强化。", "enabled": True},
                   {"choice_id": "element_attune", "title": "七曜共鸣", "description": f"七张卡基础伤害永久+1：Lv.{e.power}→{min(3,e.power+1)}；不恢复生命。", "enabled": e.power < 3}]
        if e.edition >= 3 and e.doctrine is None:
            options += [{"choice_id": "element_doctrine_" + key, "title": "流派 · " + title,
                         "description": text + "本局只能学一种且不能更换；本次放弃回血和永久加伤。",
                         "enabled": True, "kind": "doctrine"} for key, (title, text) in DOCTRINES.items()]
    elif phase == "combat":
        for key in e.hand:
            card = CARDS[key]
            enabled = card.cost <= e.energy
            after, effect = card_effect(e, key) if enabled else (e, {})
            options.append({"choice_id": "element_" + key, "title": card.name,
                "description": (f"{effect['reaction'] or '无反应'}：伤害{effect['damage']}、削盾{effect['barrier_damage']}；治疗{effect['heal']}，护盾→{after.block}。 " if enabled else "能量不足。 ")
                               + (f"触发【{DOCTRINES[effect['doctrine_trigger']][0]}】，结算后能量{after.energy}。 " if effect.get('doctrine_trigger') else "") + card.text,
                "enabled": enabled, "cost": card.cost, "element": key, "points_awarded": effect.get("damage", 0),
                "focus_after": after.energy, **effect})
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
    trait = ("反应额外削盾4；攻击循环7/12/20，第7回合起每回合再+2。" if state.round_no==10 else
             "不攻击；击败额外50分一次。" if state.current_monster.bonus_points else
             ENEMY_STYLES[state.current_monster.monster_id][2] if e.edition>=2 else "初版普通敌人节奏，原规则保持。")
    forecast = [{"turn": turn, "title": enemy_intent(state, turn)[0], "damage": enemy_intent(state, turn)[1]}
                for turn in range(e.turn, min(e.turn+3, 13 if state.round_no==10 else 9))] if e.edition>=2 and phase=="combat" else []
    return {"ruleset": ELEMENTS_RULESET, "focus": e.energy, "max_focus": ENERGY,
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
            "cards": [{"element": k, "title": c.name, "cost": c.cost, "damage": c.damage+e.power,
                       "description": c.text} for k,c in CARDS.items()]}}
