"""显式玩家动作；不解析聊天，不加入模型只读工具白名单。"""
from dataclasses import asdict

from .combat import (BLESSED_RULESET, TACTICAL_RULESET, CombatState, advance_round,
                     choose_blessing, choose_camp, choose_relic, choose_route, choose_card, play_card, combat_preview, start_combat)
from .relics import SPIRE_RULESET, RELICS
from .routes import ROUTES_RULESET, ROUTE_CHOICES
from .cards import CARDS_RULESET, CARDS, DRAFT_CHOICES
from .expedition import EXPEDITION_RULESET, CAMP_CHOICES
from .blessings import BLESSINGS
from .narration import narrate
from .rules import accept_permit_quest, claim_training_herb, guard_reply, submit_herb_for_permit
from .state import GameState
from .elements import ELEMENTS_RULESET, ACTIONS as ELEMENT_ACTIONS, perform as perform_element


SUCCESS = {"QUEST_ACCEPTED", "TRAINING_HERB_CLAIMED", "COMPLETED", "ENTER_ALLOWED",
           "COMBAT_STARTED", "BLESSING_CHOSEN", "CAMP_CHOSEN", "RELIC_CHOSEN", "ROUTE_CHOSEN", "CARD_DRAFTED", "ROUND_RESOLVED", "PLAYER_DEAD", "TOWER_CLEARED"}
SUCCESS.update({"ELEMENT_PLAYED", "ELEMENT_TURN_ENDED", "ELEMENT_CAMP_CHOSEN"})


def public_state(game: GameState, combat: CombatState | None) -> dict:
    """公开当前动作效果与危险提示，不泄露seed、完整怪物池或未来遭遇。"""
    preview = combat_preview(combat) if combat else None
    return {"inventory": dict(game.inventory), "quest_stage": game.quest_stage,
            "has_permit": game.has_permit, "training_herb_claimed": game.training_herb_claimed,
            "combat": None if combat is None else {
                "round_no": combat.round_no, "status": combat.status, "score": combat.score,
                "monster_id": (combat.current_monster.monster_id if combat.current_monster else None)
                    if preview["phase"] == "combat" or combat.status != "active" else None,
                **preview}}


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
    elif action in {"tower", "tower_blessed", "tower_expedition", "tower_spire", "tower_routes", "tower_cards", "tower_elements"}:
        if combat is not None:
            code = "COMBAT_ALREADY_STARTED"
        elif not game.has_permit:
            code = "PERMIT_REQUIRED"
        else:
            mode = {"tower": TACTICAL_RULESET, "tower_blessed": BLESSED_RULESET,
                    "tower_expedition": EXPEDITION_RULESET, "tower_spire": SPIRE_RULESET,
                    "tower_routes": ROUTES_RULESET, "tower_cards": CARDS_RULESET,
                    "tower_elements": ELEMENTS_RULESET}[action]
            combat = start_combat(game, seed=seed, ruleset=mode)
            code = "COMBAT_STARTED"
            scene = asdict(narrate(combat))
    elif action in ELEMENT_ACTIONS:
        code = "COMBAT_NOT_STARTED" if combat is None else perform_element(combat, action, expected_round)
        if code in SUCCESS:
            scene = asdict(narrate(combat))
    elif action in BLESSINGS:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = choose_blessing(combat, action, expected_round)
            code = result.code
            if code == "BLESSING_CHOSEN":
                scene = asdict(narrate(combat, result))
    elif action in CAMP_CHOICES:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = choose_camp(combat, action, expected_round)
            code = result.code
            if result.camp_event is not None:
                scene = asdict(narrate(combat, result))
    elif action in RELICS:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = choose_relic(combat, action, expected_round)
            code = result.code
            if result.relic_event is not None:
                scene = asdict(narrate(combat, result))
    elif action in ROUTE_CHOICES:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = choose_route(combat, action, expected_round)
            code = result.code
            if result.route_event is not None:
                scene = asdict(narrate(combat, result))
    elif action in DRAFT_CHOICES or action in CARDS:
        if combat is None:
            code = "COMBAT_NOT_STARTED"
        else:
            result = choose_card(combat, action, expected_round) if action in DRAFT_CHOICES else play_card(combat, action, expected_round)
            code = result.code
            if result.card_draft is not None or result.event is not None:
                scene = asdict(narrate(combat, result))
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
