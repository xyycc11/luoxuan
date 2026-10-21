"""EchoWorld 守卫许可命令行演示。"""

import argparse
from .elements import ELEMENTS_RULESET, ACTIONS as ELEMENT_ACTIONS, perform as perform_element

from .combat import (
    BLESSED_RULESET, CHOICES, MAX_ROUNDS, TACTICAL_RULESET, CombatState,
    advance_round, choose_blessing, choose_camp, choose_relic, choose_route, choose_card, play_card, combat_preview, start_combat,
)
from .relics import SPIRE_RULESET, RELICS
from .routes import ROUTES_RULESET, ROUTE_CHOICES
from .cards import CARDS_RULESET, CARDS, DRAFT_CHOICES
from .expedition import EXPEDITION_RULESET, CAMP_CHOICES
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

    def show_tactics(combat_state: CombatState) -> None:
        preview = combat_preview(combat_state)
        monster_name = "尚未揭晓" if preview["phase"] in {"blessing", "camp", "relic", "route", "draft"} else combat_state.current_monster.monster_id
        print(f"试炼第{combat_state.round_no}/{MAX_ROUNDS}轮，分数={combat_state.score}，"
              f"状态={combat_state.status}，怪物={monster_name}")
        if preview["focus"] is not None:
            resource = "能量" if combat_state.elemental else "专注"
            print(f"{resource}：{preview['focus']}/{preview['max_focus']}")
        else:
            print("经典存档：本局不使用专注。")
        print("规则提示：" + preview["hint"])
        if combat_state.elemental:
            e = preview["elemental"]
            print(f"生命{e['hp']}/{e['max_hp']}，护盾{e['block']}；敌人{e['enemy_hp']}/{e['enemy_max_hp']}，屏障{e['barrier']}；附着{e['aura']}，草原核{e['cores']}；第{e['turn']}回合，预告{e['intent']} {e['incoming']}伤。")
        print(f"章节：{preview['chapter']['title']} · 守卫提示：{preview['guard_comments'][guard_id]}")
        if preview["blessing_name"]:
            print(f"本局祝福：【{preview['blessing_name']}】Lv.{preview['blessing_level']} · {preview['blessing_description']}")
        if preview["relics"]:
            print("本局遗物：" + "、".join(row["title"] for row in preview["relics"]))
        if preview["deck"]:
            deck = preview["deck"]
            print(f"牌组{deck['size']}张 / 手牌{deck['hand_count']} / 抽牌堆{deck['draw_count']} / 弃牌堆{deck['discard_count']}；每轮选1张。")
        for route in preview["routes"]:
            print(f"路线：{route['title']} · 第{route['round_no']}-{route['end_round']}轮 · {route['status']}"
                  + (f" · 成功普通攻击{route['progress']}/{route['target']} · 已获{route['earned_points']}分" if route['target'] else ""))
        for option in preview["options"]:
            print(f"  {option['choice_id']}（{option['title']}）：{option['description']}")
        if combat_state.status != "active":
            report = preview["report"]
            print(f"战绩：普通策略{report['normal_points']} / 营地{report['camp_points']} / 稀有{report['rare_points']}；"
                  f"祝福触发{report['blessing_triggers']}次；纪念印记：{'、'.join(report['badges']) or '本次未获得'}")

    def show_scene(combat_state: CombatState, result=None) -> None:
        scene = narrate(combat_state, result, model)
        if scene.result_narration:
            print(scene.result_narration)
        if scene.encounter_narration:
            print(scene.encounter_narration)
        # 操作效果来自权威规则预览，不由模型文案决定。
        show_tactics(combat_state)
        if args.narration == "llm" and scene.source == "template" and model is not None:
            print("模型叙事不可用，本轮已使用模板；战斗结果不受影响。")

    print(f"场景：{args.scenario}，输入 exit 退出。")
    print("输入 ask <问题> 向守卫提问；问答不发许可、不推进任务。")
    print("输入 npc venti / npc varka 切换守卫；当前：" + load_guard_skill(guard_id).display_name)
    if args.scenario == "quest":
        print(HELP_TEXT + "、tower_elements（七元素反应/首领）、tower（普通试炼）、tower_blessed（祝福试炼）、tower_expedition（章节/营地远征）、tower_spire（遗物构筑）、tower_routes（分岔构筑）、tower_cards（战术牌组）")
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
                if command in ELEMENT_ACTIONS:
                    code = perform_element(combat, command, combat.round_no)
                    print(f"规则结果：{code}，分数={combat.score}")
                    show_scene(combat)
                elif command.startswith("boon_"):
                    result = choose_blessing(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}")
                    show_scene(combat, result)
                elif command in CAMP_CHOICES:
                    result = choose_camp(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}")
                    show_scene(combat, result)
                elif command in RELICS:
                    result = choose_relic(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}")
                    show_scene(combat, result)
                elif command in ROUTE_CHOICES:
                    result = choose_route(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}")
                    show_scene(combat, result)
                elif command in CARDS or command in DRAFT_CHOICES:
                    result = choose_card(combat, command, combat.round_no) if command in DRAFT_CHOICES else play_card(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}，分数={combat.score}")
                    show_scene(combat, result)
                elif command in CHOICES:
                    result = advance_round(combat, command, combat.round_no)
                    print(f"规则结果：{result.code}，分数={combat.score}")
                    show_scene(combat, result)
                elif command == "status":
                    show_tactics(combat)
                else:
                    print("按当前选项输入boon_*/camp_*/relic_*/route_*/card_*/draft_*，非牌组模式可用strike/guard；status 查看，exit 退出。")
            elif command == "status" and combat is not None:
                show_tactics(combat)
            elif command in {"tower", "tower_blessed", "tower_expedition", "tower_spire", "tower_routes", "tower_cards", "tower_elements"}:
                if combat is not None:
                    print("本次爬塔已结束，不能重复开始。")
                elif not state.has_permit:
                    print("守卫：你还没有通行许可。")
                else:
                    mode = {"tower": TACTICAL_RULESET, "tower_blessed": BLESSED_RULESET,
                            "tower_expedition": EXPEDITION_RULESET, "tower_spire": SPIRE_RULESET,
                            "tower_routes": ROUTES_RULESET, "tower_cards": CARDS_RULESET, "tower_elements": ELEMENTS_RULESET}[command]
                    combat = start_combat(state, seed=args.seed, ruleset=mode)
                    print("爬塔开始。七元素模式请用element_*出牌/结束回合；其他模式按各自规则预览行动。")
                    show_scene(combat)
            else:
                print(handle_quest_command(state, message))
        else:
            print(guard_reply(state, message))


if __name__ == "__main__":
    main()
