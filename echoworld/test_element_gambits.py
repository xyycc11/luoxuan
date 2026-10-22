"""第三版：手牌招架与唯一流派，沿用存档/CAS/只读工具边界。"""
import copy
import json
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from fastapi.testclient import TestClient
from echoworld.api import create_app
from echoworld.database import GameSave, unpack
from echoworld.elements import (DECK, DOCTRINES, ElementState, card_effect, perform,
                                prepare_floor, preview)
from echoworld.test_element_tactics import ready
from echoworld import test_elements as element_tests
from echoworld.verify_elements import run, choose


def learned(name):
    game, state = ready(edition=3)
    # 真正走完前三层，保留合法TurnEvent，SQL恢复不能靠跳层夹具蒙混通过。
    for _ in range(120):
        if state.round_no == 4 and state.elemental.camp_due:
            break
        perform(state, choose(state), state.round_no)
    assert state.round_no == 4 and state.elemental.camp_due
    assert perform(state, 'element_doctrine_' + name, 4) == 'ELEMENT_CAMP_CHOSEN'
    return game, state


class GambitRules(unittest.TestCase):
    def test_parry_discards_exactly_one_at_zero_energy_without_attacking(self):
        _, s = ready(edition=3)
        e = s.elemental
        e.energy = 0
        before = copy.deepcopy(e)
        key = e.hand[0]
        self.assertEqual(perform(s, 'element_parry_' + key, 1), 'ELEMENT_PARRIED')
        self.assertEqual((e.hp, e.energy, e.aura, e.enemy_hp, e.turn, s.score),
                         (before.hp, 0, before.aura, before.enemy_hp, 1, 0))
        self.assertEqual((e.block, e.hold_used, e.held), (4, True, None))
        self.assertEqual(e.hand, before.hand[1:])
        self.assertEqual(e.draw, before.draw)
        self.assertEqual(e.discard, before.discard + [key])
        self.assertEqual(Counter(e.hand + e.draw + e.discard), Counter(DECK))

    def test_hold_and_parry_share_one_opportunity_in_both_orders(self):
        for first in ('hold', 'parry'):
            _, s = ready(edition=3)
            perform(s, 'element_' + first + '_' + s.elemental.hand[0], 1)
            before = copy.deepcopy(s)
            key = s.elemental.hand[0]
            self.assertEqual(perform(s, 'element_parry_' + key, 1), 'TACTIC_ALREADY_USED')
            self.assertEqual(perform(s, 'element_hold_' + key, 1), 'HOLD_ALREADY_USED')
            self.assertEqual(s, before)

    def test_old_editions_reject_new_actions_without_mutation(self):
        for edition in (1, 2):
            _, s = ready(edition=edition)
            before = copy.deepcopy(s)
            self.assertEqual(perform(s, 'element_parry_' + s.elemental.hand[0], 1), 'PARRY_NOT_SUPPORTED')
            self.assertEqual(perform(s, 'element_doctrine_flow', 1), 'DOCTRINE_NOT_SUPPORTED')
            self.assertEqual(s, before)

    def test_missing_card_stale_unknown_camp_and_finished_are_readonly(self):
        _, s = ready(edition=3)
        absent = next(k for k in DECK if k not in s.elemental.hand)
        for action, floor, code in (
            ('element_parry_' + absent, 1, 'ELEMENT_NOT_IN_HAND'),
            ('element_parry_' + s.elemental.hand[0], True, 'STALE_ROUND'),
            ('element_parry_fake', 1, 'UNKNOWN_ELEMENT_ACTION'),
            ('element_doctrine_flow', 1, 'ELEMENT_CAMP_NOT_PENDING'),
        ):
            before = copy.deepcopy(s)
            self.assertEqual(perform(s, action, floor), code)
            self.assertEqual(s, before)
        s.round_no = 4
        prepare_floor(s)
        before = copy.deepcopy(s)
        self.assertEqual(perform(s, 'element_parry_' + s.elemental.hand[0], 4), 'ELEMENT_CAMP_REQUIRED')
        self.assertEqual(s, before)
        s.status = 'dead'
        before = copy.deepcopy(s)
        self.assertEqual(perform(s, 'element_parry_' + s.elemental.hand[0], 4), 'COMBAT_FINISHED')
        self.assertEqual(s, before)

    def test_parry_caps_block_and_full_block_is_rejected(self):
        _, s = ready(edition=3)
        s.elemental.block = 30
        key = s.elemental.hand[0]
        option = next(x for x in preview(s)['options'] if x['choice_id'] == 'element_parry_' + key)
        self.assertFalse(option['enabled'])
        before = copy.deepcopy(s)
        self.assertEqual(perform(s, option['choice_id'], 1), 'BLOCK_ALREADY_FULL')
        self.assertEqual(s, before)
        s.elemental.block = 29
        perform(s, option['choice_id'], 1)
        self.assertEqual(s.elemental.block, 30)
        self.assertEqual(s.elemental.log[-1]['block_added'], 1)

    def test_parry_resets_next_turn_but_does_not_duplicate_discarded_card(self):
        game, s = ready(edition=3)
        key = s.elemental.hand[0]
        perform(s, 'element_parry_' + key, 1)
        restored = unpack(GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(s)))))[1]
        self.assertEqual(restored, s)
        self.assertTrue(restored.elemental.hold_used)
        perform(restored, 'element_end', 1)
        e = restored.elemental
        self.assertFalse(e.hold_used)
        self.assertEqual(e.block, 0)
        self.assertEqual(len(e.hand), 4)
        self.assertEqual(Counter(e.hand + e.draw + e.discard), Counter(DECK))

    def test_camp_doctrine_costs_other_rewards_and_cannot_be_rechosen(self):
        for name in DOCTRINES:
            _, s = learned(name)
            e = s.elemental
            self.assertEqual((e.doctrine, e.hp, e.power, e.camp_due), (name, 44, 0, False))
            self.assertEqual(e.camps, [{'floor': 4, 'action': 'element_doctrine_' + name}])
            self.assertEqual(s.elemental.log[-1]['points'], 0)
            self.assertEqual(perform(s, 'element_rest', 4), 'ELEMENT_CAMP_NOT_PENDING')
            s.round_no = 7
            prepare_floor(s)
            before = copy.deepcopy(s)
            self.assertEqual(perform(s, 'element_doctrine_flow', 7), 'DOCTRINE_ALREADY_LEARNED')
            self.assertEqual(s, before)
            self.assertEqual({x['choice_id'] for x in preview(s)['options']}, {'element_rest', 'element_attune'})
            self.assertEqual(perform(s, 'element_attune', 7), 'ELEMENT_CAMP_CHOSEN')
            ElementState(**asdict(s.elemental))

    def test_first_reaction_bonus_is_correct_and_preview_is_pure(self):
        for name in DOCTRINES:
            _, s = learned(name)
            e = s.elemental
            e.aura = 'hydro'
            before = copy.deepcopy(e)
            after, effect = card_effect(e, 'cryo')
            self.assertEqual(e, before)
            self.assertEqual(effect['reaction'], '冻结')
            self.assertEqual(effect['damage'], 8 if name == 'assault' else 5)
            self.assertEqual(after.block, 5 if name == 'bastion' else 2)
            self.assertEqual(after.energy, 3 if name == 'flow' else 2)
            self.assertTrue(after.doctrine_used)
            self.assertEqual(effect['doctrine_trigger'], name)
            after.aura = 'hydro'
            again, second = card_effect(after, 'cryo')
            self.assertEqual(second['doctrine_trigger'], '')
            self.assertEqual(second['damage'], 5)
            self.assertEqual(again.block - after.block, 2)
            self.assertEqual(again.energy, after.energy - 1)

    def test_nonreaction_does_not_consume_trigger_and_combined_reactions_only_trigger_once(self):
        _, s = learned('assault')
        e = s.elemental
        e.aura = None
        after, effect = card_effect(e, 'pyro')
        self.assertFalse(after.doctrine_used)
        self.assertEqual(effect['doctrine_trigger'], '')
        e.aura, e.cores = 'hydro', 1
        after, effect = card_effect(e, 'pyro')
        self.assertEqual(effect['reaction'], '蒸发 + 烈绽放')
        self.assertEqual(effect['bonus_damage'], 5 + 8 + 3)
        self.assertTrue(after.doctrine_used)

    def test_trigger_reset_on_next_turn_and_floor_but_doctrine_persists(self):
        _, s = learned('flow')
        s.elemental.doctrine_used = True
        perform(s, 'element_end', 4)
        self.assertFalse(s.elemental.doctrine_used)
        s.elemental.doctrine_used = True
        s.round_no = 5
        prepare_floor(s)
        self.assertEqual(s.elemental.doctrine, 'flow')
        self.assertFalse(s.elemental.doctrine_used)

    def test_bad_or_unearned_doctrines_and_old_edition_injection_rejected(self):
        _, s = ready(edition=3)
        for fields in ({'doctrine': 'flow'}, {'doctrine': 'fake'}, {'doctrine': []},
                       {'doctrine_used': True}, {'doctrine_used': 1}, {'edition': 99}):
            with self.assertRaises(ValueError):
                ElementState(**{**asdict(s.elemental), **fields})
        _, s = learned('flow')
        for fields in ({'edition': 2}, {'doctrine': 'assault'},
                       {'camps': s.elemental.camps + [{'floor': 7, 'action': 'element_doctrine_flow'}]}):
            with self.assertRaises(ValueError):
                ElementState(**{**asdict(s.elemental), **fields})

    def test_doctrine_preview_matches_actual_damage_and_persists_trigger(self):
        game, s = learned('flow')
        e = s.elemental
        e.hand = ['cryo', 'geo', 'anemo', 'electro']
        e.draw, e.discard = ['pyro', 'hydro', 'dendro'], []
        e.aura = 'hydro'
        before = copy.deepcopy(s)
        row = next(x for x in preview(s)['options'] if x['choice_id'] == 'element_cryo')
        self.assertEqual(s, before)
        perform(s, 'element_cryo', 4)
        self.assertEqual((s.elemental.energy, s.elemental.block), (row['energy_after'], row['block_after']))
        self.assertEqual(s.elemental.enemy_hp, before.elemental.enemy_hp - row['damage'])
        restored = unpack(GameSave(game=asdict(game), combat=json.loads(json.dumps(asdict(s)))))[1]
        self.assertEqual(s, restored)
        self.assertTrue(restored.elemental.doctrine_used)

    def test_all_three_builds_finish_and_score_is_only_actual_damage_plus_rare(self):
        for doctrine in DOCTRINES:
            for seed in (0, 2, 7, 13):
                result, s, _ = run(seed, 'combo_' + doctrine, edition=3)
                self.assertEqual(result['status'], 'cleared', result)
                self.assertEqual(result['doctrine'], doctrine)
                self.assertGreater(result['doctrine_triggers'], 0)
                self.assertEqual(s.score, sum(x['points'] for x in s.elemental.log))
                self.assertEqual(Counter(s.elemental.hand+s.elemental.draw+s.elemental.discard), Counter(DECK))
                ElementState(**asdict(s.elemental))


class GambitApi(unittest.TestCase):
    setUp = element_tests.ElementApi.setUp
    tearDown = element_tests.ElementApi.tearDown
    state = element_tests.ElementApi.state
    post = element_tests.ElementApi.post
    body = element_tests.ElementApi.body
    start = element_tests.ElementApi.start

    def parry(self):
        return next(x['choice_id'] for x in self.state()['combat']['options'] if x.get('kind') == 'parry')

    def test_parry_receipt_replay_stale_and_restart(self):
        self.start()
        body = self.body(self.parry(), 'parry-once')
        response = self.post(body).json()
        self.assertEqual(response['code'], 'ELEMENT_PARRIED')
        self.assertEqual(self.post(body).json(), response)
        self.assertEqual(self.post({**body, 'request_id': 'stale-parry'}).status_code, 409)
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.assertEqual(self.state(), response['state'])
        self.assertIn('titles.ELEMENT_PARRIED=', self.client.get('/').text)

    def test_new_authoritative_fields_cannot_be_injected(self):
        self.start()
        before = self.state()
        body = self.body(self.parry())
        for extra in ({'doctrine': 'flow'}, {'doctrine_used': False}, {'block': 99}, {'edition': 3}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.state(), before)

    def test_two_stores_parry_and_hold_compete_for_single_commit(self):
        self.start()
        parry = self.body(self.parry())
        hold = self.body(next(x['choice_id'] for x in self.state()['combat']['options'] if x.get('kind') == 'hold'))
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(self.post, parry), pool.submit(other.post, self.base+'/action', headers=self.headers, json=hold)]
                self.assertEqual(sorted(j.result().status_code for j in jobs), [200, 409])
        self.assertTrue(self.state()['combat']['elemental']['hold_used'])

    def test_full_http_build_receipt_and_no_repeat_camp_reward(self):
        self.start()
        expected, _, actions = run(2, 'combo_flow', edition=3)
        for action in actions:
            body = self.body(action)
            response = self.post(body)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['ok'], response.json()['code'])
            if action.startswith('element_doctrine_'):
                self.assertEqual(self.post(body).json(), response.json())
                before = self.state()
                self.assertFalse(self.post(self.body('element_rest')).json()['ok'])
                self.assertEqual(self.state(), before)
        e = self.state()['combat']['elemental']
        self.assertEqual((self.state()['combat']['status'], e['doctrine']['key'], e['hp']),
                         ('cleared', 'flow', expected['hp']))


if __name__ == '__main__':
    unittest.main()
