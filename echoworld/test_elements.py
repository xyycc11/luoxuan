"""七元素规则、原子动作、SQL重启/CAS和顾问边界。"""
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
from echoworld.combat import start_combat, advance_round, combat_preview
from echoworld.database import GameSave, unpack
from echoworld.elements import (ELEMENTS_RULESET, CARDS, DECK, ACTIONS, ElementState, REACTIONS,
                                perform, preview, card_effect, prepare_floor, enemy_spec, enemy_intent)
from echoworld.game_actions import apply_action, public_state
from echoworld.game_ai import visible_packet, default_selection
from echoworld.narration import narrate
from echoworld.state import GameState
from echoworld.tools import execute_tool
from echoworld.verify_elements import run, choose


def ready(seed=2):
    g = GameState("p", "w", has_permit=True)
    return g, start_combat(g, seed, ruleset=ELEMENTS_RULESET)


class ElementRules(unittest.TestCase):
    def test_permit_and_legacy_fatal_unchanged(self):
        with self.assertRaises(PermissionError):
            start_combat(GameState("p","w"),2,ruleset=ELEMENTS_RULESET)
        from echoworld.combat import Monster
        s = start_combat(GameState("p","w",has_permit=True),2,(Monster("遗迹重机",fatal=True),))
        self.assertEqual(advance_round(s,"guard",1).code,"PLAYER_DEAD")
        self.assertIsNone(s.elemental)

    def test_exactly_seven_unique_cards_and_pure_views(self):
        g,s = ready()
        before = copy.deepcopy(s)
        for _ in range(4):
            public = json.dumps(public_state(g,s),ensure_ascii=False)
            for private in ('"seed"','"draw"','"discard"','"monsters"'):
                self.assertNotIn(private,public)
        self.assertEqual(s,before)
        self.assertEqual(Counter(s.elemental.hand+s.elemental.draw+s.elemental.discard),Counter(DECK))
        self.assertEqual(len(CARDS),7)

    def test_all_pair_reactions_and_support(self):
        for pair,(name,bonus) in REACTIONS.items():
            a,b=tuple(pair)
            for previous,key in ((a,b),(b,a)):
                _,s=ready();s.elemental.aura=previous
                before=copy.deepcopy(s.elemental)
                after,effect=card_effect(s.elemental,key)
                self.assertEqual(effect["reaction"],name)
                self.assertEqual(effect["bonus_damage"],bonus)
                self.assertIsNone(after.aura)
                self.assertEqual(s.elemental,before)
        for key,name in (("anemo","扩散"),("geo","结晶")):
            _,s=ready();s.elemental.aura="hydro"
            after,effect=card_effect(s.elemental,key)
            self.assertEqual(effect["reaction"],name)
            self.assertEqual(after.aura,"hydro")
        self.assertEqual(card_effect(s.elemental,"geo")[0].block,9)
        self.assertEqual(card_effect(s.elemental,"anemo")[0].energy,3)

    def test_no_fabricated_dendro_cryo_anemo_geo_reactions(self):
        _,s=ready();s.elemental.aura="dendro"
        for key in ("anemo","geo","cryo"):
            self.assertEqual(card_effect(s.elemental,key)[1]["reaction"],"")

    def test_core_combo_uses_one_core_and_cannot_reuse(self):
        _,s=ready();e=s.elemental;e.aura="hydro"
        e,result=card_effect(e,"dendro")
        self.assertEqual((result["reaction"],e.cores),("绽放",1))
        e.energy=3;e.enemy_hp=76;e.enemy_max_hp=76
        e,result=card_effect(e,"electro")
        self.assertEqual((result["reaction"],result["damage"],e.cores),("超绽放",13,0))
        e.energy=3
        self.assertNotIn("绽放",card_effect(e,"electro")[1]["reaction"])

    def test_failed_actions_and_old_round_do_not_mutate(self):
        _,s=ready()
        absent=next(k for k in CARDS if k not in s.elemental.hand)
        for action,number,code in (("element_"+absent,1,"ELEMENT_NOT_IN_HAND"),
              ("element_cheat",1,"UNKNOWN_ELEMENT_ACTION"),("element_end",True,"STALE_ROUND"),
              ("element_end",2,"STALE_ROUND"),("element_rest",1,"ELEMENT_CAMP_NOT_PENDING")):
            before=copy.deepcopy(s);self.assertEqual(perform(s,action,number),code);self.assertEqual(s,before)
        s.elemental.energy=0
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_"+s.elemental.hand[0],1),"ELEMENT_UNAFFORDABLE")
        self.assertEqual(s,before)
        self.assertEqual(advance_round(s,"guard",1).code,"ELEMENT_CARD_REQUIRED")

    def test_previews_match_damage_and_cost_and_only_used_card_discarded(self):
        for seed in range(15):
            _,s=ready(seed)
            for option in preview(s)["options"]:
                if option["choice_id"]=="element_end":continue
                t=copy.deepcopy(s);before=t.elemental
                perform(t,option["choice_id"],1)
                self.assertEqual(t.elemental.enemy_hp,before.enemy_hp-option["damage"])
                self.assertEqual(t.elemental.energy,option["energy_after"])
                self.assertEqual(len(t.elemental.hand),3)
                self.assertEqual(t.score,option["damage"])

    def test_turn_cleanup_aura_preserved_freeze_and_shield_reduce_damage(self):
        _,s=ready();e=s.elemental;e.aura="hydro";e.block=2;e.weaken=4
        hp=e.hp
        self.assertEqual(perform(s,"element_end",1),"ELEMENT_TURN_ENDED")
        self.assertEqual((e.hp,e.block,e.weaken,e.energy,e.aura),(hp,0,0,3,"hydro"))
        self.assertEqual(len(e.hand),4)

    def test_dot_kill_prevents_counterattack(self):
        _,s=ready();e=s.elemental;e.hp=1;e.enemy_hp=2;e.dot=3
        self.assertEqual(perform(s,"element_end",1),"ROUND_RESOLVED")
        self.assertEqual(s.elemental.hp,1)
        self.assertEqual(s.score,2)

    def test_enemy_is_only_boss_on_floor_ten_and_never_fatal(self):
        for seed in range(100):
            _,s=ready(seed)
            for floor in range(1,11):
                s.round_no=floor;prepare_floor(s)
                self.assertFalse(s.current_monster.fatal)
                self.assertEqual("七曜" in s.current_monster.monster_id,floor==10)
                if floor==10:self.assertEqual((s.elemental.enemy_hp,s.elemental.barrier),(90,20))

    def test_barrier_reactions_break_faster_and_weaken_once(self):
        _,s=ready();e=s.elemental;e.enemy_hp=e.enemy_max_hp=76;e.barrier=16;e.aura="cryo"
        after,result=card_effect(e,"pyro")
        self.assertEqual((after.barrier,after.enemy_hp,result["barrier_damage"]),(0,76,16))
        self.assertEqual(after.weaken,4)
        after.energy=3
        self.assertEqual(card_effect(after,"geo")[0].weaken,4)

    def test_boss_never_instant_kills_and_bad_play_loses_normally(self):
        _,s=ready();s.round_no=10;prepare_floor(s);s.elemental.camp_due=False
        hp=s.elemental.hp
        self.assertEqual(perform(s,"element_end",10),"ELEMENT_TURN_ENDED")
        self.assertEqual(hp-s.elemental.hp,7) # 首击可承受，不是直接致死。
        while s.status=="active":perform(s,"element_end",10)
        self.assertEqual(s.status,"dead")
        self.assertGreater(len(s.elemental.log),1)

    def test_turn_limit_stops_stall_even_with_hp(self):
        _,s=ready();s.elemental.turn=8;s.elemental.block=30
        self.assertEqual(perform(s,"element_end",1),"PLAYER_DEAD")
        self.assertGreater(s.elemental.hp,0)
        self.assertIn("撤离",s.elemental.log[-1]["text"])

    def test_camp_choice_is_once_and_does_not_advance(self):
        _,s=ready();s.round_no=4;prepare_floor(s);s.elemental.hp=20
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_end",4),"ELEMENT_CAMP_REQUIRED");self.assertEqual(s,before)
        self.assertEqual(perform(s,"element_rest",4),"ELEMENT_CAMP_CHOSEN")
        self.assertEqual((s.elemental.hp,s.round_no,s.score),(32,4,0))
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_attune",4),"ELEMENT_CAMP_NOT_PENDING");self.assertEqual(s,before)

    def test_rare_reward_once_and_fixed_boss_replaces_tenth_rare(self):
        seed=next(n for n in range(1000) if enemy_spec(n,1)[3])
        _,s=ready(seed);s.elemental.enemy_hp=1
        action="element_"+s.elemental.hand[0]
        self.assertEqual(perform(s,action,1),"ROUND_RESOLVED")
        self.assertEqual(s.score,51)
        self.assertEqual(sum(row["action"]=="rare_reward" for row in s.elemental.log),1)
        before=copy.deepcopy(s);self.assertEqual(perform(s,action,1),"STALE_ROUND");self.assertEqual(s,before)
        self.assertFalse(enemy_spec(seed,10)[3])

    def test_json_restore_conservation_and_legacy_missing_field(self):
        g,s=ready()
        perform(s,choose(s),1)
        restored=unpack(GameSave(game=asdict(g),combat=json.loads(json.dumps(asdict(s)))))[1]
        self.assertEqual(restored,s)
        for pile in ("hand","draw","discard"):
            data=asdict(s.elemental);data[pile].append("pyro")
            with self.assertRaises(ValueError):ElementState(**data)
        old=start_combat(g,2);data=asdict(old);data.pop("elemental")
        self.assertIsNone(unpack(GameSave(game=asdict(g),combat=data))[1].elemental)

    def test_corrupt_score_power_history_and_log_are_rejected(self):
        g,s=ready()
        data=asdict(s);data["score"]=999
        with self.assertRaises(ValueError):unpack(GameSave(game=asdict(g),combat=data))
        data=asdict(s.elemental);data["power"]=3
        with self.assertRaises(ValueError):ElementState(**data)
        data=asdict(s.elemental);data["log"]=[{"text":"fake"}]
        with self.assertRaises(ValueError):ElementState(**data)

    def test_camp_preview_does_not_reveal_next_enemy_or_hand(self):
        _,s=ready();s.round_no=4;prepare_floor(s)
        v=preview(s)
        self.assertIsNone(v["elemental"]["enemy_hp"])
        self.assertIsNone(v["elemental"]["barrier"])
        self.assertEqual(v["elemental"]["intent"],"尚未揭晓")
        self.assertEqual({o["choice_id"] for o in v["options"]},{"element_rest","element_attune"})

    def test_full_combo_runs_finish_and_are_deterministic(self):
        for seed in (0,1,2,3,4,7,19):
            result,s,actions=run(seed)
            self.assertEqual(s.status,"cleared",result)
            self.assertGreater(preview(s)["report"]["reactions"],5)
            self.assertEqual(s.score,sum(row.get("points",0) for row in s.elemental.log))
            self.assertEqual(len(s.events),10)
            ElementState(**asdict(s.elemental))
        self.assertEqual(run(2)[0],run(2)[0])

    def test_advisor_and_narration_do_not_mutate_or_claim_instant_death(self):
        g,s=ready();before=copy.deepcopy(s)
        packet=visible_packet(g,s,1,"venti","score","","advice")
        selection=default_selection(packet)
        self.assertIn(selection["action_id"],{o["choice_id"] for o in preview(s)["options"] if o["enabled"]})
        self.assertNotIn('"seed"',json.dumps(packet))
        model=lambda _: (_ for _ in ()).throw(AssertionError("element narration must stay local"))
        scene=narrate(s,model=model)
        self.assertEqual(scene.source,"template")
        self.assertEqual(s,before)
        for action in ACTIONS:self.assertFalse(execute_tool(g,action,{})["ok"])


class ElementApi(unittest.TestCase):
    setUp=test_routes.RouteApi.setUp
    tearDown=test_routes.RouteApi.tearDown
    state=test_routes.RouteApi.state
    post=test_routes.RouteApi.post

    def body(self,action,request_id=None):
        data=test_routes.RouteApi.body(self,action,request_id)
        if action.startswith("element_"):data["expected_round"]=self.state()["combat"]["round_no"]
        return data

    def start(self):
        for action in ("accept","submit","tower_elements"):
            self.assertTrue(self.post(self.body(action)).json()["ok"])

    def test_authority_permission_and_ownership(self):
        self.assertEqual(self.post(self.body("tower_elements")).json()["code"],"PERMIT_REQUIRED")
        self.start();before=self.state();body=self.body("element_end")
        for extra in ({"hp":999},{"aura":"pyro"},{"hand":[]},{"elemental":{}},{"seed":1},{"energy":99}):
            self.assertEqual(self.post({**body,**extra}).status_code,422)
        self.assertEqual(self.post({**body,"expected_round":True}).status_code,422)
        self.assertEqual(self.post({k:v for k,v in body.items() if k!="expected_round"}).status_code,422)
        self.assertEqual(self.client.post(self.base+"/action",headers={"Authorization":"Bearer demo-bob"},json=body).status_code,403)
        self.assertEqual(self.state(),before)

    def test_same_floor_cas_replay_and_narration_no_double_damage(self):
        self.start();view=self.state()["combat"]
        key=next(o["choice_id"] for o in view["options"] if o["choice_id"]!="element_end")
        body=self.body(key,"element-once");first=self.post(body).json()
        self.assertTrue(first["ok"])
        self.assertEqual(self.post(body).json(),first)
        self.assertEqual(self.post({**body,"request_id":"stale"}).status_code,409)
        for _ in range(2):
            self.assertEqual(self.client.post(self.base+"/actions/element-once/narration",headers=self.headers).status_code,200)
        self.assertEqual(self.state(),first["state"])

    def test_restart_keeps_hp_hand_aura_and_current_turn(self):
        self.start();key=next(o["choice_id"] for o in self.state()["combat"]["options"] if o["choice_id"]!="element_end")
        self.post(self.body(key));before=self.state()
        self.client.__exit__(None,None,None)
        self.client=TestClient(create_app(database_url=self.url,combat_seed=99));self.client.__enter__()
        self.assertEqual(self.state(),before)

    def test_two_independent_sql_stores_only_commit_one_same_turn_action(self):
        self.start();options=self.state()["combat"]["options"][:2]
        bodies=[self.body(o["choice_id"]) for o in options]
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.post,bodies[0]),pool.submit(other.post,self.base+"/action",headers=self.headers,json=bodies[1])]
                self.assertEqual(sorted(j.result().status_code for j in jobs),[200,409])
        self.assertEqual(self.state()["version"],bodies[0]["expected_version"]+1)

    def test_new_mode_uses_server_seed_and_ui_contract(self):
        self.client.__exit__(None,None,None)
        self.client=TestClient(create_app(database_url=self.url,combat_seed=None));self.client.__enter__()
        for action in ("accept","submit"):self.post(self.body(action))
        with patch("echoworld.action_store.randbits",return_value=2) as random:
            self.assertTrue(self.post(self.body("tower_elements")).json()["ok"]);random.assert_called_once_with(63)
        html=self.client.get("/").text
        for term in ("tower_elements","elementPanel","displayElements","七元素"):
            self.assertIn(term,html)

    def test_fake_model_advisor_is_readonly_and_only_proposes_current_hand(self):
        from echoworld.test_game_ai import FakeAI
        self.start()
        self.client.__exit__(None,None,None)
        model=FakeAI("tools")
        self.client=TestClient(create_app(database_url=self.url,game_ai_model=model));self.client.__enter__()
        before=self.state()
        response=self.client.post(self.base+"/ai/advice",headers=self.headers,json={
            "request_id":"element-model","expected_version":before["version"],"npc_id":"venti","goal":"score","use_model":True})
        self.assertEqual(response.status_code,200)
        content=response.json()["content"]
        self.assertEqual((content["source"],model.calls),("model",2))
        self.assertIn(content["action"]["choice_id"],{row["choice_id"] for row in before["combat"]["options"] if row["enabled"]})
        self.assertEqual(self.state(),before)

    def test_full_http_tower_and_final_boss_do_not_double_award(self):
        self.start()
        _,_,actions=run(2,edition=2)
        for action in actions:
            response=self.post(self.body(action))
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()["ok"],response.json()["code"])
        before=self.state();combat=before["combat"]
        self.assertEqual((combat["status"],combat["score"],combat["elemental"]["hp"]),("cleared",270,22))
        self.assertEqual(combat["report"]["completed_rounds"],10)
        self.assertEqual(self.post(self.body("element_pyro","after-win")).json()["code"],"COMBAT_FINISHED")
        self.assertEqual(self.state(),before)


if __name__=="__main__":unittest.main()
