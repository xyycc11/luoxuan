"""固定seed纯普通怪的选牌脚本对照，不是最优构筑或玩家满意度测量。"""
import json
from statistics import mean

from .cards import CARDS_RULESET
from .combat import (Monster, start_combat, choose_blessing, choose_camp,
                     choose_relic, choose_card, play_card, combat_preview)
from .state import GameState


def run(seed, policy):
    state = start_combat(GameState("demo", "demo", has_permit=True), seed,
                         (Monster("丘丘人"),), ruleset=CARDS_RULESET)
    choose_blessing(state, state.blessing_options[0], 1)
    while state.status == "active":
        view = combat_preview(state)
        if view["phase"] == "camp":
            choose_camp(state, "camp_rest", state.round_no)
        elif view["phase"] == "relic":
            choose_relic(state, view["options"][0]["choice_id"], state.round_no)
        elif view["phase"] == "draft":
            offered = [row["choice_id"] for row in view["options"]]
            preferred = ("draft_riposte" if state.blessing == "boon_wind" else
                         "draft_counter" if state.blessing == "boon_knight" else "draft_lunge")
            key = (offered[0] if policy == "collect" else
                   preferred if policy == "synergy" and preferred in offered else "draft_skip")
            choose_card(state, key, state.round_no)
        else:
            options = [row for row in view["options"] if row["enabled"]]
            chosen = max(options, key=lambda row: (row["points_awarded"], row["focus_after"]))
            play_card(state, chosen["choice_id"], state.round_no)
    return {"score": state.score, "size": len(state.cards.deck),
            "emergency": sum(row.card_id == "card_breathe" for row in state.events)}


def main():
    rows = {policy: [run(seed, policy) for seed in range(200)] for policy in ("thin", "collect", "synergy")}
    result = {policy: {"runs": len(data), "average_score": mean(row["score"] for row in data),
                       "min_score": min(row["score"] for row in data), "max_score": max(row["score"] for row in data),
                       "average_deck_size": mean(row["size"] for row in data),
                       "average_emergency_turns": mean(row["emergency"] for row in data)} for policy, data in rows.items()}
    difference = [b["score"]-a["score"] for a, b in zip(rows["thin"], rows["collect"])]
    result["collect_vs_thin"] = {"collect_higher": sum(value > 0 for value in difference),
                                  "thin_higher": sum(value < 0 for value in difference),
                                  "equal": sum(value == 0 for value in difference)}
    print(json.dumps({"scope": "200 paired seeds; ordinary monsters only; greedy card play, same camps/relics; not optimal/balance/win rate proof",
                      "results": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
