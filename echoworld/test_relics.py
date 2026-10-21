"""遗物构筑：纯规则与临时SQL/HTTP验证，不访问真实存档或外部模型。"""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.combat import (CLASSIC_RULESET, TACTICAL_RULESET, BLESSED_RULESET,
    CombatState, Monster, TurnEvent, advance_round, choose_blessing, choose_camp,
    choose_relic, combat_preview, start_combat)
from echoworld.database import GameSave, unpack
from echoworld.expedition import EXPEDITION_RULESET
from echoworld.game_actions import apply_action, public_state
from echoworld.game_ai import visible_packet, default_selection
from echoworld.narration import narrate, narrate_facts, scene_facts
from echoworld.relics import (SPIRE_RULESET, RELICS, RelicEvent, offer_relics, relic_effect)
from echoworld.state import GameState
from echoworld.tools import execute_tool

NORMAL = (Monster("丘丘人"),)


def normal_run(seed=2):
    game = GameState("alice", "world-alice", has_permit=True, quest_stage="completed")
    state = start_combat(game, seed, NORMAL, ruleset=SPIRE_RULESET)
    choose_blessing(state, state.blessing_options[0], 1)
    return game, state


def first_draft(seed=2):
    game, state = normal_run(seed)
    for _ in range(3):
        advance_round(state, "guard", state.round_no)
    choose_camp(state, "camp_forge", 4)
    return game, state


class RelicRules(unittest.TestCase):
    def test_permit_and_blessing_still_required(self):
        with self.assertRaises(PermissionError):
            start_combat(GameState("p", "w"), 2, ruleset=SPIRE_RULESET)
        game = GameState("p", "w", has_permit=True)
        state = start_combat(game, 2, ruleset=SPIRE_RULESET)
        self.assertEqual(advance_round(state, "guard", 1).code, "BLESSING_REQUIRED")

    def test_camp_precedes_draft_and_battle_cannot_skip_it(self):
        _, state = normal_run()
        self.assertEqual(choose_relic(state, "relic_ember", 1).code, "RELIC_NOT_PENDING")
        for _ in range(3):
            advance_round(state, "guard", state.round_no)
        self.assertEqual(combat_preview(state)["phase"], "camp")
        self.assertEqual(advance_round(state, "guard", 4).code, "CAMP_REQUIRED")
        self.assertEqual(choose_relic(state, "relic_ember", 4).code, "RELIC_NOT_PENDING")
        choose_camp(state, "camp_rest", 4)
        before = copy.deepcopy(state)
        self.assertEqual(combat_preview(state)["phase"], "relic")
        self.assertEqual(advance_round(state, "strike", 4).code, "RELIC_REQUIRED")
        self.assertEqual(state, before)

    def test_preview_is_pure_and_all_public_views_hide_next_enemy(self):
        game, state = first_draft()
        before = copy.deepcopy(state)
        preview = combat_preview(state)
        self.assertEqual(len(preview["options"]), 3)
        self.assertIsNone(public_state(game, state)["combat"]["monster_id"])
        facts = scene_facts(state)
        self.assertIsNone(facts["next_monster"])
        self.assertEqual(facts["next_bonus_points"], 0)
        self.assertNotIn("丘丘人", narrate(state).encounter_narration)
        packet = visible_packet(game, state, 1, "venti", "score", "选哪个", "advice")
        self.assertIsNone(packet["monster_id"])
        self.assertNotIn("seed", packet)
        self.assertNotIn("monsters", packet)
        preview["options"][0]["points_awarded"] = 999
        self.assertEqual(state, before)

    def test_draft_is_deterministic_and_excludes_owned(self):
        for seed in range(100):
            self.assertEqual(offer_relics(seed, 4), offer_relics(seed, 4))
            owned = []
            for number in (4, 7, 10):
                offered = offer_relics(seed, number, owned)
                self.assertEqual(len(set(offered)), 3)
                self.assertFalse(set(owned) & set(offered))
                owned.append(offered[0])

    def test_invalid_duplicate_and_stale_picks_do_not_mutate(self):
        _, state = first_draft()
        offered = [row["choice_id"] for row in combat_preview(state)["options"]]
        unoffered = next(key for key in RELICS if key not in offered)
        before = copy.deepcopy(state)
        for choice, number, code in ((offered[0], True, "STALE_ROUND"),
                                    (offered[0], 3, "STALE_ROUND"),
                                    (unoffered, 4, "RELIC_NOT_OFFERED"),
                                    ("relic_unknown", 4, "RELIC_NOT_OFFERED")):
            self.assertEqual(choose_relic(state, choice, number).code, code)
            self.assertEqual(state, before)
        picked = choose_relic(state, offered[0], 4)
        self.assertEqual(picked.code, "RELIC_CHOSEN")
        self.assertEqual((state.score, state.focus, state.round_no, state.events, state.current_monster),
                         (before.score, before.focus, before.round_no, before.events, before.current_monster))
        after = copy.deepcopy(state)
        self.assertEqual(choose_relic(state, offered[1], 4).code, "RELIC_NOT_PENDING")
        self.assertEqual(state, after)

    def test_all_six_relic_conditions(self):
        cases = [
            ("relic_ember", "opening", "strike", 2, 1, True, None, 2, 1),
            ("relic_mirror", "pressure", "guard", 1, 2, False, None, 2, 2),
            ("relic_feather", "opening", "guard", 0, 1, False, None, 0, 2),
            ("relic_lantern", "pressure", "strike", 3, 1, True, None, 3, 1),
            ("relic_echo", "opening", "strike", 2, 1, True, "guard", 2, 1),
            ("relic_dew", "pressure", "guard", 1, 2, False, None, 2, 2),
        ]
        for key, stance, action, before, after, success, previous, bonus, expected_focus in cases:
            with self.subTest(key=key):
                effect = relic_effect([key], stance, action, before, after, success, previous)
                self.assertEqual((effect.bonus, effect.focus_after, effect.triggers), (bonus, expected_focus, (key,)))

    def test_relics_do_not_reward_failed_attacks_or_first_echo(self):
        owned = list(RELICS)
        self.assertEqual(relic_effect(owned, "opening", "strike", 0, 0, False, "guard").bonus, 0)
        self.assertEqual(relic_effect(["relic_echo"], "opening", "guard", 0, 1, False).bonus, 0)
        effect = relic_effect(["relic_feather"], "opening", "guard", 3, 3, False)
        self.assertEqual((effect.focus_after, effect.triggers), (3, ()))

    def test_synergies_and_focus_cap_are_exact(self):
        effect = relic_effect(["relic_ember", "relic_lantern", "relic_echo"],
                              "opening", "strike", 3, 2, True, "guard")
        self.assertEqual(effect.bonus, 7)
        effect = relic_effect(["relic_mirror", "relic_dew", "relic_feather"],
                              "pressure", "guard", 1, 2, False, "strike")
        self.assertEqual((effect.bonus, effect.focus_after), (4, 3))

    def test_relics_never_change_rare_or_fatal_effects(self):
        for stance in ("rare", "fatal"):
            for action in ("strike", "guard"):
                self.assertEqual(relic_effect(list(RELICS), stance, action, 1, 1, True, "guard").bonus, 0)
        for monster, expected_code, expected_points in ((Monster("大伟丘", bonus_points=50), "ROUND_RESOLVED", 50),
                                                       (Monster("遗迹重机", fatal=True), "PLAYER_DEAD", 0)):
            _, state = first_draft()
            choose_relic(state, combat_preview(state)["options"][0]["choice_id"], 4)
            state.current_monster = monster
            before = state.score
            result = advance_round(state, "guard", 4)
            self.assertEqual(result.code, expected_code)
            self.assertEqual(state.score-before, expected_points)
            self.assertEqual(result.event.relic_triggers, ())

    def test_preview_matches_settlement_and_records_actual_triggers(self):
        for seed in range(30):
            _, state = first_draft(seed)
            choose_relic(state, combat_preview(state)["options"][0]["choice_id"], 4)
            for action in ("strike", "guard"):
                for focus in range(4):
                    trial = copy.deepcopy(state)
                    trial.focus = focus
                    option = next(row for row in combat_preview(trial)["options"] if row["choice_id"] == action)
                    score = trial.score
                    result = advance_round(trial, action, 4)
                    self.assertEqual(trial.score-score, option["points_awarded"])
                    self.assertEqual(trial.focus, option["focus_after"])
                    self.assertEqual(result.event.relic_bonus_points, option["relic_bonus_points"])
                    self.assertEqual(list(result.event.relic_triggers), option["relic_triggers"])

    def test_three_drafts_complete_and_report_does_not_double_count(self):
        _, state = normal_run()
        while state.status == "active":
            view = combat_preview(state)
            if view["phase"] == "camp":
                choose_camp(state, "camp_forge", state.round_no)
            elif view["phase"] == "relic":
                choose_relic(state, view["options"][0]["choice_id"], state.round_no)
            else:
                option = max(view["options"], key=lambda row: (row["points_awarded"], row["focus_after"]))
                advance_round(state, option["choice_id"], state.round_no)
        view = combat_preview(state)
        self.assertEqual(state.status, "cleared")
        self.assertEqual([event.round_no for event in state.relic_events], [4, 7, 10])
        self.assertEqual(len(set(event.choice_id for event in state.relic_events)), 3)
        report = view["report"]
        self.assertEqual(state.score, report["normal_points"]+report["camp_points"]+report["rare_points"])
        self.assertEqual(report["relic_points"], sum(event.relic_bonus_points for event in state.events))
        self.assertEqual(choose_relic(state, "relic_ember", 10).code, "COMBAT_FINISHED")

    def test_new_and_legacy_saves_roundtrip_without_reward_migration(self):
        game, state = first_draft()
        choose_relic(state, combat_preview(state)["options"][0]["choice_id"], 4)
        advance_round(state, "strike", 4)
        row = GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(state))))
        self.assertEqual(unpack(row), (game, state))
        old = start_combat(game, 2, NORMAL)
        advance_round(old, "strike", 1)
        payload = asdict(old)
        payload.pop("relic_events")
        for event in payload["events"]:
            event.pop("relic_bonus_points")
            event.pop("relic_triggers")
        restored = unpack(GameSave(game=asdict(game), combat=payload))[1]
        self.assertEqual((restored.ruleset, restored.score, restored.relic_events), (CLASSIC_RULESET, 1, []))
        for mode in (TACTICAL_RULESET, BLESSED_RULESET, EXPEDITION_RULESET):
            legacy = start_combat(game, 2, NORMAL, ruleset=mode)
            self.assertEqual(choose_relic(legacy, "relic_ember", 1).code, "RELICS_NOT_SUPPORTED")

    def test_forged_or_skipped_relic_history_is_rejected(self):
        _, state = first_draft()
        offered = {row["choice_id"] for row in combat_preview(state)["options"]}
        bad = next(key for key in RELICS if key not in offered)
        with self.assertRaises(ValueError):
            CombatState(**{**state.__dict__, "relic_events": [RelicEvent(4, bad)]})
        with self.assertRaises(ValueError):
            CombatState(**{**state.__dict__, "round_no": 5})
        with self.assertRaises(ValueError):
            RelicEvent(True, "relic_ember")

    def test_narration_and_advisor_use_authoritative_relic_options(self):
        game, state = first_draft()
        packet = visible_packet(game, state, 7, "varka", "score", "选择遗物", "advice")
        selected = default_selection(packet)
        self.assertIn(selected["action_id"], {row["choice_id"] for row in packet["options"]})
        facts = scene_facts(state)
        def model(received):
            received["relics"].append({"title": "假遗物"})
            return {"result_narration": "", "encounter_narration": "篝火映亮遗物。",
                    "options": [{"choice_id": key, "title": "免死", "description": "999分"}
                                for key in facts["allowed_choices"]]}
        scene = narrate_facts(facts, model)
        self.assertEqual([row.description for row in scene.options], [row["description"] for row in facts["rule_options"]])
        result = choose_relic(state, selected["action_id"], 4)
        scene = narrate(state, result)
        self.assertIn("领取不加分", scene.result_narration)
        self.assertNotIn("规则计分：本轮", scene.result_narration)
        self.assertEqual(len(state.relic_events), 1)

    def test_unchanged_monsters_stances_and_terminal_round_across_seeds(self):
        game = GameState("p", "w", has_permit=True)
        for seed in range(80):
            old = start_combat(game, seed, ruleset=EXPEDITION_RULESET)
            new = start_combat(game, seed, ruleset=SPIRE_RULESET)
            for state in (old, new):
                choose_blessing(state, state.blessing_options[0], 1)
            while new.status == "active":
                if combat_preview(new)["phase"] == "camp":
                    choose_camp(old, "camp_rest", old.round_no)
                    choose_camp(new, "camp_rest", new.round_no)
                    choose_relic(new, combat_preview(new)["options"][0]["choice_id"], new.round_no)
                self.assertEqual(new.current_monster, old.current_monster)
                self.assertEqual(combat_preview(new)["stance"], combat_preview(old)["stance"])
                advance_round(old, "guard", old.round_no)
                advance_round(new, "guard", new.round_no)
            self.assertEqual((new.status, new.round_no), (old.status, old.round_no))

    def test_final_round_fatal_priority_and_rare_reward_with_three_relics(self):
        _, ready = normal_run()
        while ready.round_no < 10 or combat_preview(ready)["phase"] in {"camp", "relic"}:
            preview = combat_preview(ready)
            if preview["phase"] == "camp":
                choose_camp(ready, "camp_forge", ready.round_no)
            elif preview["phase"] == "relic":
                choose_relic(ready, preview["options"][0]["choice_id"], ready.round_no)
            else:
                advance_round(ready, "guard", ready.round_no)
        self.assertEqual(len(ready.relic_events), 3)
        for monster, status, points in ((Monster("深渊咏者", fatal=True), "dead", 0),
                                        (Monster("大伟丘", bonus_points=50), "cleared", 50)):
            state = copy.deepcopy(ready)
            state.current_monster = monster
            before = state.score
            result = advance_round(state, "guard", 10)
            self.assertEqual((state.status, state.score-before), (status, points))
            self.assertEqual((result.event.relic_bonus_points, result.event.relic_triggers), (0, ()))

    def test_guard_guidance_mentions_new_mode_without_granting_rewards(self):
        from echoworld.guard_skills import beginner_question, beginner_hint
        game = GameState("p", "w", has_permit=True)
        self.assertTrue(beginner_question("遗物构筑"))
        before = copy.deepcopy(game)
        self.assertIn("遗物", beginner_hint(game))
        self.assertEqual(game, before)


class RelicApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name)/"relic.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2))
        self.client.__enter__()
        self.base = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.base+"/state", headers=self.headers).json()

    def body(self, action, request_id=None):
        state = self.state()
        payload = {"action": action, "request_id": request_id or f"{action}-{state['version']}",
                   "expected_version": state["version"]}
        if action in {"strike", "guard"} or action.startswith(("boon_", "camp_", "relic_")):
            payload["expected_round"] = state["combat"]["round_no"]
        return payload

    def post(self, payload):
        return self.client.post(self.base+"/action", headers=self.headers, json=payload)

    def draft(self):
        for action in ("accept", "submit", "tower_spire"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        blessing = self.state()["combat"]["options"][0]["choice_id"]
        for action in (blessing, "guard", "guard", "guard", "camp_rest"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        self.assertEqual(self.state()["combat"]["phase"], "relic")

    def test_permit_types_and_authoritative_field_injection(self):
        before = self.state()
        self.assertEqual(self.post(self.body("tower_spire")).json()["code"], "PERMIT_REQUIRED")
        self.assertEqual(self.state(), before)
        self.draft()
        option = self.state()["combat"]["options"][0]["choice_id"]
        body = self.body(option)
        for extra in ({"relic_events": []}, {"relics": ["relic_ember"]}, {"score": 999}, {"seed": 1}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.post({**body, "expected_round": True}).status_code, 422)
        self.assertEqual(self.post({key: value for key, value in body.items() if key != "expected_round"}).status_code, 422)
        self.assertEqual(self.post({**body, "action": "relic_unknown"}).status_code, 422)

    def test_replay_narration_and_new_id_do_not_repeat_pick(self):
        self.draft()
        before = self.state()
        body = self.body(before["combat"]["options"][0]["choice_id"], "pick-one")
        first = self.post(body).json()
        self.assertEqual(first["code"], "RELIC_CHOSEN")
        self.assertEqual(first["state"]["version"], before["version"]+1)
        self.assertEqual(first["state"]["combat"]["score"], before["combat"]["score"])
        self.assertEqual(self.post(body).json(), first)
        for _ in range(2):
            scene = self.client.post(self.base+"/actions/pick-one/narration", headers=self.headers).json()
            self.assertIn("遗物已收下", scene["result_narration"])
            self.assertNotIn("规则计分：本轮", scene["result_narration"])
        self.assertEqual(self.state(), first["state"])
        self.assertEqual(self.post({**body, "action": before["combat"]["options"][1]["choice_id"]}).status_code, 409)
        duplicate = self.post(self.body(body["action"], "new-id")).json()
        self.assertEqual(duplicate["code"], "RELIC_NOT_PENDING")
        self.assertEqual(self.state(), first["state"])

    def test_restart_keeps_pending_offers_and_owned_relic(self):
        self.draft()
        for pick in (False, True):
            if pick:
                self.post(self.body(self.state()["combat"]["options"][0]["choice_id"]))
            before = self.state()
            self.client.__exit__(None, None, None)
            self.client = TestClient(create_app(database_url=self.url, combat_seed=999))
            self.client.__enter__()
            self.assertEqual(self.state(), before)

    def test_concurrent_different_picks_commit_only_one(self):
        self.draft()
        options = self.state()["combat"]["options"]
        bodies = [self.body(row["choice_id"]) for row in options[:2]]
        with TestClient(create_app(database_url=self.url, combat_seed=2)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(self.post, bodies[0]), pool.submit(other.post, self.base+"/action",
                        headers=self.headers, json=bodies[1])]
                responses = [job.result() for job in jobs]
        self.assertEqual(sorted(row.status_code for row in responses), [200, 409])
        self.assertEqual(len(self.state()["combat"]["relics"]), 1)
        self.assertEqual(self.state()["version"], bodies[0]["expected_version"]+1)

    def test_advisor_only_proposes_offered_relic(self):
        self.draft()
        before = self.state()
        response = self.client.post(self.base+"/ai/advice", headers=self.headers,
            json={"request_id": "advice", "expected_version": before["version"], "npc_id": "varka", "goal": "score"})
        self.assertEqual(response.status_code, 200)
        # API wraps the generated selection in content.
        data = response.json()["content"]
        self.assertIn(data["action"]["choice_id"], {row["choice_id"] for row in before["combat"]["options"]})
        self.assertEqual(self.state(), before)

    def test_full_seed2_tower_and_report(self):
        self.draft()
        while self.state()["combat"]["status"] == "active":
            state = self.state()["combat"]
            if state["phase"] == "camp":
                action = "camp_forge"
            elif state["phase"] == "relic":
                action = state["options"][0]["choice_id"]
            else:
                action = max(state["options"], key=lambda row: (row["points_awarded"], row["focus_after"]))["choice_id"]
            self.assertTrue(self.post(self.body(action)).json()["ok"])
        state = self.state()["combat"]
        self.assertEqual((state["status"], len(state["relics"])), ("cleared", 3))
        self.assertEqual(state["score"], state["report"]["normal_points"]+state["report"]["camp_points"]+state["report"]["rare_points"])

    def test_fake_two_step_model_can_compare_relics_but_not_award_them(self):
        from echoworld.test_game_ai import FakeAI
        self.draft()
        self.client.__exit__(None, None, None)
        model = FakeAI("tools")
        self.client = TestClient(create_app(database_url=self.url, combat_seed=2, game_ai_model=model))
        self.client.__enter__()
        before = self.state()
        response = self.client.post(self.base+"/ai/advice", headers=self.headers,
            json={"request_id": "model-relic", "expected_version": before["version"],
                  "npc_id": "venti", "goal": "score", "use_model": True})
        self.assertEqual(response.status_code, 200)
        content = response.json()["content"]
        self.assertEqual(content["source"], "model")
        self.assertIn(content["action"]["choice_id"], {row["choice_id"] for row in before["combat"]["options"]})
        self.assertEqual(model.calls, 2)
        self.assertEqual(self.state(), before)

    def test_production_seed_server_owned_and_chat_tools_stay_readonly(self):
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url, combat_seed=None))
        self.client.__enter__()
        for action in ("accept", "submit"):
            self.post(self.body(action))
        with patch("echoworld.action_store.randbits", return_value=471) as seed:
            self.assertTrue(self.post(self.body("tower_spire")).json()["ok"])
            seed.assert_called_once_with(63)
        game = GameState("p", "w")
        for action in ("tower_spire", *RELICS):
            self.assertFalse(execute_tool(game, action, {})["ok"])

    def test_web_entry_assets_and_no_client_authoritative_fields(self):
        html = self.client.get("/").text
        for fragment in ("tower_spire", "relicInventory", "relic-choice", "relic_triggers", "action.startsWith('relic_')"):
            self.assertIn(fragment, html)
        css = self.client.get("/assets/adventure-v2.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn(".relic-panel[hidden]", css.text)


if __name__ == "__main__":
    unittest.main()
