"""纯离线策略对照；不含即死/稀有怪，不代表真人乐趣或整局通关率。"""
import json

from .combat import DEFAULT_MONSTERS, TACTICAL_RULESET, advance_round, combat_preview, start_combat
from .state import GameState


def compare_policies(samples=1000):
    if type(samples) is not int or samples <= 0:
        raise ValueError("samples must be a positive integer")
    game = GameState("simulation", "temporary", has_permit=True)
    pool = tuple(monster for monster in DEFAULT_MONSTERS if not monster.fatal and not monster.bonus_points)
    totals = {"always_strike": 0, "always_guard": 0, "stance_aware": 0}
    low, high = 100, 0
    for seed in range(samples):
        for policy in totals:
            combat = start_combat(game, seed, monsters=pool, ruleset=TACTICAL_RULESET)
            for round_no in range(1, 11):
                action = "strike" if policy == "always_strike" else "guard"
                if policy == "stance_aware":
                    stance = combat_preview(combat)["stance"]
                    cost = 1 if stance == "opening" else 2
                    # 不看未来怪物，只用当前架势/资源；末轮不再为下一轮储蓄。
                    attack = combat.focus >= cost and (
                        stance == "opening" or combat.focus == 3 or round_no == 10)
                    action = "strike" if attack else "guard"
                advance_round(combat, action, round_no)
            assert combat.status == "cleared" and len(combat.events) == 10
            totals[policy] += combat.score
            if policy == "stance_aware":
                low, high = min(low, combat.score), max(high, combat.score)
    means = {policy: total / samples for policy, total in totals.items()}
    assert means["stance_aware"] > max(means["always_strike"], means["always_guard"])
    return {"samples": samples, "ordinary_monsters_only": True,
            "mean_score": means, "stance_aware_range": [low, high],
            "note": "规则模拟，不含即死/大伟丘，不证明真人满意度、真实模型质量或通关率提升。"}


if __name__ == "__main__":
    print(json.dumps(compare_policies(), ensure_ascii=False, indent=2))
