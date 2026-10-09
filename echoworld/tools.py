"""只读游戏工具：参数由调用端检查，游戏事实始终来自 GameState。"""

from typing import Any

from .state import GameState


def _success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def get_inventory(state: GameState, item_name: str | None = None) -> dict[str, Any]:
    """读取整个背包的副本，或读取指定物品的数量。"""

    if item_name is None:
        return _success(dict(state.inventory))
    if not isinstance(item_name, str) or not item_name.strip():
        return _error("INVALID_ARGUMENT", "item_name 必须是非空字符串")
    return _success(state.inventory.get(item_name.strip(), 0))


def check_permit(state: GameState) -> dict[str, Any]:
    """只读取服务端状态中的许可，不接受玩家声明。"""

    return _success(state.has_permit)


# 仅列出当前允许调用的工具及其参数。新增工具时须显式登记。
_TOOL_PARAMETERS = {
    "get_inventory": {"item_name"},
    "check_permit": set(),
}


def execute_tool(
    state: GameState, tool_name: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """按白名单分发；模型参数不能传入或覆盖 GameState 权威字段。"""

    if not isinstance(tool_name, str) or tool_name not in _TOOL_PARAMETERS:
        return _error("UNKNOWN_TOOL", "不允许调用该工具")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error("INVALID_ARGUMENT", "params 必须是字典")

    unknown = params.keys() - _TOOL_PARAMETERS[tool_name]
    if unknown:
        return _error("INVALID_ARGUMENT", "工具包含不允许的参数")

    if tool_name == "get_inventory":
        return get_inventory(state, params.get("item_name"))
    return check_permit(state)
