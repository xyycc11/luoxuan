"""Day22最小回复边界：事实由代码生成，未知自由文案不直接展示。"""
import json
from dataclasses import dataclass

from .rules import guard_reply
from .state import GameState
from .long_memory import is_preference_question
from .guard_skills import beginner_question, beginner_hint, choose_dialogue


@dataclass(frozen=True)
class CheckedReply:
    reply: str
    policy: str


def facts_reply(game: GameState) -> str:
    # 当前只支持一个固定许可任务；避免让模型从历史中猜测现状。
    stages = {"not_started": "尚未领取", "active": "进行中", "completed": "已完成"}
    items = sorted(game.inventory.items())[:30]
    inventory = json.dumps(dict(items), ensure_ascii=False)
    if len(game.inventory) > 30:
        inventory += "（仅显示前30种物品）"
    return (f"{guard_reply(game, '')}\n背包：{inventory}。"
            f"许可任务：{stages[game.quest_stage]}。")


def is_fact_question(message: str) -> bool:
    return any(word in message.lower() for word in
               ("许可", "背包", "任务", "进入", "材料", "库存", "herb", "permit", "inventory"))


def check_reply(game: GameState, message: str, candidate: str, *, lore=None, memories=None, persona=None, public_faq=None) -> CheckedReply:
    """关键词只选择模板，不充当安全判断；非白名单文案一律不放行。"""
    if public_faq:
        base = check_reply(game, message, candidate, lore=lore, memories=memories, persona=persona)
        # 此处只能传调用端刚经SQL回查的有效标准FAQ，绝不传待审原文。
        extra = "\n\n已审核公共提示（通用参考，当前存档事实优先）：\n" + "\n".join(
            f"[{row['id']} v{row['version']}] {row['text']}" for row in public_faq)
        return CheckedReply(base.reply + extra, "public_faq_" + base.policy)
    if persona is not None:
        # 游戏事实/私有偏好/授权资料仍由原有代码分支生成，角色只能增加固定口吻。
        if beginner_question(message) or is_fact_question(message):
            text = persona.voice_prefix + "\n" + facts_reply(game) + "\n指引：" + beginner_hint(game)
            return CheckedReply(persona.say(text), "guard_guidance_template")
        if not is_preference_question(message) and not lore:
            return CheckedReply(choose_dialogue(persona, message, candidate), "guard_dialogue_catalog")
        checked = check_reply(game, message, candidate, lore=lore, memories=memories)
        return CheckedReply(persona.say(checked.reply), checked.policy)
    if is_fact_question(message):
        return CheckedReply(facts_reply(game), "facts_template")
    if is_preference_question(message):
        text = "；".join(row["text"] for row in (memories or []))
        return CheckedReply("守卫：" + (text or "暂时没有可用的已确认偏好记录。"), "preference_template")
    if lore:
        # 仅接受调用端已回查的内置公开片段；不直接展示模型自由生成的文案。
        text = "\n\n".join(f"[{row['id'][:8]} · {row['source']}]\n{row['text']}" for row in lore)
        return CheckedReply("守卫：以下是检索到的世界观资料，仅供参考：\n" + text, "lore_extract")
    # 极小、完整字符串白名单；不允许把合法句子与任意后缀拼接。
    if candidate in {"收到", "你好", "你好。", "你好！", "你好呀"}:
        return CheckedReply(candidate, "smalltalk_allowlist")
    return CheckedReply(facts_reply(game), "template_fallback")
