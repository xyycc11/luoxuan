"""留牌与敌人节奏；旧JSON继续第一版，现有SQL/CAS机制不变。"""
import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from fastapi.testclient import TestClient
from echoworld.api import create_app
from echoworld.combat import start_combat
from echoworld.database import GameSave, unpack
from echoworld.elements import (DECK, CARDS, ElementState, ELEMENTS_RULESET, perform,
                                preview, enemy_intent, prepare_floor)
from echoworld.state import GameState
from echoworld import test_elements as element_tests
from echoworld.verify_elements import run


def ready(seed=2, edition=2):
    game=GameState("p","w",has_permit=True)
    return game,start_combat(game,seed,ruleset=ELEMENTS_RULESET,elements_edition=edition)


class ElementTactics(unittest.TestCase):
    def test_hold_is_free_once_without_drawing_or_advancing(self):
        _,s=ready();e=s.elemental;key=e.hand[0];before=copy.deepcopy(e)
        self.assertEqual(perform(s,"element_hold_"+key,1),"ELEMENT_HELD")
        self.assertEqual((e.hp,e.energy,e.turn,s.round_no,s.score),(before.hp,before.energy,1,1,0))
        self.assertEqual((e.hand,e.draw,e.discard),(before.hand,before.draw,before.discard))
        saved=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_hold_"+e.hand[1],1),"HOLD_ALREADY_USED")
        self.assertEqual(s,saved)

    def test_end_keeps_one_draws_to_four_and_conserves_deck(self):
        _,s=ready();e=s.elemental;key=e.hand[0]
        perform(s,"element_hold_"+key,1);perform(s,"element_end",1)
        self.assertIn(key,e.hand);self.assertEqual(len(e.hand),4)
        self.assertEqual(Counter(e.hand+e.draw+e.discard),Counter(DECK))
        self.assertEqual(e.hand.count(key),1)
        self.assertEqual((e.held,e.hold_used),(None,False))

    def test_playing_held_card_consumes_it_and_does_not_duplicate(self):
        _,s=ready();key=s.elemental.hand[0]
        perform(s,"element_hold_"+key,1);perform(s,"element_"+key,1)
        e=s.elemental
        self.assertIsNone(e.held);self.assertTrue(e.hold_used)
        self.assertNotIn(key,e.hand);self.assertEqual(e.discard.count(key),1)
        self.assertEqual(Counter(e.hand+e.draw+e.discard),Counter(DECK))

    def test_zero_energy_can_hold_but_missing_card_cannot(self):
        _,s=ready();s.elemental.energy=0
        absent=next(k for k in DECK if k not in s.elemental.hand)
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_hold_"+absent,1),"ELEMENT_NOT_IN_HAND")
        self.assertEqual(s,before)
        self.assertEqual(perform(s,"element_hold_"+s.elemental.hand[0],1),"ELEMENT_HELD")

    def test_stale_type_unknown_and_camp_leave_state_unchanged(self):
        _,s=ready();s.round_no=4;prepare_floor(s)
        for action,number,code in (("element_hold_pyro",4,"ELEMENT_CAMP_REQUIRED"),
            ("element_hold_pyro",True,"STALE_ROUND"),("element_hold_fake",4,"UNKNOWN_ELEMENT_ACTION")):
            before=copy.deepcopy(s);self.assertEqual(perform(s,action,number),code);self.assertEqual(s,before)

    def test_old_json_defaults_first_edition_and_old_seed_result_stays(self):
        game,s=ready(edition=1);data=asdict(s)
        for key in ("edition","held","hold_used"):data["elemental"].pop(key)
        restored=unpack(GameSave(game=asdict(game),combat=json.loads(json.dumps(data))))[1]
        self.assertEqual(restored.elemental.edition,1)
        before=copy.deepcopy(restored)
        self.assertEqual(perform(restored,"element_hold_"+restored.elemental.hand[0],1),"HOLD_NOT_SUPPORTED")
        self.assertEqual(restored,before)
        result,_,_=run(2,edition=1)
        self.assertEqual((result["score"],result["hp"],result["actions"]),(270,21,59))

    def test_hold_json_roundtrip_and_no_future_pile_leak(self):
        game,s=ready();perform(s,"element_hold_"+s.elemental.hand[0],1)
        before=copy.deepcopy(s)
        for _ in range(4):
            v=preview(s)
            self.assertEqual(len(v["elemental"]["forecast"]),3)
            self.assertNotIn('"draw"',json.dumps(v));self.assertNotIn('"seed"',json.dumps(v))
        self.assertEqual(s,before)
        restored=unpack(GameSave(game=asdict(game),combat=json.loads(json.dumps(asdict(s)))))[1]
        self.assertEqual(s,restored)

    def test_invalid_hold_markers_rejected(self):
        _,s=ready()
        for extra in ({"held":s.elemental.hand[0],"hold_used":False},{"edition":99},
                      {"held":"fake","hold_used":True},{"hold_used":1}):
            with self.assertRaises(ValueError):ElementState(**{**asdict(s.elemental),**extra})

    def test_monster_identity_changes_shield_aura_and_rhythm(self):
        seen={}
        for seed in range(100):
            _,s=ready(seed)
            if not s.current_monster.bonus_points:seen[s.current_monster.monster_id]=s
        self.assertEqual(set(seen),{"丘丘人","木盾丘丘暴徒","火史莱姆","风丘丘萨满","遗迹守卫"})
        self.assertEqual(seen["木盾丘丘暴徒"].elemental.barrier,6)
        self.assertEqual(seen["火史莱姆"].elemental.aura,"pyro")
        self.assertEqual([enemy_intent(seen["遗迹守卫"],t)[1] for t in (1,2,3)],[2,5,12])
        self.assertEqual([enemy_intent(seen["风丘丘萨满"],t)[1] for t in (1,2,3)],[2,8,4])

    def test_boss_forecast_current_to_next_two_includes_enrage(self):
        _,s=ready();s.round_no=10;prepare_floor(s);s.elemental.camp_due=False
        s.elemental.turn=6
        forecast=preview(s)["elemental"]["forecast"]
        self.assertEqual([x["damage"] for x in forecast],[20,9,16])
        self.assertEqual(s.elemental.turn,6)

    def test_new_floor_clears_hold_and_camp_hides_trait_and_forecast(self):
        _,s=ready();s.elemental.held=s.elemental.hand[0];s.elemental.hold_used=True
        s.round_no=4;prepare_floor(s)
        self.assertEqual((s.elemental.held,s.elemental.hold_used),(None,False))
        v=preview(s)["elemental"]
        self.assertIsNone(v["enemy_trait"]);self.assertEqual(v["forecast"],[])

    def test_new_full_runs_restore_and_scores_remain_log_sum(self):
        for seed in (0,2,7,13):
            result,s,_=run(seed,edition=2)
            self.assertEqual(s.status,"cleared",result)
            self.assertGreater(result["holds"],0)
            self.assertEqual(s.score,sum(x["points"] for x in s.elemental.log))
            ElementState(**asdict(s.elemental))


class TacticsApi(unittest.TestCase):
    setUp=element_tests.ElementApi.setUp
    tearDown=element_tests.ElementApi.tearDown
    state=element_tests.ElementApi.state
    post=element_tests.ElementApi.post
    body=element_tests.ElementApi.body
    start=element_tests.ElementApi.start

    def hold_action(self):
        return next(x["choice_id"] for x in self.state()["combat"]["options"] if x.get("kind")=="hold")

    def test_hold_receipt_cas_restart_and_old_save_not_upgraded(self):
        self.start();self.assertEqual(self.state()["combat"]["elemental"]["edition"],3)
        body=self.body(self.hold_action(),"hold-one");response=self.post(body).json()
        self.assertEqual(response["code"],"ELEMENT_HELD")
        self.assertEqual(self.post(body).json(),response)
        self.assertEqual(self.post({**body,"request_id":"hold-stale"}).status_code,409)
        self.client.__exit__(None,None,None)
        self.client=TestClient(create_app(database_url=self.url));self.client.__enter__()
        self.assertEqual(self.state(),response["state"])

    def test_hold_disallows_field_injection_and_missing_round(self):
        self.start();body=self.body(self.hold_action());before=self.state()
        for extra in ({"held":"pyro"},{"hold_used":False},{"edition":1}):
            self.assertEqual(self.post({**body,**extra}).status_code,422)
        self.assertEqual(self.post({k:v for k,v in body.items() if k!="expected_round"}).status_code,422)
        self.assertEqual(self.state(),before)

    def test_independent_sql_instances_cannot_hold_two_cards(self):
        self.start();opts=[x for x in self.state()["combat"]["options"] if x.get("kind")=="hold"][:2]
        bodies=[self.body(o["choice_id"]) for o in opts]
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.post,bodies[0]),pool.submit(other.post,self.base+"/action",headers=self.headers,json=bodies[1])]
                self.assertEqual(sorted(j.result().status_code for j in jobs),[200,409])
        self.assertTrue(self.state()["combat"]["elemental"]["hold_used"])


if __name__=="__main__":unittest.main()
