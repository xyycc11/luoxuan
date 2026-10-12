"""项目角色Skill：固定路径、只读加载；不是全局助手指令或可执行插件。"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

SKILL_ROOT = Path(__file__).parent / "npc_skills"
SKILL_PATHS = {"venti": "venti-guard/SKILL.md", "varka": "varka-guard/SKILL.md"}
INTENTS = frozenset({"greeting", "introduce", "poem", "freedom", "encourage", "cat", "goodbye", "smalltalk"})


@dataclass(frozen=True)
class GuardSkill:
    npc_id: str
    display_name: str
    version: str
    fingerprint: str
    prompt: str
    voice_prefix: str
    dialogue: Mapping[str, tuple[str, ...]]

    def say(self, text: str) -> str:
        return f"{self.display_name}：{text}"


def load_guard_skill(npc_id: str) -> GuardSkill | None:
    # guard只保留旧教学API/存档兼容模式，网页只展示两位新角色。
    if npc_id == "guard":
        return None
    if npc_id not in SKILL_PATHS:
        raise ValueError("未知守卫")
    raw = (SKILL_ROOT / SKILL_PATHS[npc_id]).read_text(encoding="utf-8")
    if len(raw) > 16000:
        raise ValueError("角色Skill超出大小限制")
    marker = "## Runtime data\n\n```json\n"
    before, sep, tail = raw.partition(marker)
    block, closing, _ = tail.partition("\n```")
    if not sep or not closing:
        raise ValueError("角色Skill缺少Runtime data")
    data = json.loads(block)
    expected = {"npc_id", "display_name", "version", "voice_prefix", "dialogue"}
    if set(data) != expected or data["npc_id"] != npc_id:
        raise ValueError("角色Skill元数据不合法")
    for key in expected - {"dialogue"}:
        if not isinstance(data[key], str) or not data[key].strip() or len(data[key]) > 100:
            raise ValueError("角色Skill文本元数据不合法")
    lines = data["dialogue"]
    if not isinstance(lines, dict) or set(lines) != INTENTS:
        raise ValueError("角色Skill话题不合法")
    for choices in lines.values():
        if (not isinstance(choices, list) or not 1 <= len(choices) <= 4 or
                any(not isinstance(line, str) or not line.strip() or len(line) > 300 for line in choices)):
            raise ValueError("角色Skill话术不合法")
    # 不装载引用区/上游原文；只有本地审定的角色表达与有限话术进入模型上下文。
    prompt = before.split("---", 2)[-1].strip() + "\n[可选角色话术]\n" + block
    return GuardSkill(npc_id, data["display_name"], data["version"],
                      hashlib.sha256(raw.encode()).hexdigest(), prompt, data["voice_prefix"],
                      MappingProxyType({key: tuple(value) for key, value in lines.items()}))


def list_guards() -> list[dict]:
    return [{"npc_id": role.npc_id, "display_name": role.display_name,
             "skill_version": role.version, "skill_fingerprint": role.fingerprint}
            for role in (load_guard_skill(npc_id) for npc_id in SKILL_PATHS)]


def beginner_question(message: str) -> bool:
    return any(word in message.lower() for word in
               ("新手", "怎么", "如何", "下一步", "指引", "攻略", "不会", "爬塔", "战斗", "攻击", "防御", "祝福", "符文", "help", "tutorial"))


def beginner_hint(game) -> str:
    """仅生成建议，不触发动作；不猜测当前战斗进度/未公开抽怪结果。"""
    if game.has_permit:
        return "你已有许可。尚未开塔可点“普通试炼”或“开启祝福试炼”进入爬塔；祝福试炼先从随机两种中选一种，仅影响普通怪得分，不免死。之后按场景选择攻击或防御，规则决定结果，聊天不能改判或复活。"
    if game.quest_stage == "not_started":
        return "先点“接受任务”。之后准备1个herb，再点“交付herb”；获得许可后才能进入爬塔。"
    if game.quest_stage == "completed":
        return "任务已完成但许可未确认；请刷新存档核对状态，我不会凭聊天补发许可。"
    if game.inventory.get("herb", 0) >= 1:
        return "背包已有herb，下一步点“交付herb”，规则成功后会消耗1个herb并发放许可。"
    if not game.training_herb_claimed:
        return "任务进行中，先点“领取herb”，再点“交付herb”。聊天不会替你领取或提交。"
    return "本存档的教学herb已领过，但当前材料不足，不能重复领取；请核对背包，不要重复提交。"


def choose_dialogue(skill: GuardSkill, message: str, candidate: str) -> str:
    # 输入关键词只挑安全话题，不是权限校验；模型文本从不直接显示。
    topics = (("introduce", ("你是谁", "介绍自己", "名字")),
              ("poem", ("诗", "歌", "唱")), ("cat", ("猫",)),
              ("freedom", ("自由", "责任", "骑士")),
              ("encourage", ("难过", "失落", "害怕", "加油", "鼓励", "困难")),
              ("goodbye", ("再见", "拜拜")), ("greeting", ("你好", "hello", "hi")))
    intent = next((name for name, words in topics if any(word in message.lower() for word in words)), "smalltalk")
    variant = 0
    try:
        selected = json.loads(candidate)
    except (ValueError, TypeError):
        selected = None
    if (isinstance(selected, dict) and set(selected) == {"intent", "variant"} and
            isinstance(selected["intent"], str) and selected["intent"] in INTENTS and
            type(selected["variant"]) is int and
            0 <= selected["variant"] < len(skill.dialogue[selected["intent"]])):
        intent, variant = selected["intent"], selected["variant"]
    # API二次回查后可保留图节点已经生成的完整白名单话术（不接受任意后缀）。
    approved = {skill.say(line) for choices in skill.dialogue.values() for line in choices}
    if candidate in approved:
        return candidate
    return skill.say(skill.dialogue[intent][variant])
