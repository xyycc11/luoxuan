"""十回合爬塔的确定性规则内核；模型以后只负责叙事。"""

from dataclasses import dataclass, field
from random import Random

from .state import GameState


MAX_ROUNDS = 10
CHOICES = ("strike", "guard")


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
) -> CombatState:
    """许可由服务端状态核验，玩家输入不能在这里伪造。"""

    if not game.has_permit:
        raise PermissionError("PERMIT_REQUIRED")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if not monsters or any(type(monster.weight) is not int or monster.weight <= 0 for monster in monsters):
        raise ValueError("monsters must have positive integer weights")
    state = CombatState(game.player_id, game.world_id, seed, tuple(monsters))
    state.current_monster = _draw_monster(seed, 1, state.monsters)
    return state


def advance_round(state: CombatState, choice_id: str, expected_round: int) -> TurnResult:
    """验证后结算一次；失败不改状态，死亡优先于第十轮通关。"""

    if state.status != "active":
        return TurnResult("COMBAT_FINISHED")
    if type(expected_round) is not int or expected_round != state.round_no:
        return TurnResult("STALE_ROUND")
    if choice_id not in CHOICES:
        return TurnResult("INVALID_CHOICE")

    monster = state.current_monster
    if monster is None:
        raise RuntimeError("active combat has no monster")
    points = 0 if monster.fatal else (monster.bonus_points or (1 if choice_id == "strike" else 0))
    event = TurnEvent(state.round_no, monster.monster_id, choice_id, monster.fatal, points)
    state.events.append(event)
    if monster.fatal:
        state.status = "dead"
        return TurnResult("PLAYER_DEAD", event)

    state.score += points
    if state.round_no == MAX_ROUNDS:
        state.status = "cleared"
        return TurnResult("TOWER_CLEARED", event)

    state.round_no += 1
    state.current_monster = _draw_monster(state.seed, state.round_no, state.monsters)
    return TurnResult("ROUND_RESOLVED", event)
