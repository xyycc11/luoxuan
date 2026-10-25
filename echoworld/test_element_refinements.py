"""营地精炼：真实规则结算、旧局恢复、公开信息及临时SQL事务。"""
import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import lru_cache

from fastapi.testclient import TestClient

from echoworld import test_elements
from echoworld.api import create_app
from echoworld.combat import start_combat
from echoworld.database import GameSave, pack, unpack
from echoworld.elements import (CARDS, DECK, ELEMENTS_RULESET, ElementState,
                                card_effect, card_text, perform, preview, refinement_offers)
from echoworld.game_ai import default_selection, visible_packet
from echoworld.state import GameState
from echoworld.verify_elements import choose


@lru_cache(maxsize=3)
def first_camp(seed=2):
    state = start_combat(GameState("p", "w", has_permit=True), seed,
                         ruleset=ELEMENTS_RULESET, elements_edition=5)
    for _ in range(180):
        if state.round_no == 4 and state.elemental.camp_due:
            return state
        if state.status != "active":
            raise AssertionError("fixture did not reach first camp")
        perform(state, choose(state), state.round_no)
    raise AssertionError("fixture timed out")


def camp(seed=2):
    return copy.deepcopy(first_camp(seed))


def roundtrip(state):
    game = GameState(state.player_id, state.world_id, has_permit=True)
    return unpack(GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(state)))))[1]


class RefinementRules(unittest.TestCase):
    def test_fixed_three_offers_and_preview_restore_do_not_reroll(self):
        state = camp()
        before = copy.deepcopy(state)
        offers = refinement_offers(state)
        self.assertEqual(len(set(offers)), 3)
        for _ in range(3):
            view = preview(state)
            self.assertEqual([o["element"] for o in view["options"] if o.get("kind") == "refine"], offers)
            self.assertIsNone(view["elemental"]["enemy_hp"])
        self.assertEqual(refinement_offers(roundtrip(state)), offers)
        self.assertEqual(state, before)

    def test_reward_is_exclusive_persistent_and_keeps_seven_cards(self):
        state = camp()
        key = refinement_offers(state)[0]
        hp, power = state.elemental.hp, state.elemental.power
        self.assertEqual(perform(state, "element_refine_" + key, 4), "ELEMENT_CAMP_CHOSEN")
        self.assertEqual((state.elemental.hp, state.elemental.power), (hp, power))
        self.assertEqual(state.elemental.refinements, [key])
        self.assertEqual(Counter(state.elemental.hand + state.elemental.draw + state.elemental.discard), Counter(DECK))
        self.assertEqual(roundtrip(state), state)
        before = copy.deepcopy(state)
        for action in ("element_rest", "element_refine_" + key):
            self.assertEqual(perform(state, action, 4), "ELEMENT_CAMP_NOT_PENDING")
            self.assertEqual(state, before)

    def test_unoffered_and_stale_choice_are_atomic(self):
        state = camp()
        absent = next(key for key in DECK if key not in refinement_offers(state))
        before = copy.deepcopy(state)
        self.assertEqual(perform(state, "element_refine_" + absent, 4), "REFINEMENT_NOT_OFFERED")
        self.assertEqual(perform(state, "element_refine_" + refinement_offers(state)[0], 3), "STALE_ROUND")
        self.assertEqual(state, before)

    def test_all_seven_effects_conditions_and_caps(self):
        for key in DECK:
            with self.subTest(element=key):
                e = copy.deepcopy(camp().elemental)
                e.hp, e.block, e.power, e.barrier = 10, 0, 0, 0
                e.enemy_hp = e.enemy_max_hp = 90
                e.aura = "hydro" if key != "hydro" else None
                before = copy.deepcopy(e)
                normal, base = card_effect(e, key)
                self.assertEqual(e, before)
                e.refinements = [key]  # Pure effect fixture; earned-state validation is tested separately.
                before = copy.deepcopy(e)
                refined, effect = card_effect(e, key)
                self.assertEqual(e, before)
                self.assertTrue(effect["refinement"])
                expected_text = {"hydro": "恢复5生命", "cryo": "获得6护盾", "geo": "获得9护盾"}
                if key in expected_text:
                    self.assertIn(expected_text[key], card_text(e, key))
                self.assertEqual(refined.energy, normal.energy)
                if key == "hydro":
                    self.assertEqual((base["heal"], effect["heal"]), (2, 5))
                elif key in {"pyro", "electro", "anemo"}:
                    self.assertEqual(effect["damage"] - base["damage"], 4)
                else:
                    self.assertEqual(refined.block - normal.block, 4)
                if key in {"electro", "anemo", "dendro"}:
                    e.aura, e.cores = None, 0
                    upgraded, effect = card_effect(e, key)
                    e.refinements = []
                    plain, base = card_effect(e, key)
                    self.assertEqual((effect["damage"], upgraded.block), (base["damage"], plain.block))
        e.refinements, e.hp = ["hydro"], 43
        self.assertEqual(card_effect(e, "hydro")[1]["heal"], 1)
        e.refinements, e.block = ["geo"], 29
        self.assertEqual(card_effect(e, "geo")[0].block, 30)

    def test_preview_and_actual_refined_card_have_identical_effects(self):
        state = camp()
        key = refinement_offers(state)[0]
        perform(state, "element_refine_" + key, 4)
        other = [k for k in DECK if k != key]
        state.elemental.hand, state.elemental.draw, state.elemental.discard = [key] + other[:3], other[3:], []
        before = copy.deepcopy(state)
        option = next(o for o in preview(state)["options"] if o["choice_id"] == "element_" + key)
        self.assertTrue(option["refined"])
        self.assertIn("精炼", option["title"])
        self.assertEqual(state, before)
        perform(state, "element_" + key, 4)
        log = next(row for row in reversed(state.elemental.log) if row["action"] == "element_" + key)
        for field in ("damage", "barrier_damage", "heal", "refinement"):
            self.assertEqual(log[field], option[field])
        self.assertEqual(roundtrip(state), state)

    def test_old_editions_without_field_restore_without_refinement(self):
        for edition in (1, 2, 3, 4):
            state = camp()
            state.elemental.edition = edition
            before = copy.deepcopy(state)
            self.assertEqual(refinement_offers(state), [])
            self.assertNotIn("refinements", preview(state)["elemental"])
            self.assertEqual(perform(state, "element_refine_pyro", 4), "REFINEMENT_NOT_SUPPORTED")
            self.assertEqual(state, before)
            data = asdict(state.elemental)
            data.pop("refinements")
            self.assertEqual(ElementState(**data).refinements, [])

    def test_unearned_duplicate_unknown_and_old_edition_rewards_are_rejected(self):
        for bad in (["pyro"], ["pyro", "pyro"], ["bad"], list(DECK[:4])):
            data = asdict(camp().elemental)
            data["refinements"] = bad
            with self.assertRaises(ValueError):
                ElementState(**data)
        state = camp()
        perform(state, "element_refine_" + refinement_offers(state)[0], 4)
        data = asdict(state.elemental)
        data["edition"] = 4
        with self.assertRaises(ValueError):
            ElementState(**data)

    def test_advisor_has_only_current_offers_and_cannot_change_state(self):
        state = camp()
        before = copy.deepcopy(state)
        packet = visible_packet(GameState("p", "w", has_permit=True), state, 1, "venti", "score", "", "advice")
        self.assertIn("element_refinement", json.dumps(packet))
        self.assertNotIn('"seed"', json.dumps(packet))
        action = default_selection(packet)["action_id"]
        self.assertIn(action, {o["choice_id"] for o in preview(state)["options"] if o["enabled"]})
        self.assertEqual(state, before)

    def test_full_run_with_three_distinct_refinements_restores_after_every_action(self):
        state = start_combat(GameState("p", "w", has_permit=True), 2,
                             ruleset=ELEMENTS_RULESET, elements_edition=5)
        for _ in range(600):
            if state.status != "active":
                break
            action = ("element_refine_" + refinement_offers(state)[0]
                      if state.elemental.camp_due else choose(state))
            perform(state, action, state.round_no)
            state = roundtrip(state)
        self.assertEqual(state.status, "cleared")
        self.assertEqual(len(set(state.elemental.refinements)), 3)

    def test_refinement_survives_secret_entry_without_changing_boss_or_deadline(self):
        state = camp()
        key = refinement_offers(state)[0]
        perform(state, "element_refine_" + key, 4)
        for _ in range(600):
            if state.status != "active":
                break
            perform(state, choose(state), state.round_no)
        self.assertEqual(state.elemental.secret, "offered")
        self.assertEqual(perform(state, "element_secret_enter", 10), "SECRET_ENTERED")
        e = state.elemental
        self.assertEqual((e.enemy_hp, e.barrier, e.energy, len(e.hand)), (210, 32, 5, 6))
        self.assertEqual(e.refinements, [key])
        self.assertEqual(preview(state)["elemental"]["turn_limit"], 5)
        self.assertEqual(roundtrip(state), state)
        # Fifth turn still kills through shield: refinement cannot bypass the explicit deadline.
        e.turn, e.hp, e.block, e.weaken, e.dot = 5, 44, 30, 6, 0
        self.assertEqual(perform(state, "element_end", 11), "PLAYER_DEAD")
        self.assertEqual((state.elemental.hp, state.elemental.secret), (0, "lost"))


class RefinementApi(unittest.TestCase):
    setUp = test_elements.ElementApi.setUp
    tearDown = test_elements.ElementApi.tearDown
    state = test_elements.ElementApi.state
    post = test_elements.ElementApi.post
    body = test_elements.ElementApi.body

    def test_web_new_start_uses_fifth_edition(self):
        for action in ("accept", "submit", "tower_elements"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        self.assertEqual(self.state()["combat"]["elemental"]["edition"], 5)

    def test_legacy_sql_editions_without_new_field_do_not_upgrade_on_restart(self):
        for edition in (1, 2, 3, 4):
            self.seed_camp()
            with self.client.app.state.store.sessions.begin() as db:
                row = db.get(GameSave, "world-alice")
                payload = copy.deepcopy(row.combat)
                payload["elemental"]["edition"] = edition
                payload["elemental"].pop("refinements")
                row.combat = payload
            self.client.__exit__(None, None, None)
            self.client = TestClient(create_app(database_url=self.url))
            self.client.__enter__()
            view = self.state()["combat"]
            self.assertEqual(view["elemental"]["edition"], edition)
            self.assertNotIn("refinements", view["elemental"])
            self.assertFalse(any(o.get("kind") == "refine" for o in view["options"]))

    def seed_camp(self):
        self.state()
        with self.client.app.state.store.sessions.begin() as db:
            row = db.get(GameSave, "world-alice")
            game, _ = unpack(row)
            game.has_permit = True
            state = camp()
            state.player_id, state.world_id = game.player_id, game.world_id
            pack(row, game, state)

    def test_api_earned_reward_receipt_stale_rejection_and_restart(self):
        self.seed_camp()
        option = next(o for o in self.state()["combat"]["options"] if o.get("kind") == "refine")
        body = self.body(option["choice_id"], "refine-once")
        for injected in ({"refinements": ["pyro"]}, {"power": 3}, {"edition": 5}):
            self.assertEqual(self.post({**body, **injected}).status_code, 422)
        first = self.post(body)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["code"], "ELEMENT_CAMP_CHOSEN")
        self.assertEqual(self.post(body).json(), first.json())
        self.assertEqual(self.post({**body, "request_id": "again"}).status_code, 409)
        saved = self.state()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.assertEqual(self.state(), saved)

    def test_two_stores_can_only_commit_one_camp_reward(self):
        self.seed_camp()
        option = next(o for o in self.state()["combat"]["options"] if o.get("kind") == "refine")
        bodies = [self.body(option["choice_id"]), self.body("element_rest")]
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(self.post, bodies[0]),
                        pool.submit(other.post, self.base + "/action", headers=self.headers, json=bodies[1])]
                self.assertEqual(sorted(j.result().status_code for j in jobs), [200, 409])
        self.assertEqual(self.state()["version"], bodies[0]["expected_version"] + 1)
        with self.client.app.state.store.sessions() as db:
            _, state = unpack(db.get(GameSave, "world-alice"))
            self.assertEqual(len(state.elemental.camps), 1)
