"""固定seed、仅普通怪的构筑对照；不代表通关率或真人乐趣。"""
import json

from .combat import (DEFAULT_MONSTERS, advance_round, choose_blessing, choose_camp,
                     choose_relic, combat_preview, start_combat)
from .relics import SPIRE_RULESET
from .state import GameState


def compare_builds(samples=200):
    if type(samples) is not int or samples <= 0:
        raise ValueError("samples must be a positive integer")
    priorities = {
        "offense": ("relic_ember", "relic_lantern", "relic_feather", "relic_echo", "relic_mirror", "relic_dew"),
        "defense": ("relic_mirror", "relic_dew", "relic_feather", "relic_echo", "relic_lantern", "relic_ember"),
        "combo": ("relic_echo", "relic_feather", "relic_lantern", "relic_ember", "relic_dew", "relic_mirror"),
    }
    game = GameState("simulation", "temporary", has_permit=True)
    pool = tuple(monster for monster in DEFAULT_MONSTERS if not monster.fatal and not monster.bonus_points)
    output = {}
    for build, order in priorities.items():
        scores, bonuses, triggers = [], [], []
        for seed in range(samples):
            state = start_combat(game, seed, pool, ruleset=SPIRE_RULESET)
            preferred = "boon_knight" if build == "defense" else "boon_flame" if build == "offense" else "boon_wind"
            choose_blessing(state, preferred if preferred in state.blessing_options else state.blessing_options[0], 1)
            while state.status == "active":
                preview = combat_preview(state)
                if preview["phase"] == "camp":
                    choose_camp(state, "camp_forge", state.round_no)
                elif preview["phase"] == "relic":
                    offered = {row["choice_id"] for row in preview["options"]}
                    choose_relic(state, next(key for key in order if key in offered), state.round_no)
                else:
                    action = "guard" if build == "defense" else "strike" if state.focus >= (1 if preview["stance"] == "opening" else 2) else "guard"
                    if build == "combo" and state.events and state.events[-1].choice_id == "strike":
                        action = "guard"
                    advance_round(state, action, state.round_no)
            report = combat_preview(state)["report"]
            assert state.status == "cleared" and len(state.relic_events) == 3
            assert report["score"] == report["normal_points"] + report["camp_points"] + report["rare_points"]
            scores.append(state.score)
            bonuses.append(report["relic_points"])
            triggers.append(report["relic_triggers"])
        output[build] = {"mean_score": round(sum(scores)/samples, 3), "min_score": min(scores), "max_score": max(scores),
                         "mean_relic_points": round(sum(bonuses)/samples, 3), "mean_relic_triggers": round(sum(triggers)/samples, 3)}
    return {"samples_per_build": samples, "scope": "ordinary nonfatal monsters only; no rare jackpots; fixed seeds",
            "notice": "Not a win-rate, optimal-policy, retention or player-enjoyment benchmark.", "builds": output}


if __name__ == "__main__":
    print(json.dumps(compare_builds(), ensure_ascii=False, indent=2))
