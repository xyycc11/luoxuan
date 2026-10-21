"""固定seed普通遭遇对照；检验营地取舍影响分数，不代表玩家乐趣或生存率。"""
import json
from .combat import Monster, advance_round, choose_blessing, choose_camp, combat_preview, start_combat
from .expedition import EXPEDITION_RULESET
from .state import GameState


def play(seed: int, policy: str, blessing: str | None = None) -> dict:
    game = GameState("simulation", "isolated", has_permit=True)
    combat = start_combat(game, seed, (Monster("丘丘人"),), ruleset=EXPEDITION_RULESET)
    # 所有策略在同seed使用同一候选、同一祝福、同一怪物与架势序列。
    chosen = blessing or combat.blessing_options[0]
    if choose_blessing(combat, chosen, 1).code != "BLESSING_CHOSEN":
        raise ValueError("requested blessing was not offered")
    while combat.status == "active":
        view = combat_preview(combat)
        if view["phase"] == "camp":
            actions = {"rest": "camp_rest", "forge": "camp_forge", "cash": "camp_cash"}
            action = actions[policy]
            if not next(row for row in view["options"] if row["choice_id"] == action)["enabled"]:
                action = "camp_rest"
            choose_camp(combat, action, combat.round_no)
            continue
        strike = next(row for row in view["options"] if row["choice_id"] == "strike")
        if chosen == "boon_wind":
            attacking = combat.combo_ready and strike["points_awarded"] > 0
        elif chosen == "boon_flame":
            attacking = view["stance"] == "opening" and strike["points_awarded"] > 0
        else:
            attacking = view["stance"] == "opening" and strike["points_awarded"] > 0
        advance_round(combat, "strike" if attacking else "guard", combat.round_no)
    return {"score": combat.score, "blessing": chosen, "level": combat.blessing_level}


def main():
    samples = 600
    policies = ("rest", "forge", "cash")
    results = {policy: [] for policy in policies}
    winners = {policy: 0 for policy in policies}
    by_blessing = {}
    for seed in range(samples):
        rows = {policy: play(seed, policy) for policy in policies}
        best = max(row["score"] for row in rows.values())
        blessing = rows["rest"]["blessing"]
        per_blessing = by_blessing.setdefault(blessing, {policy: [] for policy in policies})
        for policy, row in rows.items():
            results[policy].append(row["score"])
            per_blessing[policy].append(row["score"])
            # 并列最优分别计入，非互斥概率；不窥视未来做玩家决策。
            winners[policy] += row["score"] == best
    print(json.dumps({"samples": samples, "scope": "only_normal_monsters_no_fatal_no_rare",
        "policy": "same_current_stance_combat_policy_only_camp_choices_differ",
        "mean_scores": {key: round(sum(values) / samples, 3) for key, values in results.items()},
        "best_or_tied_runs": winners,
        "by_blessing": {key: {policy: round(sum(values) / len(values), 3)
                               for policy, values in group.items()} for key, group in by_blessing.items()}},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
