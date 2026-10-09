"""EchoWorld 守卫许可命令行演示。"""

import argparse

from .combat import CHOICES, CombatState, advance_round, start_combat
from .deepseek_agent import DeepSeekAgentModel
from .narration import DeepSeekNarrator, narrate
from .npc_agent import run_npc_turn
from .quest_flow import HELP_TEXT, handle_quest_command
from .rules import guard_reply
from .state import GameState
from .guard_skills import load_guard_skill


def build_demo_state(scenario: str) -> GameState:
    """用演示场景预设状态；正式玩家输入没有修改许可的接口。"""

    if scenario == "no_permit":
        return GameState(player_id="demo-player", world_id="demo-world")
    if scenario == "with_permit":
        return GameState(
            player_id="demo-player",
            world_id="demo-world",
            quest_stage="completed",
            has_permit=True,
        )
    if scenario == "quest":
        return GameState(player_id="demo-player", world_id="demo-world")
    raise ValueError(f"unsupported scenario: {scenario}")


def main() -> None:
    parser = argparse.ArgumentParser(description="EchoWorld 守卫许可演示")
    parser.add_argument(
        "--scenario",
        choices=("no_permit", "with_permit", "quest"),
        default="no_permit",
        help="使用哪一个本地演示状态",
    )
    parser.add_argument("--seed", type=int, default=7, help="本地爬塔演示的固定随机种子")
    parser.add_argument(
        "--narration", choices=("template", "llm"), default="template",
        help="战斗叙事来源；llm需预先设置ECHOWORLD_LLM_API_KEY",
    )
    parser.add_argument("--npc", choices=("rules", "llm"), default="rules",
                        help="NPC问答来源；llm开启只读工具闭环")
    parser.add_argument("--trace", action="store_true", help="显示工具结果和停止原因，不显示密钥")
    parser.add_argument("--guard", choices=("venti", "varka"), default="venti", help="选择温迪或法尔伽守卫")
    args = parser.parse_args()
    state = build_demo_state(args.scenario)
    guard_id = args.guard
    histories = {"venti": [], "varka": []}
    model = DeepSeekNarrator.from_environment() if args.narration == "llm" else None
    if args.narration == "llm" and model is None:
        print("未设置模型密钥；战斗叙事将使用本地模板。")
    npc_model = DeepSeekAgentModel.from_environment() if args.npc == "llm" else None
    if args.npc == "llm" and npc_model is None:
        print("未设置模型密钥；NPC问答使用固定规则，不会请求模型。")

    def show_scene(combat_state: CombatState, result=None) -> None:
        scene = narrate(combat_state, result, model)
        if scene.result_narration:
            print(scene.result_narration)
        if scene.encounter_narration:
            print(scene.encounter_narration)
        if scene.options:
            print("选项：" + " / ".join(f"{option.choice_id}（{option.title}）" for option in scene.options))
        if args.narration == "llm" and scene.source == "template" and model is not None:
            print("模型叙事不可用，本轮已使用模板；战斗结果不受影响。")

    print(f"场景：{args.scenario}，输入 exit 退出。")
    print("输入 ask <问题> 向守卫提问；问答不发许可、不推进任务。")
    print("输入 npc venti / npc varka 切换守卫；当前：" + load_guard_skill(guard_id).display_name)
    if args.scenario == "quest":
        print(HELP_TEXT + "、tower（取得许可后开塔）")
    combat: CombatState | None = None
    while True:
        try:
            message = input("玩家：")
        except EOFError:
            print()
            break
        if message.strip().lower() == "exit":
            break
        if message.strip().lower().startswith("npc "):
            target = message.strip().lower()[4:]
            if target in histories:
                guard_id = target
                print("已切换：" + load_guard_skill(guard_id).display_name)
            else:
                print("只支持 npc venti 或 npc varka。")
            continue
        if message.strip().lower().startswith("ask ") and not (
            combat is not None and combat.status == "active"
        ):
            question = message.strip()[4:].strip()
            print("规则核验：" + guard_reply(state, question))
            answer = run_npc_turn(state, question, npc_model, history=histories[guard_id],
                                  persona=load_guard_skill(guard_id))
            print(answer["reply"])
            histories[guard_id].append({"user": question, "assistant": answer["reply"]})
            histories[guard_id] = histories[guard_id][-6:]
            if args.trace:
                for observation in answer["observations"]:
                    print(f"工具：{observation['tool_name']} → {observation['result']}")
                print(f"闭环：{answer['stop_reason']}，模型调用={answer['model_calls']}，工具调用={answer['tool_calls']}")
            continue
        if args.scenario == "quest":
            command = message.strip().lower()
            if combat is not None and combat.status == "active":
                if command in CHOICES:
                    result = advance_round(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}，分数={combat.score}")
                    show_scene(combat, result)
                elif command == "status":
                    print(f"战斗第{combat.round_no}轮，分数={combat.score}，怪物={combat.current_monster.monster_id}")
                else:
                    print("战斗中只能输入 strike、guard、status 或 exit。")
            elif command == "tower":
                if combat is not None:
                    print("本次爬塔已结束，不能重复开始。")
                elif not state.has_permit:
                    print("守卫：你还没有通行许可。")
                else:
                    combat = start_combat(state, seed=args.seed)
                    print("爬塔开始。")
                    show_scene(combat)
            else:
                print(handle_quest_command(state, message))
        else:
            print(guard_reply(state, message))


if __name__ == "__main__":
    main()
