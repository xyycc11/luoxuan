"""固定动作序列的教学演示；不是模型 Agent，也不接收玩家消息。"""

from typing import Any

from .rules import submit_herb_for_permit
from .state import GameState
from .tools import execute_tool


READ_ACTIONS = frozenset({"check_permit", "get_inventory"})
WRITE_ACTION = "submit_herb_for_permit"


def _state_snapshot(state: GameState) -> tuple[Any, ...]:
    """重复检测使用动作执行前的完整内存状态。"""

    return (
        state.player_id,
        state.world_id,
        state.quest_stage,
        state.has_permit,
        state.training_herb_claimed,
        tuple(sorted(state.inventory.items())),
    )


def run_scripted_turn(
    state: GameState, actions: list[str], max_steps: int = 3
) -> dict[str, Any]:
    """执行内部夹具给定的动作，返回观察记录和明确的停止原因。

    finish 不计入执行步数；其他动作最多执行 max_steps 次。
    该函数没有认证，不能作为玩家可调用的权限入口。
    """

    if type(max_steps) is not int or max_steps < 0:
        return {"stop_reason": "INVALID_MAX_STEPS", "steps": 0, "observations": []}

    seen: set[tuple[str, tuple[Any, ...]]] = set()
    observations: list[dict[str, Any]] = []
    steps = 0

    for action in actions:
        if action == "finish":
            return {"stop_reason": "DONE", "steps": steps, "observations": observations}
        if steps >= max_steps:
            return {"stop_reason": "MAX_STEPS", "steps": steps, "observations": observations}
        if not isinstance(action, str) or (action not in READ_ACTIONS and action != WRITE_ACTION):
            return {"stop_reason": "UNKNOWN_ACTION", "steps": steps, "observations": observations}

        key = (action, _state_snapshot(state))
        if key in seen:
            return {"stop_reason": "REPEATED_ACTION", "steps": steps, "observations": observations}
        seen.add(key)

        if action in READ_ACTIONS:
            result = execute_tool(state, action)
        else:
            status = submit_herb_for_permit(state)
            result = {
                "ok": status == "COMPLETED",
                "data": status if status == "COMPLETED" else None,
                "error": None if status == "COMPLETED" else {"code": status, "message": "任务规则拒绝此动作"},
            }

        observations.append({"action": action, "result": result})
        steps += 1
        if not result["ok"]:
            return {"stop_reason": "ACTION_FAILED", "steps": steps, "observations": observations}

    return {"stop_reason": "INPUT_EXHAUSTED", "steps": steps, "observations": observations}
