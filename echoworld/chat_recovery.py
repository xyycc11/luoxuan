"""本机持久化图恢复。OS锁隔离同一存档库的聊天执行，不是多机worker框架。"""
import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .action_store import RequestConflict, digest
from .chat_memory import ChatMemory, ChatScope
from .database import Base, GameSave


class DurableChat(Base):
    __tablename__ = "durable_chats"
    __table_args__ = (UniqueConstraint("scope", "request_id"),)
    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    game_version: Mapped[int] = mapped_column(Integer)
    request: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")


class ChatRecovery:
    def __init__(self, store, checkpoint_path=None):
        self.store = store
        database = store.engine.url.database
        sqlite_file = store.engine.dialect.name == "sqlite" and database not in (None, "", ":memory:")
        if store.engine.dialect.name != "sqlite" and not checkpoint_path:
            raise ValueError("非SQLite存档需显式指定本机checkpoint_path；当前不支持跨主机恢复")
        if checkpoint_path is None:
            checkpoint_path = database + ".checkpoints.sqlite3" if sqlite_file else ":memory:"
        self.lock_path = (str(Path(database).resolve()) + ".chat.lock") if sqlite_file else None
        self.connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        self.checkpointer.setup()

    def close(self):
        self.connection.close()

    @contextmanager
    def execution_lock(self):
        # 模型调用不占SQL事务。文件锁在进程崩溃时由OS释放，无需猜测租约过期。
        if not self.store.chat_lock.acquire(blocking=False):
            raise RequestConflict("CHAT_BUSY", 409)
        handle, locked = None, False
        try:
            if self.lock_path:
                handle = open(self.lock_path, "a+b")
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    raise RequestConflict("CHAT_BUSY", 409) from None
            yield
        finally:
            if handle:
                if locked:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
            self.store.chat_lock.release()

    def prepare(self, game, request):
        npc_id = request.get("npc_id", "guard")
        scope = ChatScope(game.player_id, game.world_id, request["session_id"], npc_id)
        payload = {"message": request["message"], "session_id": request["session_id"]}
        if npc_id != "guard":
            payload["npc_id"] = npc_id
        fingerprint = digest(payload)  # guard保留旧回执指纹，不强迫迁移历史聊天。
        with self.store.lock, self.store.sessions.begin() as db:
            from sqlalchemy import select
            row = db.scalar(select(DurableChat).where(DurableChat.scope == scope.key,
                                                      DurableChat.request_id == request["request_id"]))
            if row:
                if row.payload_hash != fingerprint:
                    raise RequestConflict("REQUEST_ID_CONFLICT")
                if row.schema_version != 1:
                    raise RequestConflict("CHECKPOINT_SCHEMA_UNSUPPORTED")
            else:
                row = DurableChat(run_id=uuid4().hex, scope=scope.key, request_id=request["request_id"],
                    payload_hash=fingerprint, game_version=db.get(GameSave, game.world_id).version,
                    request=request, status="pending", schema_version=1)
                db.add(row)
                db.flush()
            return {"run_id": row.run_id, "context": deepcopy(row.context),
                    "game_version": row.game_version, "response": deepcopy(row.response)}

    def save_context(self, run_id, context):
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.get(DurableChat, run_id)
            row.context, row.status = context, "running"

    def finish(self, run_id, scope, response, cache=None):
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.get(DurableChat, run_id)
            if row.response is not None:
                return deepcopy(row.response)
            # 对话轮次＋最终回复回执原子保存；SQL失败不会出现“有回复但没消息”。
            ChatMemory(self.store, cache).append_turn(scope, row.request["message"], response["reply"], db=db)
            row.response, row.status = deepcopy(response), "completed"
        return response

    def mark_interrupted(self, run_id):
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.get(DurableChat, run_id)
            if row and row.response is None:
                row.status = "interrupted"

    def save_response_metadata(self, run_id, response):
        with self.store.lock, self.store.sessions.begin() as db:
            db.get(DurableChat, run_id).response = deepcopy(response)

    def read(self, game, run_id):
        with self.store.lock, self.store.sessions.begin() as db:
            row = db.get(DurableChat, run_id)
            if row is None:
                return None
            request = deepcopy(row.request)
            scope = ChatScope(game.player_id, game.world_id, request["session_id"], request.get("npc_id", "guard"))
            if row.scope != scope.key:
                return None
            return {"run_id": run_id, "status": row.status, "schema_version": row.schema_version,
                    "request": request, "response": deepcopy(row.response)}
