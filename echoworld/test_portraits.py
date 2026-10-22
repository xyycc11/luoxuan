"""旅人外观保存在既有SQL JSON；角色图纯展示，不影响权威规则。"""
import copy
import json
import struct
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient
from echoworld import test_routes
from echoworld.api import create_app
from echoworld.database import GameSave, unpack
from echoworld.game_actions import apply_action
from echoworld.state import GameState
from echoworld.tools import execute_tool


class TravelerRules(unittest.TestCase):
    def test_gender_only_changes_appearance_not_permission_or_inventory(self):
        for choice in ('aether', 'lumine'):
            game = GameState('p', 'w', inventory={'herb': 2})
            before = asdict(game)
            result, combat = apply_action(game, None, 'traveler_' + choice)
            self.assertEqual((result['ok'], result['code']), (True, 'TRAVELER_SELECTED'))
            self.assertIsNone(combat)
            self.assertEqual({k:v for k,v in asdict(game).items() if k != 'traveler'},
                             {k:v for k,v in before.items() if k != 'traveler'})
            self.assertEqual(game.traveler, choice)

    def test_selected_traveler_cannot_be_replaced(self):
        game = GameState('p','w', traveler='aether')
        before = copy.deepcopy(game)
        result, _ = apply_action(game, None, 'traveler_lumine')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'TRAVELER_ALREADY_SELECTED')
        self.assertEqual(game, before)

    def test_invalid_traveler_values_rejected(self):
        for value in ('male', 'varka', '', 1, True, [], {}):
            with self.assertRaises(ValueError):
                GameState('p','w', traveler=value)

    def test_legacy_json_defaults_unselected_and_new_state_roundtrips(self):
        game = GameState('p','w')
        legacy = asdict(game)
        legacy.pop('traveler')
        self.assertIsNone(unpack(GameSave(game=legacy, combat=None))[0].traveler)
        game.traveler = 'lumine'
        restored, _ = unpack(GameSave(game=json.loads(json.dumps(asdict(game))), combat=None))
        self.assertEqual(restored, game)

    def test_model_readonly_tools_cannot_select_or_inject_traveler(self):
        game = GameState('p','w')
        before = copy.deepcopy(game)
        for action in ('traveler_aether', 'traveler_lumine'):
            self.assertFalse(execute_tool(game, action, {})['ok'])
        self.assertFalse(execute_tool(game, 'check_permit', {'traveler':'aether'})['ok'])
        self.assertEqual(game, before)


class PortraitApi(unittest.TestCase):
    setUp = test_routes.RouteApi.setUp
    tearDown = test_routes.RouteApi.tearDown
    state = test_routes.RouteApi.state
    post = test_routes.RouteApi.post
    body = test_routes.RouteApi.body

    def test_selection_receipt_replay_restart_and_scoped_player(self):
        self.assertIsNone(self.state()['traveler'])
        before = self.state()
        body = self.body('traveler_lumine', 'traveler-once')
        result = self.post(body)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['ok'])
        self.assertEqual(self.post(body).json(), result.json())
        self.assertEqual(self.state()['version'], before['version'] + 1)
        bob = self.client.get('/api/v1/worlds/world-bob/state',headers={'Authorization':'Bearer demo-bob'}).json()
        self.assertIsNone(bob['traveler'])
        self.client.__exit__(None,None,None)
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.assertEqual(self.state()['traveler'], 'lumine')

    def test_request_cannot_inject_authoritative_fields_or_access_other_save(self):
        body = self.body('traveler_aether')
        before = self.state()
        for extra in ({'traveler':'lumine'}, {'has_permit':True}, {'hp':99}, {'player_id':'bob'}):
            self.assertEqual(self.post({**body, **extra}).status_code, 422)
        self.assertEqual(self.client.post(self.base+'/action',headers={'Authorization':'Bearer demo-bob'},json=body).status_code,403)
        self.assertEqual(self.state(), before)

    def test_two_stores_cannot_select_two_different_travelers(self):
        bodies = [self.body('traveler_aether'), self.body('traveler_lumine')]
        with TestClient(create_app(database_url=self.url)) as other:
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs=[pool.submit(self.post,bodies[0]),pool.submit(other.post,self.base+'/action',headers=self.headers,json=bodies[1])]
                self.assertEqual(sorted(job.result().status_code for job in jobs), [200,409])
        self.assertIn(self.state()['traveler'], ('aether','lumine'))

    def test_existing_combat_can_fill_missing_cosmetic_without_changing_battle(self):
        for action in ('accept','submit','tower_elements'):
            self.assertTrue(self.post(self.body(action)).json()['ok'])
        before = self.state()
        self.assertTrue(self.post(self.body('traveler_aether')).json()['ok'])
        self.assertEqual(self.state()['combat'], before['combat'])
        self.assertEqual(self.state()['inventory'], before['inventory'])
        self.assertEqual(self.state()['has_permit'], before['has_permit'])

    def test_same_choice_is_noop_and_different_choice_does_not_increment_version(self):
        self.post(self.body('traveler_aether'))
        before = self.state()
        self.assertTrue(self.post(self.body('traveler_aether')).json()['ok'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.post(self.body('traveler_lumine')).json()['code'],'TRAVELER_ALREADY_SELECTED')
        self.assertEqual(self.state(), before)

    def test_all_local_original_images_are_available_without_external_calls(self):
        for name in ('aether','lumine','venti','varka'):
            response = self.client.get('/assets/characters/'+name+'.jpg')
            self.assertEqual(response.status_code,200,name)
            self.assertTrue(response.content.startswith(b'\xff\xd8'))
            self.assertTrue(response.content.endswith(b'\xff\xd9'))
        for name in ('hilichurl','wooden-mitachurl','pyro-slime','anemo-samachurl',
                     'ruin-guard','unusual-hilichurl','ruin-grader','abyss-lector'):
            response=self.client.get('/assets/monsters/'+name+'.png')
            self.assertEqual(response.status_code,200,name)
            self.assertEqual(response.content[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II',response.content[16:24]),(256,256))

    def test_image_controls_and_camp_concealment_are_present(self):
        html = self.client.get('/').text
        for value in ('travelerPicker','data-traveler="aether"','data-traveler="lumine"','guardPortraitChoices','data-npc="venti"',
                      'data-npc="varka"','enemyPortrait',"combat.phase==='combat'", 'MONSTER_ART'):
            self.assertIn(value,html)
        self.assertNotIn('<canvas',html)


if __name__=='__main__':
    unittest.main()
