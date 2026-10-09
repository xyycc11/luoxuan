"""显式玩家动作；不解析聊天，不加入模型只读工具白名单。"""
from dataclasses import asdict

from .combat import CombatState, advance_round, start_combat
from .narration import narrate
from .rules import accept_permit_quest, claim_training_herb, guard_reply, submit_herb_for_permit
from .state import GameState


SUCCESS = {"QUEST_ACCEPTED", "TRAINING_HERB_CLAIMED", "COMPLETED", "ENTER_ALLOWED",
           "COMBAT_STARTED", "ROUND_RESOLVED", "PLAYER_DEAD", "TOWER_CLEARED"}


def public_state(game: GameState, combat: CombatState | None) -> dict:
    """不泄露随机seed、完整怪物池或致死标记。"""
    return {"inventory": dict(game.inventory), "quest_stage": game.quest_stage,
            "has_permit": game.has_permit, "training_herb_claimed": game.training_herb_claimed,
            "combat": None if combat is None else {
                "round_no": combat.round_no, "status": combat.status, "score": combat.score,
                "monster_id": combat.current_monster.monster_id if combat.current_monster else None}}


def apply_action(game: GameState, combat: CombatState | None, action: str,
                 expected_round: int | None = None, *, seed: int = 2) -> tuple[dict, CombatState | None]:
    """调用端负责短事务保存；seed仅由服务端提供，无外部模型调用。"""
    scene = None
    if action == "accept":
        code = accept_permit_quest(game)
    elif action == "claim":
        code = claim_training_herb(game)
    elif action == "submit":
        code = submit_herb_for_permit(game)
    elif action == "enter":
        code = "ENTER_ALLOWED" if game.has_permit else "PERMIT_REQUIRED"
    elif action == "tower":
        if combat is not None:
            code = "COMBAT_ALREADY_STARTED"
        elif not game.has_permit:
            code = "PERMIT_REQUIRED"
        else:
            combat = start_combat(game, seed=seed)
            code = "COMBAT_STARTED"
            scene = asdict(narrate(combat))
    elif action in {"strike", "guard"}:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = advance_round(combat, action, expected_round)
            code = result.code
            if result.event is not None:
                scene = asdict(narrate(combat, result))
    else:
        code = "UNKNOWN_ACTION"
    return {"ok": code in SUCCESS, "code": code, "rule_reply": guard_reply(game, ""),
            "state": public_state(game, combat), "scene": scene}, combat
