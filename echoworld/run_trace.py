"""一次聊天的脱敏事件记录；不是Checkpoint或幂等回执。"""
from contextlib import contextmanager
from time import perf_counter
from uuid import uuid4

from sqlalchemy import JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from .chat_memory import ChatScope
from .database import Base, SaveStore


class ChatRun(Base):
    __tablename__ = "chat_runs"
    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(80), index=True)
    world_id: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[str] = mapped_column(String(40))
    record: Mapped[dict] = mapped_column(JSON)


class RunTrace:
    def __init__(self, scope: ChatScope):
        self.scope = scope
        self.run_id = uuid4().hex
        self.events = []
        self.summary = {"status": "running", "usage": None}

    @contextmanager
    def span(self, phase: str):
        started = perf_counter()
        event = {"phase": phase, "status": "ok", "error_code": None}
        try:
            yield
        except Exception:
            # 不记录exception文本/玩家输入/模型全文/工具参数/凭据。
            event.update(status="error", error_code="PHASE_FAILED")
            raise
        finally:
            event["duration_ms"] = round((perf_counter() - started) * 1000, 3)
            self.events.append(event)

    def payload(self) -> dict:
        return {"schema_version": 1, "run_id": self.run_id,
                "session_id": self.scope.session_id, "npc_id": self.scope.npc_id, "events": list(self.events),
                "summary": dict(self.summary)}

    def save(self, store: SaveStore) -> bool:
        # 单独短事务、尽力持久化；故障不能逆向撤销已保存聊天。
        try:
            with store.lock, store.sessions.begin() as db:
                old = db.get(ChatRun, self.run_id)
                if old:
                    old.record = self.payload()
                else:
                    db.add(ChatRun(run_id=self.run_id, player_id=self.scope.player_id,
                                   world_id=self.scope.world_id, session_id=self.scope.session_id,
                                   record=self.payload()))
            return True
        except Exception:
            return False


def load_run(store: SaveStore, run_id: str, player_id: str, world_id: str) -> dict | None:
    with store.lock, store.sessions.begin() as db:
        row = db.scalar(select(ChatRun).where(ChatRun.run_id == run_id,
                        ChatRun.player_id == player_id, ChatRun.world_id == world_id))
        return row.record if row else None
