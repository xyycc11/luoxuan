"""固定普通怪池的章节路线对照；不是全怪通关率或真实玩家乐趣测量。"""
import json
from statistics import mean

from .combat import (Monster, start_combat, choose_blessing, choose_camp,
                     choose_relic, choose_route, advance_round, combat_preview)
from .routes import ROUTES_RULESET
from .state import GameState


def run(seed, policy):
    state = start_combat(GameState("demo", "demo", has_permit=True), seed,
                         (Monster("丘丘人"),), ruleset=ROUTES_RULESET)
    choose_blessing(state, state.blessing_options[0], 1)
    while state.status == "active":
        view = combat_preview(state)
        phase = view["phase"]
        if phase == "camp":
            choose_camp(state, "camp_rest", state.round_no)
        elif phase == "relic":
            choose_relic(state, view["options"][0]["choice_id"], state.round_no)
        elif phase == "route":
            action = "route_hunt" if policy == "hunt" and state.focus >= 1 else "route_rest"
            choose_route(state, action, state.round_no)
        else:
            action = max(view["options"], key=lambda row: (row["points_awarded"], row["focus_after"]))["choice_id"]
            advance_round(state, action, state.round_no)
    report = combat_preview(state)["report"]
    return {"score": state.score, "route_points": report["route_points"],
            "completed": report["routes_completed"]}


def main():
    rows = {policy: [run(seed, policy) for seed in range(200)] for policy in ("rest", "hunt")}
    summary = {policy: {"runs": len(data), "average_score": mean(row["score"] for row in data),
                       "min_score": min(row["score"] for row in data), "max_score": max(row["score"] for row in data),
                       "average_challenge_points": mean(row["route_points"] for row in data),
                       "average_completed": mean(row["completed"] for row in data)}
               for policy, data in rows.items()}
    difference = [b["score"]-a["score"] for a, b in zip(rows["rest"], rows["hunt"])]
    summary["paired"] = {"hunt_higher": sum(value > 0 for value in difference),
                         "rest_higher": sum(value < 0 for value in difference),
                         "equal": sum(value == 0 for value in difference)}
    print(json.dumps({"scope": "200 paired seeds, ordinary monsters only, greedy actions; not optimal/balance/win-rate proof",
                      "results": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
