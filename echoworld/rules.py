"""守卫许可和任务规则。

守卫回复只读取服务端状态；只有显式调用任务规则才能发放许可。
玩家消息不能直接修改许可。
"""

from .state import GameState


def can_enter(state: GameState) -> bool:
    """返回玩家是否拥有真实通行许可，不修改状态。"""

    return state.has_permit


def guard_reply(state: GameState, message: str) -> str:
    """根据真实状态回复守卫消息。

    message 目前只作为输入记录，不被解析为授权指令。
    """

    del message  # 明确表示今天的原型不会相信玩家文本中的授权声明。
    if can_enter(state):
        return "守卫：许可核验通过，可以进入。"
    return "守卫：你还没有通行许可。"


def accept_permit_quest(state: GameState) -> str:
    """接受通行许可任务；只有未开始状态会发生变化。"""

    if state.has_permit or state.quest_stage == "completed":
        return "QUEST_ALREADY_COMPLETED"
    if state.quest_stage == "active":
        return "QUEST_ALREADY_ACTIVE"

    state.quest_stage = "active"
    return "QUEST_ACCEPTED"


def claim_training_herb(state: GameState) -> str:
    """为教学任务领取一个 herb；同一存档只允许领取一次。"""

    if state.has_permit or state.quest_stage == "completed":
        return "QUEST_ALREADY_COMPLETED"
    if state.quest_stage != "active":
        return "QUEST_NOT_ACTIVE"
    if state.training_herb_claimed:
        return "TRAINING_HERB_ALREADY_CLAIMED"

    state.inventory["herb"] = state.inventory.get("herb", 0) + 1
    state.training_herb_claimed = True
    return "TRAINING_HERB_CLAIMED"


def submit_herb_for_permit(state: GameState) -> str:
    """交付一个 herb 取得许可；失败时不修改状态。

    这是单进程内存规则，不提供跨请求的事务或并发安全保证。
    """

    if state.has_permit:
        return "ALREADY_PERMITTED"
    if state.quest_stage != "active":
        return "QUEST_NOT_ACTIVE"
    if state.inventory.get("herb", 0) < 1:
        return "MISSING_HERB"

    state.inventory["herb"] -= 1
    state.quest_stage = "completed"
    state.has_permit = True
    return "COMPLETED"
