"""LangGraph管理只读model→tools→model；API可传持久化checkpointer。"""

from langgraph.graph import END, START, StateGraph

from .agent_steps import TurnState, initial_turn, model_step, tools_step, turn_result
from .rules import guard_reply
from .state import GameState


def build_npc_graph(game: GameState, message: str, model, *, checkpointer=None, interrupt_after=None):
    # 模型/可信游戏对象通过闭包传入，不放进玩家可提交的图输入。
    def call_model(turn):
        return {**model_step(turn, model), "node_trace": turn["node_trace"] + ["model"]}

    def run_tools(turn):
        return {**tools_step(turn, game), "node_trace": turn["node_trace"] + ["tools"]}

    def finalize(turn):
        return {"reply": turn["reply"] or guard_reply(game, message),
                "node_trace": turn["node_trace"] + ["finalize"]}

    def after_model(turn):
        return "finalize" if turn["stop_reason"] else "tools"

    def after_tools(turn):
        return "finalize" if turn["stop_reason"] else "model"

    # 未设置reducer：节点返回完整的新列表，字段默认覆盖，不自动追加。
    graph = StateGraph(TurnState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, {"tools": "tools", "finalize": "finalize"})
    graph.add_conditional_edges("tools", after_tools, {"model": "model", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)


def run_graph_turn(game: GameState, message: str, model,
                   max_model_calls: int = 4, max_tool_calls: int = 3,
                   *, history=None, max_context_chars: int = 12000, game_facts=None, lore=None, memories=None,
                   checkpointer=None, thread_id=None, persona=None) -> dict:
    turn = initial_turn(message, max_model_calls, max_tool_calls,
                        history=history, max_context_chars=max_context_chars, game_facts=game_facts,
                        lore=lore, memories=memories, persona=persona)
    graph = build_npc_graph(game, message, model, checkpointer=checkpointer)
    config = {"recursion_limit": 2 * max_model_calls + 5}
    if checkpointer is not None:
        if not thread_id:
            raise ValueError("持久化图必须提供服务端thread_id")
        config["configurable"] = {"thread_id": thread_id}
        snapshot = graph.get_state(config)
        if snapshot.values:
            # 已完成图直接读结果；中断图从下一个节点继续，不从START重跑。
            turn = graph.invoke(None, config=config, durability="sync") if snapshot.next else snapshot.values
        else:
            turn = graph.invoke(turn, config=config, durability="sync")
    else:
        turn = graph.invoke(turn, config=config)
    return {**turn_result(turn, game, message, persona=persona), "node_trace": turn["node_trace"]}
