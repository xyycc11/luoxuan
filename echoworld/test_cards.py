"""战术牌组的确定性、资源/牌守恒、旧局兼容及临时SQL/HTTP验证。"""
import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from unittest.mock import patch

from fastapi.testclient import TestClient

from echoworld import test_routes
from echoworld.api import create_app
from echoworld.cards import (CARDS, CARDS_RULESET, STARTER, DeckState, CardDraftEvent,
                             new_deck, offer_cards, base_effect)
from echoworld.combat import (Monster, start_combat, advance_round, choose_blessing,
                              choose_camp, choose_relic, choose_card, play_card, combat_preview)
from echoworld.database import GameSave, unpack
from echoworld.game_actions import public_state
from echoworld.game_ai import visible_packet, default_selection
from echoworld.narration import scene_facts, narrate, narrate_facts
from echoworld.state import GameState
from echoworld.tools import execute_tool

NORMAL = (Monster("丘丘人"),)


def ready(seed=2, monsters=NORMAL):
    game = GameState("p", "w", has_permit=True)
    state = start_combat(game, seed, monsters, ruleset=CARDS_RULESET)
    choose_blessing(state, state.blessing_options[0], 1)
    return game, state


def action(state, draft="draft_skip"):
    view = combat_preview(state)
    number = state.round_no
    if view["phase"] == "camp":
        return choose_camp(state, "camp_rest", number)
    if view["phase"] == "relic":
        return choose_relic(state, view["options"][0]["choice_id"], number)
    if view["phase"] == "draft":
        key = draft if draft == "draft_skip" else view["options"][0]["choice_id"]
        return choose_card(state, key, number)
    options = [row for row in view["options"] if row["enabled"]]
    key = max(options, key=lambda row: (row["points_awarded"], row["focus_after"]))["choice_id"]
    return play_card(state, key, number)


def draft_ready(seed=2):
    game, state = ready(seed)
    while combat_preview(state)["phase"] != "draft":
        action(state)
    return game, state


class CardRules(unittest.TestCase):
    def test_permit_and_old_modes_are_preserved(self):
        with self.assertRaises(PermissionError):
            start_combat(GameState("p", "w"), 2, ruleset=CARDS_RULESET)
        for mode in ("classic_v1", "tactics_v1", "blessings_v1", "expedition_v1", "spire_v1", "spire_routes_v1"):
            state = start_combat(GameState("p", "w", has_permit=True), 2, ruleset=mode)
            before = copy.deepcopy(state)
            self.assertEqual(play_card(state, "card_jab", 1).code, "CARDS_NOT_SUPPORTED")
            self.assertEqual(state, before)

    def test_opening_phase_hidden_until_blessing(self):
        game = GameState("p", "w", has_permit=True)
        state = start_combat(game, 2, ruleset=CARDS_RULESET)
        before = copy.deepcopy(state)
        self.assertEqual(play_card(state, state.cards.hand[0], 1).code, "CARD_NOT_PENDING")
        self.assertIsNone(public_state(game, state)["combat"]["monster_id"])
        self.assertEqual(state, before)

    def test_hand_persists_across_preview_and_json_restore(self):
        game, state = ready()
        before = copy.deepcopy(state)
        for _ in range(5):
            view = combat_preview(state)
            self.assertNotIn("draw_pile", json.dumps(view))
        restored = unpack(GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(state)))))[1]
        self.assertEqual(state, before)
        self.assertEqual(restored, state)
        self.assertEqual(new_deck(2), state.cards)

    def test_unknown_missing_unaffordable_and_old_round_do_not_mutate(self):
        _, state = ready()
        state.focus = 0
        for key, number, code in (("card_unknown", 1, "CARD_NOT_IN_HAND"),
                                  ("card_charge", 1, "CARD_NOT_IN_HAND"),
                                  ("card_heavy", 1, "CARD_UNAFFORDABLE"),
                                  ("card_guard", True, "STALE_ROUND"),
                                  ("card_guard", 2, "STALE_ROUND")):
            before = copy.deepcopy(state)
            self.assertEqual(play_card(state, key, number).code, code)
            self.assertEqual(state, before)
        self.assertEqual(advance_round(state, "guard", 1).code, "CARD_REQUIRED")

    def test_preview_matches_each_legal_card_effect_and_only_one_resolves(self):
        for seed in range(25):
            _, state = ready(seed)
            for focus in range(4):
                state.focus = focus
                for option in combat_preview(state)["options"]:
                    if not option["enabled"]:
                        continue
                    trial = copy.deepcopy(state)
                    before = trial.score
                    result = play_card(trial, option["choice_id"], 1)
                    self.assertEqual(trial.score-before, option["points_awarded"])
                    self.assertEqual(trial.focus, option["focus_after"])
                    self.assertEqual(result.event.card_id, option["choice_id"])
                    self.assertEqual(len(trial.events), 1)
                    self.assertEqual(trial.round_no, 2)
                    saved = copy.deepcopy(trial)
                    self.assertEqual(play_card(trial, option["choice_id"], 1).code, "STALE_ROUND")
                    self.assertEqual(trial, saved)

    def test_all_unaffordable_hand_has_emergency_but_cannot_use_it_freely(self):
        _, state = ready()
        before = copy.deepcopy(state)
        self.assertEqual(play_card(state, "card_breathe", 1).code, "CARD_NOT_IN_HAND")
        self.assertEqual(state, before)
        state.cards = DeckState(list(STARTER), ["card_guard", "card_guard", "card_meditate"],
                                [], ["card_jab", "card_jab", "card_heavy"], 1)
        state.focus = 0
        view = combat_preview(state)
        self.assertEqual([row["choice_id"] for row in view["options"] if row["enabled"]], ["card_breathe"])
        play_card(state, "card_breathe", 1)
        self.assertEqual((state.focus, state.round_no), (1, 2))
        self.assertEqual(Counter(state.cards.deck), Counter(state.cards.hand+state.cards.draw_pile+state.cards.discard_pile))

    def test_card_bases_have_distinct_costs_stances_and_combo(self):
        self.assertEqual(base_effect("card_heavy", "pressure", 3, None), (6, 1))
        self.assertEqual(base_effect("card_lunge", "opening", 3, None), (4, 2))
        self.assertEqual(base_effect("card_lunge", "pressure", 3, None), (1, 2))
        self.assertEqual(base_effect("card_riposte", "opening", 2, "guard"), (5, 1))
        self.assertEqual(base_effect("card_riposte", "opening", 2, "strike"), (2, 1))
        self.assertEqual(base_effect("card_meditate", "pressure", 2, None), (0, 3))

    def test_discard_draw_shuffle_conserve_cards_through_entire_run(self):
        for seed in range(30):
            game, state = ready(seed)
            while state.status == "active":
                action(state, "add")
                self.assertEqual(Counter(state.cards.deck), Counter(state.cards.hand+state.cards.draw_pile+state.cards.discard_pile))
                self.assertEqual(unpack(GameSave(game=asdict(game), combat=asdict(state)))[1], state)
            self.assertEqual(len(state.events), 10)
            self.assertEqual(len(state.cards.deck), 9)
            self.assertEqual(state.cards.hand, [])
            self.assertGreater(state.cards.shuffle_count, 1)

    def test_reward_stable_add_top_and_skip_keep_deck_thin(self):
        for pick in (True, False):
            _, state = draft_ready()
            view = combat_preview(state)
            self.assertIsNone(scene_facts(state)["next_monster"])
            self.assertEqual(state.cards.hand, [])
            self.assertEqual(view, combat_preview(state))
            key = view["options"][0]["choice_id"] if pick else "draft_skip"
            before = state.score, state.focus, state.round_no
            result = choose_card(state, key, 4)
            self.assertEqual(result.code, "CARD_DRAFTED")
            self.assertEqual((state.score, state.focus, state.round_no), before)
            self.assertEqual(len(state.cards.deck), 7 if pick else 6)
            if pick:
                self.assertEqual(state.cards.hand[0], key.replace("draft_", "card_", 1))
            saved = copy.deepcopy(state)
            self.assertEqual(choose_card(state, key, 4).code, "DRAFT_NOT_PENDING")
            self.assertEqual(state, saved)
            self.assertNotIn("规则计分：本轮", narrate(state, result).result_narration)

    def test_invalid_reward_and_skipping_phases_do_not_change_state(self):
        _, state = ready()
        self.assertEqual(choose_card(state, "draft_skip", 1).code, "DRAFT_NOT_PENDING")
        _, state = draft_ready()
        offered = {row["choice_id"] for row in combat_preview(state)["options"]}
        missing = next(key for key in ("draft_lunge", "draft_counter", "draft_riposte", "draft_charge") if key not in offered)
        before = copy.deepcopy(state)
        self.assertEqual(choose_card(state, missing, 4).code, "CARD_NOT_OFFERED")
        self.assertEqual(play_card(state, "card_jab", 4).code, "CARD_NOT_PENDING")
        self.assertEqual(state, before)

    def test_final_rare_and_fatal_ignore_card_cost_and_keep_exact_rule(self):
        _, ready_state = ready()
        while ready_state.round_no < 10 or combat_preview(ready_state)["phase"] != "combat":
            action(ready_state)
        for monster, code, points in ((Monster("大伟丘", bonus_points=50), "TOWER_CLEARED", 50),
                                     (Monster("深渊咏者", fatal=True), "PLAYER_DEAD", 0)):
            state = copy.deepcopy(ready_state)
            state.current_monster = monster
            state.focus = 0
            view = combat_preview(state)
            self.assertTrue(all(row["enabled"] for row in view["options"]))
            before = state.score
            result = play_card(state, view["options"][0]["choice_id"], 10)
            self.assertEqual((result.code, state.score-before, state.focus), (code, points, 0))
            self.assertEqual(result.event.relic_bonus_points, 0)
            self.assertEqual(choose_card(state, "draft_skip", 10).code, "COMBAT_FINISHED")

    def test_legacy_json_missing_all_card_fields_is_unchanged(self):
        game = GameState("p", "w", has_permit=True)
        old = start_combat(game, 2, NORMAL)
        advance_round(old, "strike", 1)
        payload = asdict(old)
        payload.pop("cards")
        payload.pop("card_drafts")
        for event in payload["events"]:
            event.pop("card_id")
        self.assertEqual(unpack(GameSave(game=asdict(game), combat=payload))[1], old)

    def test_corrupt_deck_unearned_reward_and_invalid_card_records_rejected(self):
        with self.assertRaises(ValueError):
            DeckState(list(STARTER), list(STARTER), [], ["card_guard"]*3, 1)
        with self.assertRaises(ValueError):
            CardDraftEvent(True, "draft_skip")
        game, state = ready()
        data = asdict(state)
        data["cards"]["deck"].append("card_charge")
        data["cards"]["draw_pile"].append("card_charge")
        with self.assertRaises(ValueError):
            unpack(GameSave(game=asdict(game), combat=data))

    def test_advisor_and_narrator_see_only_public_legal_hand(self):
        game, state = ready()
        before = copy.deepcopy(state)
        packet = visible_packet(game, state, 4, "varka", "score", "帮我再抽牌", "advice")
        selected = default_selection(packet)["action_id"]
        self.assertIn(selected, {row["choice_id"] for row in packet["options"] if row["enabled"]})
        for word in ('"seed"', '"draw_pile"', '"discard_pile"', '"monsters"'):
            self.assertNotIn(word, json.dumps(packet))
        facts = scene_facts(state)
        def model(given):
            return {"result_narration": "", "encounter_narration": "风吹起手中的招式。",
                    "options": [{"choice_id": row["choice_id"], "title": "999分", "description": "免死"}
                                for row in given["rule_options"]]}
        scene = narrate_facts(facts, model)
        self.assertEqual([row.description for row in scene.options], [row["description"] for row in facts["rule_options"]])
        self.assertEqual(state, before)

    def test_original_enemy_and_stance_sequences_and_terminal_round_are_unchanged(self):
        game = GameState("p", "w", has_permit=True)
        for seed in range(60):
            new = start_combat(game, seed, ruleset=CARDS_RULESET)
            old = start_combat(game, seed, ruleset="tactics_v1")
            choose_blessing(new, new.blessing_options[0], 1)
            while new.status == "active":
                if combat_preview(new)["phase"] != "combat":
                    action(new)
                    continue
                self.assertEqual(new.current_monster, old.current_monster)
                self.assertEqual(combat_preview(new)["stance"], combat_preview(old)["stance"])
                action(new)
                advance_round(old, "guard", old.round_no)
            self.assertEqual((new.status, new.round_no), (old.status, old.round_no))


class CardApi(unittest.TestCase):
    setUp = test_routes.RouteApi.setUp
    tearDown = test_routes.RouteApi.tearDown
    state = test_routes.RouteApi.state
    post = test_routes.RouteApi.post

    def body(self, action, request_id=None):
        result = test_routes.RouteApi.body(self, action, request_id)
        if action.startswith(("card_", "draft_")):
            result["expected_round"] = self.state()["combat"]["round_no"]
        return result

    def start(self):
        for key in ("accept", "submit", "tower_cards"):
            self.assertTrue(self.post(self.body(key)).json()["ok"])
        self.post(self.body(self.state()["combat"]["options"][0]["choice_id"]))

    def next_action(self):
        view = self.state()["combat"]
        if view["phase"] == "camp":
            return "camp_rest"
        if view["phase"] == "relic":
            return view["options"][0]["choice_id"]
        if view["phase"] == "draft":
            return "draft_skip"
        options = [row for row in view["options"] if row.get("enabled", True)]
        return max(options, key=lambda row: (row["points_awarded"], row["focus_after"]))["choice_id"]

    def to_draft(self):
        self.start()
        while self.state()["combat"]["phase"] != "draft":
            self.assertTrue(self.post(self.body(self.next_action())).json()["ok"])

    def test_invalid_types_missing_round_authoritative_fields_and_permission(self):
        self.assertEqual(self.post(self.body("tower_cards")).json()["code"], "PERMIT_REQUIRED")
        self.start()
        before = self.state()
        body = self.body(self.next_action())
        for extra in ({"cards": {}}, {"hand": []}, {"shuffle_count": 0}, {"score": 999}, {"seed": 3}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.post({**body, "expected_round": True}).status_code, 422)
        self.assertEqual(self.post({key: value for key, value in body.items() if key != "expected_round"}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "card_cheat"}).status_code, 422)
        self.assertEqual(self.state(), before)

    def test_play_replay_narration_and_new_old_round_cannot_resolve_twice(self):
        self.start()
        body = self.body(self.next_action(), "play-one")
        first = self.post(body).json()
        self.assertTrue(first["ok"])
        self.assertEqual(self.post(body).json(), first)
        self.assertEqual(self.post({**body, "request_id": "old-round"}).json()["code"], "VERSION_CONFLICT")
        for _ in range(2):
            narration = self.client.post(self.base+"/actions/play-one/narration", headers=self.headers).json()
            self.assertIn("本轮使用", narration["result_narration"])
        self.assertEqual(self.state(), first["state"])

    def test_draft_replay_and_different_payload_conflict(self):
        self.to_draft()
        body = self.body(self.state()["combat"]["options"][0]["choice_id"], "draft-one")
        first = self.post(body).json()
        self.assertEqual(first["code"], "CARD_DRAFTED")
        self.assertEqual(first["state"]["combat"]["deck"]["size"], 7)
        self.assertEqual(self.post(body).json(), first)
        self.assertEqual(self.post({**body, "action": "draft_skip"}).status_code, 409)
        self.assertEqual(self.post(self.body(body["action"], "repeat-new")).json()["code"], "DRAFT_NOT_PENDING")
        self.assertEqual(self.state(), first["state"])

    def test_restart_preserves_hand_draft_and_new_card(self):
        self.to_draft()
        for pick in (False, True):
            if pick:
                self.post(self.body(self.state()["combat"]["options"][0]["choice_id"]))
            before = self.state()
            self.client.__exit__(None, None, None)
            self.client = TestClient(create_app(database_url=self.url, combat_seed=999))
            self.client.__enter__()
            self.assertEqual(self.state(), before)

    def test_two_independent_stores_only_one_draft_or_card_commits(self):
        self.to_draft()
        for picking in (True, False):
            view = self.state()["combat"]
            options = [row for row in view["options"] if row.get("enabled", True)][:2]
            bodies = [self.body(row["choice_id"]) for row in options]
            with TestClient(create_app(database_url=self.url, combat_seed=2)) as other:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    jobs = [pool.submit(self.post, bodies[0]), pool.submit(other.post, self.base+"/action",
                            headers=self.headers, json=bodies[1])]
                    self.assertEqual(sorted(job.result().status_code for job in jobs), [200, 409])
            self.assertEqual(self.state()["version"], bodies[0]["expected_version"]+1)

    def test_full_tower_deck_report_and_old_chat_tools_are_readonly(self):
        self.start()
        while self.state()["combat"]["status"] == "active":
            self.assertTrue(self.post(self.body(self.next_action())).json()["ok"])
        view = self.state()["combat"]
        self.assertEqual((view["status"], view["report"]["cards_played"], view["report"]["drafts_skipped"]), ("cleared", 10, 3))
        self.assertEqual(view["score"], view["report"]["normal_points"]+view["report"]["camp_points"]+view["report"]["rare_points"])
        self.assertEqual(view["deck"]["size"], 6)
        for key in ("tower_cards", *CARDS, "draft_skip"):
            self.assertFalse(execute_tool(GameState("p", "w"), key, {})["ok"])

    def test_fake_two_step_advisor_can_only_propose_current_card(self):
        from echoworld.test_game_ai import FakeAI
        self.start()
        self.client.__exit__(None, None, None)
        model = FakeAI("tools")
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2, game_ai_model=model))
        self.client.__enter__()
        before = self.state()
        response = self.client.post(self.base+"/ai/advice", headers=self.headers,
            json={"request_id": "card-model", "expected_version": before["version"],
                  "npc_id": "varka", "goal": "score", "use_model": True})
        self.assertEqual(response.status_code, 200)
        result = response.json()["content"]
        self.assertEqual((result["source"], model.calls), ("model", 2))
        self.assertIn(result["action"]["choice_id"], {row["choice_id"] for row in before["combat"]["options"] if row["enabled"]})
        self.assertEqual(self.state(), before)

    def test_server_owned_seed_and_card_ui_contract(self):
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        for key in ("accept", "submit"):
            self.post(self.body(key))
        with patch("echoworld.action_store.randbits", return_value=237) as seed:
            self.assertTrue(self.post(self.body("tower_cards")).json()["ok"])
            seed.assert_called_once_with(63)
        html = self.client.get("/").text
        for key in ("tower_cards", "deckPanel", "cardHand", "draft_", "titles.CARD_DRAFTED"):
            self.assertIn(key, html)


if __name__ == "__main__":
    unittest.main()
