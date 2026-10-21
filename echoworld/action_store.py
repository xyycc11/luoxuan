"""请求回执＋CAS：状态变化和原结果同事务提交；不自动重试业务动作。"""
import hashlib
import json
from secrets import randbits
from copy import deepcopy
from dataclasses import asdict

from sqlalchemy import JSON, Integer, String, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, GameSave, unpack
from .game_actions import apply_action, public_state
from .combat import TurnResult
from .narration import scene_facts


class ActionReceipt(Base):
    __tablename__ = "action_receipts"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int] = mapped_column(Integer, default=200)
    narration_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    narration: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RequestConflict(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def action_scope(player, world):
    return digest([player, world])


def read_receipt(store, player, world, request_id):
    with store.lock, store.sessions.begin() as db:
        row = db.get(ActionReceipt, (action_scope(player, world), request_id))
        return (deepcopy(row.response), row.http_status, row.payload_hash) if row else None


def execute_action(store, player, world, payload, *, seed=2):
    """API已校验类型/归属；仍在事务内再核验归属，不信客户端身份字段。"""
    scope, request_id = action_scope(player, world), payload["request_id"]
    fingerprint = digest({key: value for key, value in payload.items() if key != "request_id"})
    try:
        with store.lock, store.sessions.begin() as db:
            if store.engine.dialect.name == "sqlite":
                # SQLite提前取得写锁，避免两个读事务同时升级写锁；CAS仍保留。
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            old = db.get(ActionReceipt, (scope, request_id))
            if old:
                if old.payload_hash != fingerprint:
                    raise RequestConflict("REQUEST_ID_CONFLICT")
                return deepcopy(old.response), old.http_status
            # 唯一键先占位；竞争者只能读已提交结果，半成品不会单独提交。
            receipt = ActionReceipt(scope=scope, request_id=request_id,
                                    payload_hash=fingerprint, response=None)
            db.add(receipt)
            db.flush()
            row = db.get(GameSave, world)
            if row is None or row.player_id != player:
                raise RequestConflict("SAVE_NOT_OWNED", 403)
            game, combat = unpack(row)
            version = row.version
            if payload["expected_version"] != version:
                result = {"ok": False, "code": "VERSION_CONFLICT", "scene": None,
                          "state": {**public_state(game, combat), "version": version}}
                status = 409
            else:
                before = (asdict(game), asdict(combat) if combat else None)
                # 正式本机入口随机生成；测试/显式演示可固定。种子不接受玩家参数。
                combat_seed = randbits(63) if seed is None and payload["action"] in {"tower", "tower_blessed", "tower_expedition", "tower_spire", "tower_routes", "tower_cards", "tower_elements"} else seed
                result, combat = apply_action(game, combat, payload["action"], payload.get("expected_round"),
                                              seed=combat_seed)
                after = (asdict(game), asdict(combat) if combat else None)
                if result["ok"] and before != after:
                    changed = db.execute(update(GameSave).where(
                        GameSave.world_id == world, GameSave.player_id == player,
                        GameSave.version == version).values(game=after[0], combat=after[1], version=version + 1))
                    if changed.rowcount != 1:
                        # 回滚占位和所有变化；不在旧快照上再次执行。
                        raise RequestConflict("VERSION_CONFLICT")
                    version += 1
                result["state"]["version"] = version
                status = 200
                if result["scene"]:
                    # 营地/祝福不是上一轮战斗：回执不得重复描述或计入上一轮奖励。
                    battle_event = (combat.events[-1] if combat.events and result["code"] in
                                    {"ROUND_RESOLVED", "PLAYER_DEAD", "TOWER_CLEARED"} else None)
                    camp_event = combat.camp_events[-1] if result["code"] == "CAMP_CHOSEN" else None
                    relic_event = combat.relic_events[-1] if result["code"] == "RELIC_CHOSEN" else None
                    route_event = combat.route_events[-1] if result["code"] == "ROUTE_CHOSEN" else None
                    card_draft = combat.card_drafts[-1] if result["code"] == "CARD_DRAFTED" else None
                    turn = TurnResult(result["code"], battle_event, camp_event, relic_event, route_event, card_draft)
                    receipt.narration_facts = scene_facts(combat, turn)
            result["request_id"] = request_id
            receipt.response, receipt.http_status = result, status
        return result, status
    except IntegrityError:
        # 同ID竞争失败后只查询回执，不再次运行apply_action。
        old = read_receipt(store, player, world, request_id)
        if old and old[2] == fingerprint:
            return old[0], old[1]
        raise RequestConflict("REQUEST_ID_CONFLICT") from None
    except OperationalError:
        raise RequestConflict("DATABASE_BUSY", 503) from None
