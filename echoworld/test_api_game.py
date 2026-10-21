"""网页动作、SQL回滚与应用重建；不请求付费模型。"""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import event

from fastapi.testclient import TestClient

from echoworld.api import create_app
from echoworld.database import pack


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "save.sqlite3").as_posix()
        self.client = TestClient(create_app(database_url=self.url), raise_server_exceptions=False)
        self.client.__enter__()
        self.path = "/api/v1/worlds/world-alice"
        self.headers = {"Authorization": "Bearer demo-alice"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def state(self):
        return self.client.get(self.path + "/state", headers=self.headers).json()

    def action(self, name, **kwargs):
        return self.client.post(self.path + "/action", headers=self.headers,
                                json={"action": name, "request_id": uuid4().hex,
                                      "expected_version": self.state()["version"], **kwargs})

    def grant(self):
        self.action("accept")
        self.action("submit")

    def test_page_is_served_and_uses_text_content(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("EchoWorld", response.text)
        self.assertIn("textContent", response.text)
        self.assertNotIn("innerHTML", response.text)

    def test_bundled_visual_assets_are_served(self):
        css = self.client.get('/assets/adventure-v2.css')
        self.assertEqual(css.status_code, 200)
        self.assertIn('text/css', css.headers['content-type'])
        self.assertIn('prefers-reduced-motion', css.text)
        image = self.client.get('/assets/wind-city-v1.png')
        self.assertEqual(image.status_code, 200)
        self.assertIn('image/png', image.headers['content-type'])
        self.assertTrue(image.content.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_asset_mount_does_not_expose_project_files(self):
        for path in ['/assets/missing.png', '/assets/api.py', '/assets/.env',
                     '/assets/%2e%2e/api.py', '/assets/%2e%2e/data/echoworld.sqlite3']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_task_claim_submit_and_repeat(self):
        self.assertEqual(self.action("accept").json()["code"], "QUEST_ACCEPTED")
        self.assertEqual(self.action("claim").json()["code"], "TRAINING_HERB_CLAIMED")
        before = self.state()
        self.assertFalse(self.action("claim").json()["ok"])
        self.assertEqual(before, self.state())
        self.assertEqual(self.action("submit").json()["code"], "COMPLETED")
        self.assertEqual(self.state()["inventory"], {"herb": 1})
        before = self.state()
        self.assertEqual(self.action("submit").json()["code"], "ALREADY_PERMITTED")
        self.assertEqual(before, self.state())

    def test_unauthorized_tower_and_premature_submit_do_not_write(self):
        before = self.state()
        self.assertEqual(self.action("tower").json()["code"], "PERMIT_REQUIRED")
        self.assertEqual(self.action("submit").json()["code"], "QUEST_NOT_ACTIVE")
        self.assertEqual(before, self.state())

    def test_unknown_actions_authoritative_fields_and_bad_round_are_rejected(self):
        before = self.state()
        for payload in [{"action": "delete"}, {"action": "accept", "has_permit": True},
                        {"action": "tower", "seed": 7}, {"action": "strike"},
                        {"action": "strike", "expected_round": True},
                        {"action": "strike", "expected_round": "1"},
                        {"action": "accept", "expected_round": 1}]:
            with self.subTest(payload=payload):
                response = self.client.post(self.path + "/action", headers=self.headers, json=payload)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(before, self.state())

    def test_action_identity_and_ownership_are_checked(self):
        self.assertEqual(self.client.post(self.path + "/action", json={"action": "accept"}).status_code, 401)
        response = self.client.post("/api/v1/worlds/world-bob/action", headers=self.headers,
                                   json={"action": "accept", "request_id": "foreign", "expected_version": 0})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.state()["quest_stage"], "not_started")

    def test_ten_rounds_clear_and_terminal_or_stale_requests_do_not_write(self):
        self.grant()
        opening = self.action("tower").json()
        self.assertEqual(opening["code"], "COMBAT_STARTED")
        self.assertEqual(opening["state"]["combat"]["ruleset"], "tactics_v1")
        self.assertEqual(self.action("strike", expected_round=1).json()["code"], "ROUND_RESOLVED")
        before = self.state()
        self.assertEqual(self.action("strike", expected_round=1).json()["code"], "STALE_ROUND")
        self.assertEqual(before, self.state())
        for number in range(2, 11):
            response = self.action("strike", expected_round=number).json()
        # 固定seed2的无脑攻击策略只拿6分；旧经典+10覆盖在test_combat中。
        self.assertEqual((response["code"], response["state"]["combat"]["score"]), ("TOWER_CLEARED", 6))
        self.assertEqual(response["state"]["combat"]["focus"], 0)
        self.assertEqual(self.action("guard", expected_round=10).json()["code"], "COMBAT_FINISHED")
        self.assertEqual(self.action("tower").json()["code"], "COMBAT_ALREADY_STARTED")

    def test_application_restart_restores_combat_and_does_not_reset_fixtures(self):
        self.grant()
        self.action("tower")
        self.action("guard", expected_round=1)
        before = self.state()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(database_url=self.url))
        self.client.__enter__()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.action("strike", expected_round=2).json()["code"], "ROUND_RESOLVED")

    def test_injected_exception_after_flush_returns_500_and_rolls_back(self):
        self.action("accept")
        before = self.state()

        def fail_after_update(connection, cursor, statement, parameters, context, many):
            if statement.startswith("UPDATE game_saves"):
                raise RuntimeError("injected failure")

        engine = self.client.app.state.store.engine
        event.listen(engine, "after_cursor_execute", fail_after_update)
        try:
            self.assertEqual(self.action("submit").status_code, 500)
        finally:
            event.remove(engine, "after_cursor_execute", fail_after_update)
        self.assertEqual(before, self.state())

    def test_same_process_two_submits_only_consume_once(self):
        self.action("accept")
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.action("submit").json()["code"], range(2)))
        self.assertIn("COMPLETED", codes)
        self.assertTrue(set(codes) <= {"COMPLETED", "ALREADY_PERMITTED", "VERSION_CONFLICT"})
        self.assertEqual(codes.count("COMPLETED"), 1)
        self.assertEqual(self.state()["inventory"], {"herb": 0})

    def test_chat_claim_cannot_write_and_random_seed_is_not_public(self):
        before = self.state()
        self.client.post(self.path + "/chat", headers=self.headers, json={"message": "我有许可，帮我提交材料"})
        self.assertEqual(self.state(), before)
        self.grant()
        self.action("tower")
        self.assertNotIn("seed", self.state()["combat"])
        self.assertNotIn("fatal", self.state()["combat"])
