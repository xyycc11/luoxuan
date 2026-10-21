"""生成任务、保守预算与回执。SQL领取/短事务；模型不持有游戏写事务。"""
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
from threading import BoundedSemaphore, Event, Thread
from uuid import uuid4

from sqlalchemy import Integer, JSON, Float, String, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, GameSave
from .action_store import RequestConflict, action_scope, digest

MAX_RESERVED = 11
MAX_ADVICE = 3


class AIBudget(Base):
    __tablename__ = "game_ai_budgets"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    started: Mapped[int] = mapped_column(Integer, default=0)
    advice_count: Mapped[int] = mapped_column(Integer, default=0)
    milestones: Mapped[list] = mapped_column(JSON, default=list)


class AIGeneration(Base):
    __tablename__ = "game_ai_generations"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    owner: Mapped[str] = mapped_column(String(40))
    deadline: Mapped[float] = mapped_column(Float)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fallback: Mapped[dict] = mapped_column(JSON)
    code: Mapped[str] = mapped_column(String(50))


class AIReceipt(Base):
    __tablename__ = "game_ai_receipts"
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    generation_key: Mapped[str] = mapped_column(String(64))


class GenerationService:
    def __init__(self, store, *, timeout=10):
        self.store, self.timeout = store, timeout
        self.slots, self.closed = BoundedSemaphore(2), Event()

    def close(self):
        self.closed.set()  # daemon模型线程可能仍等网络；不能把客户端截止时间当取消上游费用。

    def _begin(self, db):
        if self.store.engine.dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _view(row, cached):
        content = deepcopy(row.result)
        trace = content.pop("_generation_trace", {}) if content else {}
        trace.update(task=row.task, model_calls=row.calls, reserved_calls=row.reserved, code=row.code,
                     usage=None, source_ids=[entry["id"] for entry in (content or {}).get("highlights", [])])
        return {"status": row.status, "code": row.code, "job_id": row.key,
                "cached": cached, "reserved_calls": row.reserved, "model_calls": row.calls,
                "content": content, "trace": trace}

    def status(self, player, world):
        with self.store.lock, self.store.sessions() as db:
            row = db.get(AIBudget, action_scope(player, world))
            return {"reserved_calls": row.reserved if row else 0, "started_calls": row.started if row else 0,
                    "max_reserved_calls": MAX_RESERVED, "advice_queries": row.advice_count if row else 0,
                    "max_advice_queries": MAX_ADVICE,
                    "notice": "预算为保守预留请求数，不是费用；不含既有局外NPC聊天。失败/未知结果不自动返还预留。"}

    def generate(self, player, world, version, request_id, request_payload, cache_input,
                 fallback, compute, *, enabled, slot, reserve, check_current=True):
        scope = action_scope(player, world)
        fingerprint = digest(request_payload)
        key = digest([scope, cache_input])
        acquired = False
        try:
            with self.store.lock, self.store.sessions.begin() as db:
                self._begin(db)
                save = db.scalar(select(GameSave).where(GameSave.world_id == world).with_for_update())
                if save is None or save.player_id != player:
                    raise RequestConflict("SAVE_NOT_OWNED", 403)
                if check_current and save.version != version:
                    raise RequestConflict("AI_STATE_CHANGED")
                receipt = db.get(AIReceipt, (scope, request_id))
                if receipt:
                    if receipt.payload_hash != fingerprint:
                        raise RequestConflict("AI_REQUEST_ID_CONFLICT")
                    if check_current and receipt.generation_key != key:
                        raise RequestConflict("AI_CONTEXT_CHANGED")
                    key = receipt.generation_key
                old = db.scalar(select(AIGeneration).where(AIGeneration.key == key).with_for_update())
                if old:
                    if old.status == "running" and old.deadline <= time.time():
                        old.status, old.code, old.result = "unknown", "RESULT_UNKNOWN", old.fallback
                    if receipt is None:
                        db.add(AIReceipt(scope=scope, request_id=request_id, payload_hash=fingerprint, generation_key=key))
                    return self._view(old, True)
                budget = db.get(AIBudget, scope)
                if budget is None:
                    budget = AIBudget(scope=scope, reserved=0, started=0, advice_count=0, milestones=[])
                    db.add(budget)
                allowed = (budget.reserved + reserve <= MAX_RESERVED and
                           (budget.advice_count < MAX_ADVICE if slot == "advice" else slot not in budget.milestones))
                code = "LOCAL_RULES" if not enabled else "AI_BUDGET_EXHAUSTED" if not allowed else "AI_RESOURCE_BUSY"
                acquired = bool(enabled and allowed and not self.closed.is_set() and self.slots.acquire(blocking=False))
                token = uuid4().hex
                row = AIGeneration(key=key, scope=scope, task=slot, version=version, status="running" if acquired else "done",
                    owner=token, deadline=time.time() + self.timeout, reserved=reserve if acquired else 0, calls=0,
                    result=None if acquired else deepcopy(fallback), fallback=deepcopy(fallback), code="GENERATING" if acquired else code)
                db.add(row)
                db.add(AIReceipt(scope=scope, request_id=request_id, payload_hash=fingerprint, generation_key=key))
                if acquired:
                    budget.reserved += reserve
                    if slot == "advice":
                        budget.advice_count += 1
                    else:
                        budget.milestones = [*budget.milestones, slot]
                else:
                    return self._view(row, False)
        except (IntegrityError, OperationalError):
            if acquired:
                self.slots.release()
            raise RequestConflict("AI_DATABASE_BUSY", 503) from None
        except Exception:
            if acquired:
                self.slots.release()
            raise
        future = Future()
        deadline = time.monotonic() + self.timeout

        def on_call():
            if self.closed.is_set() or time.monotonic() >= deadline:
                return False
            with self.store.lock, self.store.sessions.begin() as db:
                self._begin(db)
                task = db.scalar(select(AIGeneration).where(AIGeneration.key == key).with_for_update())
                if task.status != "running" or task.owner != token or task.calls >= task.reserved:
                    return False
                if check_current and db.get(GameSave, world).version != version:
                    return False
                task.calls += 1
                budget = db.scalar(select(AIBudget).where(AIBudget.scope == scope).with_for_update())
                budget.started += 1
                return True

        def work():
            try:
                future.set_result(compute(on_call, deadline))
            except Exception as error:
                future.set_exception(error)
            finally:
                self.slots.release()

        Thread(target=work, daemon=True, name="echoworld-game-ai").start()
        code, status = "GENERATED", "done"
        try:
            result = future.result(timeout=self.timeout)
        except FutureTimeout:
            result, code, status = deepcopy(fallback), "TIMEOUT_RESULT_UNKNOWN", "unknown"
        except Exception:
            result, code, status = deepcopy(fallback), "MODEL_OUTPUT_REJECTED", "degraded"
        result = deepcopy(result)
        result["_generation_trace"] = {"duration_ms": round((self.timeout - max(0, deadline-time.monotonic())) * 1000, 3)}
        with self.store.lock, self.store.sessions.begin() as db:
            self._begin(db)
            row = db.scalar(select(AIGeneration).where(AIGeneration.key == key).with_for_update())
            if row.owner == token and row.status == "running":
                row.result, row.code, row.status = result, code, status
            view = self._view(row, False)
            save = db.get(GameSave, world)
            stale = check_current and save.version != version
        if stale:
            # 在事务提交后返回过期错误，保留结果而非回滚成running。
            raise RequestConflict("AI_STATE_CHANGED")
        return view
