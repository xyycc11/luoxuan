"""使用临时SQLite文件，不触碰用户存档，不连接PostgreSQL。"""
import tempfile
import unittest
from pathlib import Path

from echoworld.combat import advance_round, start_combat
from echoworld.database import GameSave, SaveStore, pack, unpack
from echoworld.rules import accept_permit_quest, submit_herb_for_permit


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.url = "sqlite+pysqlite:///" + (Path(self.temp.name) / "save.sqlite3").as_posix()
        self.store = SaveStore(self.url)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def read(self):
        with self.store.sessions.begin() as db:
            return unpack(db.get(GameSave, "world-alice"))

    def test_commit_survives_store_recreation_without_reseeding(self):
        with self.store.sessions.begin() as db:
            row = db.get(GameSave, "world-alice")
            game, _ = unpack(row)
            accept_permit_quest(game)
            submit_herb_for_permit(game)
            pack(row, game, None)
        self.store.close()
        self.store = SaveStore(self.url)
        game, _ = self.read()
        self.assertEqual((game.has_permit, game.quest_stage, game.inventory), (True, "completed", {"herb": 0}))

    def test_exception_after_flush_rolls_back_all_game_fields(self):
        with self.assertRaises(RuntimeError):
            with self.store.sessions.begin() as db:
                row = db.get(GameSave, "world-alice")
                game, combat = unpack(row)
                accept_permit_quest(game)
                submit_herb_for_permit(game)
                pack(row, game, combat)
                db.flush()  # 已发SQL，不等于已commit。
                raise RuntimeError("injected failure")
        game, _ = self.read()
        self.assertEqual((game.has_permit, game.quest_stage, game.inventory), (False, "not_started", {"herb": 1}))

    def test_dataclass_mutation_alone_is_not_persisted(self):
        with self.store.sessions.begin() as db:
            game, _ = unpack(db.get(GameSave, "world-alice"))
            game.has_permit = True
            game.inventory["herb"] = 99
        self.assertFalse(self.read()[0].has_permit)
        self.assertEqual(self.read()[0].inventory, {"herb": 1})

    def test_combat_round_events_and_seed_survive_recreation(self):
        with self.store.sessions.begin() as db:
            row = db.get(GameSave, "world-alice")
            game, _ = unpack(row)
            game.has_permit = True
            combat = start_combat(game, seed=2)
            advance_round(combat, "strike", 1)
            pack(row, game, combat)
        self.store.close()
        self.store = SaveStore(self.url)
        _, combat = self.read()
        self.assertEqual((combat.round_no, combat.score, len(combat.events)), (2, 1, 1))
        self.assertEqual(advance_round(combat, "guard", 2).code, "ROUND_RESOLVED")

    def test_player_snapshots_are_independent(self):
        with self.store.sessions.begin() as db:
            alice, _ = unpack(db.get(GameSave, "world-alice"))
            bob, _ = unpack(db.get(GameSave, "world-bob"))
        alice.inventory["herb"] = 88
        self.assertEqual(bob.inventory, {})
        self.assertEqual(self.read()[0].inventory, {"herb": 1})
