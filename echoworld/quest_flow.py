"""Day 13 的可交互许可任务流程。

命令分发只选择业务规则；真正的状态修改仍由 rules.py 完成。
"""

from .rules import (
    accept_permit_quest,
    claim_training_herb,
    guard_reply,
    submit_herb_for_permit,
)
from .state import GameState


HELP_TEXT = "可用命令：status、accept、claim、submit、enter、help、exit"


def format_status(state: GameState) -> str:
    """返回玩家可见的任务状态。"""

    return (
        f"任务={state.quest_stage}，herb={state.inventory.get('herb', 0)}，"
        f"许可={'有' if state.has_permit else '无'}"
    )


def handle_quest_command(state: GameState, command: str) -> str:
    """校验并分发一条本地演示命令。"""

    if not isinstance(command, str) or not command.strip():
        return "系统：请输入有效命令。"

    normalized = command.strip().lower()
    if normalized == "help":
        return HELP_TEXT
    if normalized == "status":
        return format_status(state)
    if normalized == "accept":
        result = accept_permit_quest(state)
        return {
            "QUEST_ACCEPTED": "守卫：任务已接受，请领取一份教学 herb。",
            "QUEST_ALREADY_ACTIVE": "守卫：任务已经在进行中。",
            "QUEST_ALREADY_COMPLETED": "守卫：你的许可任务已经完成。",
        }[result]
    if normalized == "claim":
        result = claim_training_herb(state)
        return {
            "TRAINING_HERB_CLAIMED": "守卫：已发放 1 个教学 herb。",
            "TRAINING_HERB_ALREADY_CLAIMED": "守卫：教学 herb 已经领取过。",
            "QUEST_NOT_ACTIVE": "守卫：请先接受许可任务。",
            "QUEST_ALREADY_COMPLETED": "守卫：任务已完成，无需再次领取。",
        }[result]
    if normalized == "submit":
        result = submit_herb_for_permit(state)
        return {
            "COMPLETED": "守卫：材料核验通过，通行许可已发放。",
            "ALREADY_PERMITTED": "守卫：你已经拥有通行许可。",
            "QUEST_NOT_ACTIVE": "守卫：任务尚未激活。",
            "MISSING_HERB": "守卫：你的背包里没有 herb。",
        }[result]
    if normalized == "enter":
        return guard_reply(state, "请求进入")

    return "系统：未知命令。输入 help 查看可用命令。"
