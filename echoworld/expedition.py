"""原创远征：三处营地、章节和战绩反馈；无模型、无数据库副作用。"""
from dataclasses import dataclass


EXPEDITION_RULESET = "expedition_v1"
CAMP_ROUNDS = (4, 7, 10)  # 完成3/6/9轮后，先做选择再揭晓下一轮敌人。
CAMP_CHOICES = ("camp_rest", "camp_forge", "camp_cash")
MAX_BLESSING_LEVEL = 4
CHAPTERS = ((1, 3, "风起石阶"), (4, 6, "林间回响"),
            (7, 9, "高塔回风"), (10, 10, "塔顶终章"))
CAMP_NAMES = {4: "风栖营地", 7: "古旧符文台", 10: "塔顶前的篝火"}


@dataclass(frozen=True)
class CampEvent:
    round_no: int
    choice_id: str
    focus_before: int
    focus_after: int
    level_before: int
    level_after: int
    points_awarded: int = 0

    def __post_init__(self):
        if type(self.round_no) is not int or self.round_no not in CAMP_ROUNDS:
            raise ValueError("invalid camp round")
        if self.choice_id not in CAMP_CHOICES:
            raise ValueError("invalid camp choice")
        if any(type(value) is not int or not 0 <= value <= 3
               for value in (self.focus_before, self.focus_after)):
            raise ValueError("invalid camp focus")
        if any(type(value) is not int or not 1 <= value <= MAX_BLESSING_LEVEL
               for value in (self.level_before, self.level_after)):
            raise ValueError("invalid blessing level")
        expected = camp_effect(self.choice_id, self.focus_before, self.level_before)
        if (not expected["enabled"] or type(self.points_awarded) is not int or
                (self.points_awarded, self.focus_after, self.level_after) !=
                (expected["points_awarded"], expected["focus_after"], expected["level_after"])):
            raise ValueError("camp event does not match authoritative rules")


def camp_effect(choice_id: str, focus: int, level: int) -> dict:
    """选项预览与结算共用；明确即时收益和长线取舍。"""
    if choice_id == "camp_rest":
        after = min(3, focus + 2)
        title, description = "整顿呼吸", f"专注{focus}→{after}/3；不加分、不强化祝福。"
        points, next_level, enabled = 0, level, True
    elif choice_id == "camp_forge":
        enabled = level < MAX_BLESSING_LEVEL
        next_level = min(MAX_BLESSING_LEVEL, level + 1)
        title = "共鸣符文"
        description = f"祝福Lv.{level}→Lv.{next_level}；触发额外分{level + 1}→{next_level + 1}，专注不恢复。"
        points, after = 0, focus
        if not enabled:
            description = "祝福已达Lv.4上限，不能重复强化。"
    elif choice_id == "camp_cash":
        enabled = focus >= 1
        points, after, next_level = (3, focus - 1, level) if enabled else (0, focus, level)
        title = "记录战功"
        description = f"消耗1专注，立即+3分；专注{focus}→{after}/3，不强化祝福。" if enabled else "需要至少1专注；当前不能选择。"
    else:
        raise ValueError("unknown camp choice")
    return {"choice_id": choice_id, "title": title, "description": description,
            "points_awarded": points, "focus_after": after, "level_after": next_level,
            "enabled": enabled}


def chapter_info(round_no: int) -> dict:
    start, end, title = next(row for row in CHAPTERS if row[0] <= round_no <= row[1])
    return {"title": title, "start": start, "end": end,
            "index": next(i for i, row in enumerate(CHAPTERS, 1) if row[0] == start), "total": 4}


def run_report(events, camp_events, status: str, score: int) -> dict:
    """只描述已发生记录，稀有大奖和普通策略分分开，不发放道具/跨局奖励。"""
    attacks = sum(event.choice_id == "strike" and event.stance in {"opening", "pressure"}
                  and event.points_awarded > 0 and not event.fatal for event in events)
    guards = sum(event.choice_id == "guard" and event.stance == "pressure" and not event.fatal for event in events)
    triggers = sum(event.blessing_bonus_points > 0 for event in events)
    rare = sum(event.points_awarded for event in events if event.stance == "rare")
    normal = sum(event.points_awarded for event in events if event.stance in {"opening", "pressure"})
    camp_points = sum(event.points_awarded for event in camp_events)
    badges = []
    for condition, name in ((attacks >= 3, "果断出击"), (guards >= 2, "稳阵骑士"),
                            (triggers >= 3, "符文共鸣"), (rare > 0, "奇遇见证"),
                            (status == "cleared", "十轮远行")):
        if condition:
            badges.append(name)
    journal = [{"round_no": event.round_no, "kind": "battle",
                "title": event.monster_id, "choice": "攻击" if event.choice_id == "strike" else "防御",
                "points": event.points_awarded, "fatal": event.fatal,
                "bonus": event.blessing_bonus_points} for event in events]
    journal += [{"round_no": event.round_no, "kind": "camp", "title": CAMP_NAMES[event.round_no],
                 "choice": camp_effect(event.choice_id, event.focus_before, event.level_before)["title"],
                 "points": event.points_awarded, "fatal": False, "bonus": 0} for event in camp_events]
    journal.sort(key=lambda row: (row["round_no"], row["kind"] == "battle"))
    return {"score": score, "normal_points": normal, "rare_points": rare, "camp_points": camp_points,
            "successful_attacks": attacks, "pressure_guards": guards, "blessing_triggers": triggers,
            "completed_rounds": sum(not event.fatal for event in events), "badges": badges,
            "journal": journal[-6:]}


def guard_comments(preview: dict, status: str) -> dict:
    """可恢复的本地角色反馈；不是模型聊天，不授予许可/奖励或伪造未来。"""
    phase, stance = preview["phase"], preview["stance"]
    if status == "dead":
        venti, varka = "这一程的风停在这里。留下的战绩仍在，聊天不能把倒下改成通关。", "禁忌遭遇终止了本次试炼。复盘已完成的选择；这不是防御不足造成的。"
    elif status == "cleared":
        venti, varka = "十轮的回声，终于在塔顶合成一首歌。看看这一程留下的战绩吧。", "十轮已完成。普通策略分和稀有奖励分开列出，能更清楚地看见你的打法。"
    elif phase == "blessing":
        venti, varka = "风、火与骑士的回声，挑一个陪你走这一程。", "先选择本局祝福。选定不加分、不消耗轮次；下一步由你决定。"
    elif phase == "camp":
        venti, varka = "歇一歇，还是让符文唱得更响？也能把一份专注写成战功。", "恢复解决眼前的资源，强化押注后续触发，记录战功换即时分数。先看剩余轮数。"
    elif phase == "relic":
        venti, varka = "拾一枚回声，让它与你的祝福合奏。未来的敌人仍藏在风里。", "先看触发条件：破绽进攻、蓄势防守或资源循环。只能选一件，不能重抽；遗物不免死。"
    elif phase == "route":
        venti, varka = "风息小径补足呼吸，试锋险径先付专注。下一段的敌人仍未揭晓。", "按本章剩余轮数判断：接挑战消耗1专注，成功普通攻击达标才加分；没完成不退还，也不改变即死风险。"
    elif phase == "draft":
        venti, varka = "新的一页招式，也可能挤走熟悉的回声。不想添牌，就把位置留给旧友。", "按专注成本和遗物条件选卡，也可跳过。新卡放牌库顶保证下一手抽到；之后牌组变厚会改变循环。"
    elif stance == "fatal":
        venti, varka = "这是禁忌遭遇。风也不能改写本项目的即死规则。", "明确警告：攻击和防御都会结束本局。营地和祝福不提供免死。"
    elif stance == "rare":
        points = preview["options"][0]["points_awarded"]
        venti, varka = "稀有的客人来了！这次不必比拼打法，两种合法选择都是同一份惊喜。", f"本轮任一合法行动恰好+{points}；不叠加祝福，未行动不领取。"
    elif stance == "classic":
        venti, varka = "这是保留的经典旅程。旧规则照旧，不会突然加上新的符文。", "经典局普通攻击+1，防御+0；不使用专注、祝福或营地。以本局预览为准。"
    elif preview.get("focus") == 0:
        venti, varka = "先收一阵风，再把它送向敌人。没有专注的攻击不会得分。", "专注耗尽。当前普通攻击不得分；防御能恢复资源，请看准确效果。"
    elif stance == "opening":
        venti, varka = "风掀开了一道破绽——抓住它，或把力气留给之后。", "破绽攻击耗1专注。是否出手，结合你的祝福和剩余轮数决定。"
    else:
        venti, varka = "迎着风抢攻，还是站稳一步？这一拍由你来选。", "蓄势攻击耗2专注；防御恢复资源。屏幕已列出两种实际收益。"
    if preview.get("deck") and phase == "combat" and stance in {"opening", "pressure"}:
        venti = "这一拍的招式在手中。选一张与专注、祝福和遗物合奏，余下的留待下次洗回。"
        varka = "只打一张当前可用牌，准确消耗与得分看牌面预览；全手不可用时可应急整备。牌面并不提供免死。"
    return {"venti": venti, "varka": varka}
