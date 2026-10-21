"""原创章节分岔：稳定补给或先付专注的进攻契约；不改抽怪或生死。"""
from dataclasses import dataclass

ROUTES_RULESET = "spire_routes_v1"
ROUTE_ROUNDS = (1, 4, 7, 10)
ROUTE_CHOICES = ("route_rest", "route_hunt")
ROUTE_NAMES = {"route_rest": "风息小径", "route_hunt": "试锋险径"}


def route_terms(round_no):
    end = min(10, round_no + 2)
    target = min(2, end - round_no + 1)
    return end, target, target * 3


def route_option(choice_id, focus, round_no):
    end, target, reward = route_terms(round_no)
    if choice_id == "route_rest":
        after, enabled = min(3, focus + 1), True
        description = f"立即恢复1专注（{focus}→{after}/3）；不接挑战、不加分。不是免死路线。"
    elif choice_id == "route_hunt":
        enabled = focus >= 1
        after = focus - 1 if enabled else focus
        description = (f"先消耗1专注（{focus}→{after}/3）；第{round_no}-{end}轮成功普通攻击{target}次，"
                       f"达标当轮额外+{reward}分，只领一次。未达标或死亡不退还；稀有遭遇不计进度。")
        if not enabled:
            description = "需要至少1专注才能接挑战；当前不可选。" + description
    else:
        raise ValueError("unknown route choice")
    return {"choice_id": choice_id, "title": ROUTE_NAMES[choice_id], "description": description,
            "focus_after": after, "points_awarded": 0, "enabled": enabled,
            "target": target if choice_id == "route_hunt" else 0,
            "potential_reward": reward if choice_id == "route_hunt" else 0}


@dataclass(frozen=True)
class RouteEvent:
    round_no: int
    choice_id: str
    focus_before: int
    focus_after: int

    def __post_init__(self):
        if type(self.round_no) is not int or self.round_no not in ROUTE_ROUNDS:
            raise ValueError("invalid route round")
        if type(self.choice_id) is not str or self.choice_id not in ROUTE_CHOICES:
            raise ValueError("invalid route choice")
        if any(type(value) is not int or not 0 <= value <= 3
               for value in (self.focus_before, self.focus_after)):
            raise ValueError("invalid route focus")
        effect = route_option(self.choice_id, self.focus_before, self.round_no)
        if not effect["enabled"] or effect["focus_after"] != self.focus_after:
            raise ValueError("route does not match authoritative cost")


def successful_attack(event):
    return (not event.fatal and event.stance in {"opening", "pressure"}
            and event.choice_id == "strike" and event.points_awarded > 0)


def route_progress(route, events, round_no, status):
    end, target, reward = route_terms(route.round_no)
    relevant = [event for event in events if route.round_no <= event.round_no <= end]
    count = sum(successful_attack(event) for event in relevant)
    paid = sum(event.route_bonus_points for event in relevant)
    if route.choice_id == "route_rest":
        label = "补给已领取"
    elif paid:
        label = "挑战已完成"
    elif status != "active" or round_no > end:
        label = "挑战未完成"
    else:
        label = "挑战进行中"
    return {"round_no": route.round_no, "end_round": end, "choice_id": route.choice_id,
            "title": ROUTE_NAMES[route.choice_id], "progress": min(count, target),
            "target": target if route.choice_id == "route_hunt" else 0,
            "potential_reward": reward if route.choice_id == "route_hunt" else 0,
            "earned_points": paid, "status": label,
            "focus_before": route.focus_before, "focus_after": route.focus_after}


def route_bonus(routes, events, round_no, stance, choice_id, successful):
    """达标的普通攻击才计奖；进度从权威事件重算，不信客户端计数。"""
    route = next((row for row in reversed(routes) if row.round_no <= round_no), None)
    if route is None or route.choice_id != "route_hunt":
        return 0
    end, target, reward = route_terms(route.round_no)
    if round_no > end or stance not in {"opening", "pressure"} or choice_id != "strike" or not successful:
        return 0
    relevant = [event for event in events if route.round_no <= event.round_no <= end]
    if any(event.route_bonus_points for event in relevant):
        return 0
    return reward if sum(successful_attack(event) for event in relevant) + 1 == target else 0
