"""原创局内祝福：固定规则与独立随机候选，不由模型生成数值。"""
from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class Blessing:
    name: str
    description: str


BLESSINGS = {
    "boon_wind": Blessing("风之余韵", "普通怪防御后，下一次成功普通攻击额外+2分；重复防御不叠加。"),
    "boon_flame": Blessing("烈焰破绽", "普通怪处于破绽时，成功攻击额外+2分。"),
    "boon_knight": Blessing("骑士反击", "普通怪处于蓄势时，防御额外+2分。"),
}


def offer_blessings(seed: int) -> tuple[str, ...]:
    """三选二、无放回；刷新不会重抽，也不消费怪物/架势随机源。"""
    return tuple(Random(f"echoworld:blessings_v1:{seed}").sample(tuple(BLESSINGS), 2))


def blessing_effect(blessing_id, stance, choice_id, base_points, combo_ready):
    """返回额外分与蓄势标记；稀有/即死遇敌永远不吃祝福加成。"""
    if stance not in {"opening", "pressure"}:
        return 0, combo_ready
    if blessing_id == "boon_wind":
        if choice_id == "guard":
            return 0, True
        if base_points > 0 and combo_ready:
            return 2, False
    if blessing_id == "boon_flame" and stance == "opening" and choice_id == "strike" and base_points > 0:
        return 2, combo_ready
    if blessing_id == "boon_knight" and stance == "pressure" and choice_id == "guard":
        return 2, combo_ready
    return 0, combo_ready
