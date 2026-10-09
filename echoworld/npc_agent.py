"""纯Python基线Loop；Day18与LangGraph共用步骤，CLI仍可独立运行。"""

from .agent_steps import (
    SYSTEM_PROMPT, TOOL_SCHEMAS, initial_turn, model_step, tools_step, turn_result,
)
from .state import GameState


def run_npc_turn(state: GameState, message: str, model,
                 max_model_calls: int = 4, max_tool_calls: int = 3,
                 *, history=None, max_context_chars: int = 12000, persona=None) -> dict:
    turn = initial_turn(message, max_model_calls, max_tool_calls,
                        history=history, max_context_chars=max_context_chars, persona=persona)
    while not turn["stop_reason"]:
        turn.update(model_step(turn, model))
        if turn["stop_reason"]:
            break
        turn.update(tools_step(turn, state))
    return turn_result(turn, state, message, persona=persona)
