"""隐藏关规则、边界、存档与HTTP回执；不用真实玩家存档或外部模型。"""
import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from functools import lru_cache
from fastapi.testclient import TestClient
from echoworld.api import create_app
from echoworld.database import GameSave, unpack, pack
from echoworld.elements import (DECK, SECRET_NAME, perform, preview, card_effect,
                               _finish_floor, energy_cap)
from echoworld.game_actions import apply_action
from echoworld.state import GameState
from echoworld.tools import execute_tool
from echoworld.verify_elements import run, choose
from echoworld import test_elements


@lru_cache(maxsize=1)
def _finished():
    _, state, _ = run(2, "combo_flow", edition=4)
    assert state.status == "cleared" and state.elemental.secret == "offered"
    return state


def offered():
    return copy.deepcopy(_finished())


def hidden():
    state = offered()
    assert perform(state, "element_secret_enter", 10) == "SECRET_ENTERED"
    return state


def roundtrip(state):
    game = GameState(state.player_id, state.world_id, has_permit=True)
    return unpack(GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(state)))))[1]


class SecretRules(unittest.TestCase):
    def test_threshold_strictly_greater_than_30(self):
        for hp in (1, 30, 31, 44):
            s = offered(); s.events.pop(); s.status = "active"
            s.elemental.secret = "none"; s.elemental.base_clear_hp = None
            s.elemental.hp = hp
            self.assertEqual(_finish_floor(s), "TOWER_CLEARED")
            self.assertEqual(s.elemental.secret, "offered" if hp > 30 else "none")
            self.assertEqual(roundtrip(s), s)

    def test_old_editions_do_not_get_new_invitation(self):
        s = offered(); s.events.pop(); s.status = "active"
        e = s.elemental; e.edition = 3; e.secret = "none"; e.base_clear_hp = None
        self.assertEqual(_finish_floor(s), "TOWER_CLEARED")
        self.assertEqual(e.secret, "none")
        self.assertEqual(roundtrip(s), s)

    def test_offer_and_decline_are_explicit_once(self):
        s = offered(); before = copy.deepcopy(s)
        self.assertEqual(preview(s)["phase"], "secret_offer")
        self.assertIn("温迪", preview(s)["hint"])
        self.assertEqual(s, before)
        self.assertEqual(perform(s,"element_secret_decline",10),"SECRET_DECLINED")
        self.assertEqual((s.status,len(s.events)),("cleared",10))
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_secret_enter",10),"SECRET_NOT_OFFERED")
        self.assertEqual(s,before)
        self.assertEqual(roundtrip(s),s)

    def test_enter_buffs_and_preserves_base_game(self):
        before=offered(); s=hidden(); e=s.elemental
        self.assertEqual((s.round_no,s.status,s.current_monster.monster_id),(11,"active",SECRET_NAME))
        self.assertEqual((e.enemy_hp,e.barrier,e.energy,len(e.hand)),(210,32,5,6))
        self.assertEqual((e.hp,e.power,e.doctrine,s.score),(before.elemental.hp,before.elemental.power,before.elemental.doctrine,before.score))
        self.assertIn("意气风发啊旅行者",e.log[-1]["text"])
        self.assertEqual(Counter(e.hand+e.draw+e.discard),Counter(DECK))
        self.assertEqual(roundtrip(s),s)

    def test_early_entry_wrong_round_and_injection_do_not_mutate(self):
        game=GameState("demo","demo",has_permit=True)
        _,s=apply_action(game,None,"tower_elements")
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_secret_enter",1),"SECRET_NOT_OFFERED")
        self.assertEqual(s,before)
        s=offered(); before=copy.deepcopy(s)
        for value in (None,True,9,11):
            self.assertEqual(perform(s,"element_secret_enter",value),"STALE_ROUND")
            self.assertEqual(s,before)
        self.assertFalse(execute_tool(game,"element_secret_enter",{})["ok"])

    def test_next_turn_six_cards_five_energy_and_no_duplicate(self):
        s=hidden(); s.elemental.energy=0
        self.assertEqual(perform(s,"element_end",11),"ELEMENT_TURN_ENDED")
        self.assertEqual((s.elemental.energy,len(s.elemental.hand),s.elemental.turn),(5,6,2))
        self.assertEqual(roundtrip(s),s)

    def test_refund_cap_and_preview_share_buff(self):
        s=hidden();e=s.elemental;e.aura="hydro";e.energy=5
        e.hand=list(DECK[:6]);e.draw=[DECK[6]];e.discard=[]
        # 风不在前六张之外；所有七牌守恒，牌面与实打数值同源。
        before=copy.deepcopy(s)
        option=next(x for x in preview(s)["options"] if x["choice_id"]=="element_anemo")
        after,effect=card_effect(e,"anemo")
        self.assertEqual(after.energy,5)
        self.assertEqual(energy_cap(e),5)
        self.assertEqual(s,before)
        score=s.score
        self.assertEqual(perform(s,"element_anemo",11),"ELEMENT_PLAYED")
        self.assertEqual(s.score-score,option["damage"])
        self.assertEqual(effect["damage"],option["damage"])

    def test_fifth_turn_skateboard_ignores_shield_and_weaken(self):
        s=hidden();e=s.elemental;e.turn=5;e.hp=44;e.block=30;e.weaken=6
        self.assertIn("滑板",preview(s)["elemental"]["intent"])
        self.assertEqual(perform(s,"element_end",11),"PLAYER_DEAD")
        self.assertEqual((s.status,e.secret,e.hp),("dead","lost",0))
        self.assertIn("七曜征服者",preview(s)["report"]["badges"])
        self.assertEqual(roundtrip(s),s)

    def test_dot_kill_on_fifth_turn_precedes_skateboard(self):
        s=hidden();e=s.elemental;e.turn=5;e.enemy_hp=3;e.barrier=0;e.dot=3
        self.assertEqual(perform(s,"element_end",11),"SECRET_CLEARED")
        self.assertEqual((s.status,s.elemental.secret,len(s.events)),("cleared","won",11))
        self.assertGreater(e.hp,0)
        self.assertEqual(roundtrip(s),s)
        before=copy.deepcopy(s)
        self.assertEqual(perform(s,"element_end",11),"COMBAT_FINISHED")
        self.assertEqual(s,before)

    def test_win_is_possible_with_public_hand_policy(self):
        s=hidden()
        for _ in range(60):
            if s.status!="active":break
            perform(s,choose(s,"combo_flow"),11)
            self.assertEqual(roundtrip(s),s)
        self.assertEqual((s.status,s.elemental.secret),("cleared","won"))

    def test_invalid_saved_hidden_lifecycle_rejected(self):
        for field,value in (("secret","bad"),("base_clear_hp",30),("energy",6),("turn",6)):
            s=hidden();setattr(s.elemental,field,value)
            with self.assertRaises(ValueError):roundtrip(s)
        s=hidden();s.round_no=10
        with self.assertRaises(ValueError):roundtrip(s)


class SecretApi(unittest.TestCase):
    setUp=test_elements.ElementApi.setUp
    tearDown=test_elements.ElementApi.tearDown
    state=test_elements.ElementApi.state
    post=test_elements.ElementApi.post
    body=test_elements.ElementApi.body

    def seed_offer(self):
        # 仅测试夹具写入临时库；HTTP参数没有注入状态的接口。
        self.state()
        store=self.client.app.state.store
        with store.sessions.begin() as db:
            row=db.get(GameSave,"world-alice")
            game,_=unpack(row);game.has_permit=True
            s=offered();s.player_id=game.player_id;s.world_id=game.world_id
            pack(row,game,s)

    def test_enter_replay_restart_scoped_and_stale(self):
        self.seed_offer();body=self.body("element_secret_enter","hidden-once")
        response=self.post(body);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["code"],"SECRET_ENTERED")
        self.assertEqual(self.post(body).json(),response.json())
        before=self.state()
        for extra in ({"energy":99},{"secret":"won"},{"hp":44},{"base_clear_hp":44}):
            self.assertEqual(self.post({**self.body("element_end"),**extra}).status_code,422)
        self.assertEqual(self.client.post(self.base+"/action",headers={"Authorization":"Bearer demo-bob"},json=body).status_code,403)
        self.assertEqual(self.state(),before)
        self.client.__exit__(None,None,None)
        self.client=TestClient(create_app(database_url=self.url));self.client.__enter__()
        self.assertEqual(self.state()["combat"],before["combat"])
        stale={**self.body("element_end"),"expected_round":10}
        self.assertEqual(self.post(stale).json()["code"],"STALE_ROUND")

    def test_two_processes_cannot_accept_and_decline(self):
        self.seed_offer()
        bodies=[self.body("element_secret_enter"),self.body("element_secret_decline")]
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.post,bodies[0]),pool.submit(other.post,self.base+"/action",headers=self.headers,json=bodies[1])]
                self.assertEqual(sorted(j.result().status_code for j in jobs),[200,409])

    def test_portrait_and_pure_audio_contract(self):
        response=self.client.get("/assets/monsters/shafu.jpg")
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        html=self.client.get("/").text
        for key in ("secretOffer","secretMusic","element_secret_enter","element_secret_decline","<audio"):
            self.assertIn(key,html)
        self.assertNotIn("<iframe",html)

    def test_user_mp3_is_served_as_audio_with_byte_ranges(self):
        response=self.client.get("/assets/audio/yuai-adai-dj.mp3",headers={"Range":"bytes=0-4095"})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.headers["content-type"].split(";")[0],"audio/mpeg")
        self.assertEqual(len(response.content),4096)
        self.assertTrue(response.headers["content-range"].startswith("bytes 0-4095/"))
        self.assertTrue(response.content.startswith(b"ID3") or response.content[0]==0xff)
        html=self.client.get("/").text
        self.assertIn("/assets/audio/yuai-adai-dj.mp3",html)
        self.assertIn('id="secretAudio" loop preload="none"',html)
        self.assertNotIn('id="secretAudio" controls',html)


if __name__=="__main__":unittest.main()
