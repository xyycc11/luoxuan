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


DEFAULT_MONSTERS = (
    Monster("mistling", weight=5),
    Monster("stonebeast", weight=3),
    Monster("ember_wisp", weight=2),
    Monster("void_reaper", fatal=True),
    Monster("blood_moon", fatal=True),
)


@dataclass(frozen=True)
class TurnEvent:
    round_no: int
    monster_id: str
    choice_id: str
    fatal: bool


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
    event = TurnEvent(state.round_no, monster.monster_id, choice_id, monster.fatal)
    state.events.append(event)
    if monster.fatal:
        state.status = "dead"
        return TurnResult("PLAYER_DEAD", event)

    if choice_id == "strike":
        state.score += 1
    if state.round_no == MAX_ROUNDS:
        state.status = "cleared"
        return TurnResult("TOWER_CLEARED", event)

    state.round_no += 1
    state.current_monster = _draw_monster(state.seed, state.round_no, state.monsters)
    return TurnResult("ROUND_RESOLVED", event)
