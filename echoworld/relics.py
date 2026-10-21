"""三选一遗物构筑：原创规则，不复制杀戮尖塔卡牌或资源。

只影响普通遭遇的策略分/专注；不改变怪物抽取、即死或稀有奖励。
"""
from dataclasses import dataclass
from random import Random

SPIRE_RULESET = "spire_v1"
RELIC_ROUNDS = (4, 7, 10)


@dataclass(frozen=True)
class Relic:
    name: str
    path: str
    description: str


RELICS = {
    "relic_ember": Relic("余烬透镜", "破绽进攻", "普通破绽下成功攻击，额外+2分。"),
    "relic_mirror": Relic("磐岩镜片", "蓄势反击", "普通蓄势下防御，额外+2分。"),
    "relic_feather": Relic("回风羽饰", "资源循环", "普通防御额外恢复1专注，上限3。"),
    "relic_lantern": Relic("满弦风灯", "蓄力爆发", "以3专注成功普通攻击，额外+3分。"),
    "relic_echo": Relic("交响铃铛", "交替连招", "普通防御或成功普通攻击，与上次普通行动不同时额外+2分；首次不触发。"),
    "relic_dew": Relic("晨露沙漏", "低专注防守", "以0或1专注普通防御，额外+2分。"),
}


@dataclass(frozen=True)
class RelicEvent:
    round_no: int
    choice_id: str

    def __post_init__(self):
        if type(self.round_no) is not int or self.round_no not in RELIC_ROUNDS:
            raise ValueError("invalid relic round")
        if type(self.choice_id) is not str or self.choice_id not in RELICS:
            raise ValueError("unknown relic")


def offer_relics(seed: int, round_no: int, owned=()) -> tuple[str, ...]:
    """独立随机源，候选不重抽、不影响敌人序列；最多三营地且不重复拥有。"""
    available = [key for key in RELICS if key not in owned]
    return tuple(Random(f"echoworld:{SPIRE_RULESET}:{seed}:{round_no}").sample(available, 3))


def relic_view(key: str) -> dict:
    item = RELICS[key]
    return {"choice_id": key, "title": item.name, "path": item.path,
            "description": item.description, "points_awarded": 0, "enabled": True}


@dataclass(frozen=True)
class RelicEffect:
    bonus: int
    focus_after: int
    triggers: tuple[str, ...] = ()


def relic_effect(owned, stance, choice_id, focus_before, focus_after, successful, previous_choice=None):
    """预览/结算共用纯函数；成功基于加成前攻击，不把空专注攻击变成成功。"""
    if stance not in {"opening", "pressure"}:
        return RelicEffect(0, focus_after)
    bonus, after, triggers = 0, focus_after, []
    for key in owned:
        extra, recovery = 0, 0
        if key == "relic_ember" and choice_id == "strike" and successful and stance == "opening":
            extra = 2
        elif key == "relic_mirror" and choice_id == "guard" and stance == "pressure":
            extra = 2
        elif key == "relic_feather" and choice_id == "guard":
            recovery = min(3, after + 1) - after
        elif key == "relic_lantern" and choice_id == "strike" and successful and focus_before == 3:
            extra = 3
        elif key == "relic_echo" and previous_choice is not None and choice_id != previous_choice:
            if choice_id == "guard" or successful:
                extra = 2
        elif key == "relic_dew" and choice_id == "guard" and focus_before <= 1:
            extra = 2
        bonus += extra
        after += recovery
        if extra or recovery:
            triggers.append(key)
    return RelicEffect(bonus, after, tuple(triggers))
