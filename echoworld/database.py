"""SQL权威存档：SQLite已实测，PostgreSQL入口待联调。"""
from copy import deepcopy
from dataclasses import asdict
from threading import Lock, RLock

from sqlalchemy import JSON, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .combat import CombatState, Monster, TurnEvent
from .expedition import CampEvent
from .relics import RelicEvent
from .routes import RouteEvent
from .cards import DeckState, CardDraftEvent
from .state import GameState


class Base(DeclarativeBase):
    pass


class GameSave(Base):
    __tablename__ = "game_saves"
    world_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(80), index=True)
    game: Mapped[dict] = mapped_column(JSON)
    combat: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


def unpack(row: GameSave) -> tuple[GameState, CombatState | None]:
    """返回脱离ORM的副本，规则不会暗中改数据库对象。"""
    game = GameState(**deepcopy(row.game))
    combat = None
    if row.combat is not None:
        payload = deepcopy(row.combat)
        payload["monsters"] = tuple(Monster(**item) for item in payload["monsters"])
        payload["current_monster"] = Monster(**payload["current_monster"]) if payload["current_monster"] else None
        payload["events"] = [TurnEvent(**event) for event in payload["events"]]
        payload["camp_events"] = [CampEvent(**event) for event in payload.get("camp_events", [])]
        payload["relic_events"] = [RelicEvent(**event) for event in payload.get("relic_events", [])]
        payload["route_events"] = [RouteEvent(**event) for event in payload.get("route_events", [])]
        payload["cards"] = DeckState(**payload["cards"]) if payload.get("cards") is not None else None
        payload["card_drafts"] = [CardDraftEvent(**event) for event in payload.get("card_drafts", [])]
        combat = CombatState(**payload)
    return game, combat


def pack(row: GameSave, game: GameState, combat: CombatState | None) -> None:
    # 重新赋值完整JSON，让ORM检测到变化，不只改嵌套dict。
    row.game = asdict(game)
    row.combat = asdict(combat) if combat is not None else None


class SaveStore:
    def __init__(self, url: str = "sqlite+pysqlite:///:memory:"):
        options = {}
        if url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if url.endswith(":memory:"):
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        # 只协调同一进程，不是跨进程CAS或分布式锁。
        self.lock = RLock()
        self.chat_lock = Lock()
        Base.metadata.create_all(self.engine)
        # create_all不升级旧表。只补这次明确的列，不改原有JSON或重置存档。
        migrations = {"game_saves": {"version": "INTEGER NOT NULL DEFAULT 0"},
                      "action_receipts": {"narration_facts": "JSON", "narration": "JSON"}}
        inspector = inspect(self.engine)
        for table, additions in migrations.items():
            if not inspector.has_table(table):
                continue
            columns = {column["name"] for column in inspector.get_columns(table)}
            with self.engine.begin() as connection:
                for name, sql_type in additions.items():
                    if name not in columns:
                        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
        self.seed_demo()

    def seed_demo(self) -> None:
        """只补不存在的夹具，重启不覆盖玩家进度。"""
        with self.lock, self.sessions.begin() as db:
            for game in (GameState("alice", "world-alice", inventory={"herb": 1}),
                         GameState("bob", "world-bob")):
                if db.get(GameSave, game.world_id) is None:
                    db.add(GameSave(world_id=game.world_id, player_id=game.player_id,
                                    game=asdict(game), combat=None))

    def close(self) -> None:
        self.engine.dispose()
