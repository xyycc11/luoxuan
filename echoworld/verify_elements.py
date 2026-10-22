"""固定seed离线策略推演；不使用真实模型，不读玩家数据库。"""
from copy import deepcopy
from random import Random
from .combat import start_combat
from .elements import ELEMENTS_RULESET, CARDS, card_effect, enemy_intent, perform, preview
from .state import GameState


def choose(state, policy="combo"):
    e = state.elemental
    if e.camp_due:
        return "element_rest" if e.hp < 29 else "element_attune"
    legal = [k for k in e.hand if CARDS[k].cost <= e.energy]
    if not legal:
        if e.edition == 2 and not e.hold_used and e.hand and policy not in {"random", "greedy", "combo_no_hold"}:
            # 对当前公开附着/草原核，以恢复3能量后的牌面评估留牌；不看未来抽牌。
            def held_value(k):
                current = deepcopy(e);current.energy=3
                after,effect=card_effect(current,k)
                return effect["damage"]+effect["barrier_damage"]+effect["heal"]+max(0,after.block-e.block)*0.7
            return "element_hold_" + max(e.hand,key=held_value)
        return "element_end"
    if policy == "random":
        return "element_" + Random(f"policy:{state.seed}:{len(e.log)}").choice(legal)
    if policy == "greedy":
        return "element_" + max(legal, key=lambda k: sum(card_effect(e,k)[1][n] for n in ("damage", "barrier_damage")))
    incoming = enemy_intent(state)[1]
    best = (-float("inf"), "element_end")

    def search(current, first=None):
        nonlocal best
        lost = max(0, incoming-current.block-current.weaken)
        killed = current.enemy_hp <= current.dot and current.barrier == 0
        # 只搜索已知四张牌的排列；不窥探牌堆/seed/未来敌人。
        dealt = (e.enemy_hp-current.enemy_hp) + (e.barrier-current.barrier)*0.8
        value = dealt + (current.hp-e.hp)*1.5 - (0 if killed else lost*1.8)
        value += 10 if killed else 0
        value += current.cores*2 + current.next_bonus*0.5 + (current.dot if not killed else 0)
        if first is not None and value > best[0]: best = value, "element_" + first
        for key in current.hand:
            if CARDS[key].cost > current.energy or current.enemy_hp == 0: continue
            after, _ = card_effect(current, key)
            after.hand.remove(key)
            search(after, first or key)
    search(deepcopy(e))
    return best[1]


def run(seed=2, policy="combo", trace=False, edition=1):
    s = start_combat(GameState("demo", "demo", has_permit=True), seed, ruleset=ELEMENTS_RULESET, elements_edition=edition)
    actions = []
    for _ in range(600):
        if s.status != "active": break
        action = choose(s, policy)
        code = perform(s, action, s.round_no)
        if code not in {"ELEMENT_PLAYED", "ELEMENT_TURN_ENDED", "ELEMENT_CAMP_CHOSEN", "ELEMENT_HELD", "ROUND_RESOLVED", "TOWER_CLEARED", "PLAYER_DEAD"}:
            raise AssertionError(code)
        actions.append(action)
        if trace: print(s.elemental.log[-1]["text"])
    assert s.status != "active", "simulation did not terminate"
    return {"seed": seed, "policy": policy, "edition": edition, "status": s.status, "floor": s.round_no,
            "hp": s.elemental.hp, "score": s.score, "actions": len(actions),
            "reactions": preview(s)["report"]["reactions"],
            "holds": sum(row["action"].startswith("element_hold_") for row in s.elemental.log)}, s, actions


if __name__ == "__main__":
    import json
    from statistics import mean
    for policy in ("random", "greedy", "combo_no_hold", "combo"):
        results = [run(seed, policy, edition=2)[0] for seed in range(100)]
        print(json.dumps({"policy": policy, "runs": len(results),
            "cleared": sum(r["status"]=="cleared" for r in results),
            "avg_hp": round(mean(r["hp"] for r in results),2),
            "avg_floor": round(mean(r["floor"] for r in results),2),
            "avg_reactions": round(mean(r["reactions"] for r in results),2),
            "avg_holds": round(mean(r["holds"] for r in results),2)}, ensure_ascii=False))
    print(json.dumps(run(2,edition=2)[0], ensure_ascii=False))
