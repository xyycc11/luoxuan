"""本机教学API：显式动作写SQL，聊天图只读；演示令牌不是生产认证。"""

import os
from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from .agent_graph import run_graph_turn
from .database import GameSave, SaveStore, pack, unpack
from .chat_memory import ChatMemory, ChatScope, RedisRecentCache
from .deepseek_agent import DeepSeekAgentModel
from .game_actions import apply_action, public_state
from .rules import guard_reply
from .state import GameState
from .run_trace import RunTrace, load_run
from .streaming import sse_frame
from .lore_rag import LoreRag, RagResult
from .reply_guard import check_reply, is_fact_question
from .long_memory import LongMemory, MemoryResult, is_preference_question
from .action_store import RequestConflict, execute_action, read_receipt
from .chat_recovery import ChatRecovery
from .action_store import ActionReceipt, action_scope, digest
from .narration import DeepSeekNarrator, narrate_facts
from .guard_skills import load_guard_skill, list_guards, beginner_question
from .shared_knowledge import SharedKnowledge, KnowledgeResult, FAQ
from .shared_knowledge import rules_fingerprint
from .ai_generation import GenerationService
from .game_ai import POLICY_VERSION, policy_fingerprint, visible_packet, default_selection, render_selection, model_task


class GameAIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(strict=True, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    expected_version: int = Field(strict=True, ge=0)
    npc_id: Literal["venti", "varka"] = "varka"
    goal: Literal["score", "recover", "explain"] = "explain"
    question: str = Field(default="", strict=True, max_length=600)
    use_model: bool = Field(default=False, strict=True)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value):
        return value.strip()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(strict=True, min_length=1, max_length=2000)
    session_id: str = Field(default="main", strict=True, pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    request_id: str = Field(default_factory=lambda: uuid4().hex, strict=True,
                            pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    npc_id: Literal["guard", "venti", "varka"] = "guard"

    @field_validator("message")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message不能只有空白")
        return value.strip()


class ChatResponse(BaseModel):
    reply: str
    rule_reply: str
    stop_reason: str
    model_calls: int
    tool_calls: int
    node_trace: list[str]
    memory_source: str
    history_turns: int
    cache_status: str
    run_id: str
    reply_policy: str
    trace_saved: bool
    rag_status: str = "disabled"
    sources: list[dict] = Field(default_factory=list)
    context_meta: dict = Field(default_factory=dict)
    long_memory_status: str = "disabled"
    long_memory_ids: list[str] = Field(default_factory=list)
    npc_id: str = "guard"
    npc_name: str = "守卫"
    skill_fingerprint: str | None = None
    public_knowledge_status: str = "disabled"
    public_knowledge_sources: list[dict] = Field(default_factory=list)


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style: Literal["brief", "detailed", "stepwise"]
    consent: bool = Field(strict=True)
    ttl_seconds: int = Field(default=86400, strict=True, ge=600, le=2592000)


class KnowledgeProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    npc_id: Literal["venti", "varka"]
    topic: Literal["permit", "quest", "tower", "combat", "memory"]
    suggestion: str = Field(strict=True, min_length=1, max_length=600)
    consent: bool = Field(strict=True)
    request_id: str = Field(strict=True, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class KnowledgeFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)
    helpful: bool = Field(strict=True)


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "claim", "submit", "enter", "tower", "tower_blessed", "tower_expedition", "tower_spire", "tower_routes", "tower_cards", "tower_elements",
                    "element_pyro", "element_hydro", "element_electro", "element_cryo", "element_dendro", "element_anemo", "element_geo",
                    "element_end", "element_rest", "element_attune",
                    "strike", "guard", "boon_wind", "boon_flame", "boon_knight",
                    "camp_rest", "camp_forge", "camp_cash",
                    "relic_ember", "relic_mirror", "relic_feather", "relic_lantern", "relic_echo", "relic_dew",
                    "route_rest", "route_hunt", "card_jab", "card_heavy", "card_guard", "card_meditate",
                    "card_lunge", "card_counter", "card_riposte", "card_charge", "card_breathe",
                    "draft_lunge", "draft_counter", "draft_riposte", "draft_charge", "draft_skip"]
    expected_round: int | None = Field(default=None, strict=True, ge=1, le=10)
    request_id: str = Field(strict=True, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    expected_version: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def round_is_required_only_in_combat(self):
        battle = self.action.startswith("element_") or self.action in {"strike", "guard", "boon_wind", "boon_flame", "boon_knight",
                                "camp_rest", "camp_forge", "camp_cash",
                                "relic_ember", "relic_mirror", "relic_feather", "relic_lantern", "relic_echo", "relic_dew",
                                "route_rest", "route_hunt", "card_jab", "card_heavy", "card_guard", "card_meditate",
                                "card_lunge", "card_counter", "card_riposte", "card_charge", "card_breathe",
                                "draft_lunge", "draft_counter", "draft_riposte", "draft_charge", "draft_skip"}
        if battle != (self.expected_round is not None):
            raise ValueError("战斗/祝福/营地/遗物/路线选择必须传expected_round，其他动作不传")
        return self


def create_app(model=None, *, database_url: str = "sqlite+pysqlite:///:memory:", cache=None, rag=None,
               memory_embedder=None, memory_index=None, checkpoint_path=None, narrator=None, combat_seed=2,
               environment_memory=False, shared_embedder=None, shared_index=None,
               game_ai_model=None, ai_timeout=10) -> FastAPI:
    # 工厂默认内存SQL，便于隔离测试；模块app使用文件URL。
    @asynccontextmanager
    async def lifespan(app):
        ensure_store()
        try:
            yield
        finally:
            app.state.game_ai.close()
            app.state.recovery.close()
            app.state.store.close()
            app.state.shared_knowledge.close()
            if isinstance(cache, RedisRecentCache):
                cache.close()
            if rag is not None:
                rag.close()
            if app.state.long_memory.index is not None:
                app.state.long_memory.index.close()

    app = FastAPI(title="EchoWorld本地教学API", lifespan=lifespan)
    initialization_lock = Lock()

    def ensure_store() -> SaveStore:
        with initialization_lock:
            if not hasattr(app.state, "store"):
                if database_url == DEFAULT_DATABASE_URL:
                    (Path(__file__).parent / "data").mkdir(exist_ok=True)
                app.state.store = SaveStore(database_url)
                app.state.game_ai = GenerationService(app.state.store, timeout=ai_timeout)
                app.state.recovery = ChatRecovery(app.state.store, checkpoint_path)
                app.state.long_memory = (LongMemory(app.state.store, memory_embedder, memory_index)
                    if memory_embedder is not None or memory_index is not None
                    else LongMemory.from_environment(app.state.store) if environment_memory or database_url == DEFAULT_DATABASE_URL
                    else LongMemory(app.state.store))
                app.state.shared_knowledge = (SharedKnowledge(app.state.store, shared_embedder, shared_index)
                    if shared_embedder is not None or shared_index is not None
                    else SharedKnowledge.from_environment(app.state.store) if environment_memory or database_url == DEFAULT_DATABASE_URL
                    else SharedKnowledge(app.state.store))
        return app.state.store

    @app.exception_handler(RequestConflict)
    async def request_conflict_handler(request, error):
        return JSONResponse(status_code=error.status, content={"detail": {"code": error.code}})

    def get_db():
        with ensure_store().sessions() as db:
            yield db  # 负责生命周期；写事务必须在返回成功前提交。

    # 公开夹具只用于本机演示，不能用于真实认证。
    tokens = {"demo-alice": "alice", "demo-bob": "bob"}
    router = APIRouter(prefix="/api/v1/worlds", tags=["game"])

    def get_player(authorization: Annotated[str | None, Header()] = None) -> str:
        token = authorization.removeprefix("Bearer ") if authorization else ""
        player = tokens.get(token) if authorization and authorization.startswith("Bearer ") else None
        if player is None:
            raise HTTPException(401, "演示令牌缺失或无效", headers={"WWW-Authenticate": "Bearer"})
        return player

    def load_owned(db: Session, world_id: str, player: str) -> GameSave:
        row = db.get(GameSave, world_id)
        if row is None:
            raise HTTPException(404, "存档不存在")
        if row.player_id != player:
            raise HTTPException(403, "无权访问该存档")
        return row

    def get_game(world_id: str, player: str = Depends(get_player), db: Session = Depends(get_db)) -> GameState:
        with ensure_store().lock, db.begin():
            game, _ = unpack(load_owned(db, world_id, player))
        # 读取事务已结束；耗时模型执行只拿独立快照，不占用事务。
        return game

    @router.post("/{world_id}/memories/preferences")
    def confirm_preference(req: PreferenceRequest, game: GameState = Depends(get_game)):
        if not req.consent:
            raise HTTPException(422, "必须明确同意保存偏好")
        return app.state.long_memory.confirm(game, req.style, consent=True, ttl_seconds=req.ttl_seconds)

    @router.delete("/{world_id}/memories/{memory_id}")
    def revoke_preference(memory_id: str, game: GameState = Depends(get_game)):
        if not app.state.long_memory.revoke(game, memory_id):
            raise HTTPException(404, "记忆不存在")
        return {"ok": True}

    @router.get("/{world_id}/knowledge/topics")
    def knowledge_topics(game: GameState = Depends(get_game)):
        return {"topics": [{"topic": key, "title": row["title"], "public_preview": row["text"]}
                            for key, row in FAQ.items()],
                "notice": "只审核发布标准FAQ；不公开建议原文，不训练模型，不改规则；可撤回。"}

    @router.post("/{world_id}/knowledge/candidates")
    def propose_knowledge(req: KnowledgeProposalRequest, game: GameState = Depends(get_game)):
        return app.state.shared_knowledge.propose(game, req.npc_id, req.topic, req.suggestion,
                                                  consent=req.consent, request_id=req.request_id)

    @router.get("/{world_id}/knowledge/candidates")
    def own_knowledge_candidates(game: GameState = Depends(get_game)):
        return {"candidates": app.state.shared_knowledge.candidates(game)}

    @router.delete("/{world_id}/knowledge/candidates/{candidate_id}")
    def withdraw_knowledge(candidate_id: str, game: GameState = Depends(get_game)):
        return app.state.shared_knowledge.withdraw(game, candidate_id)

    @router.get("/{world_id}/knowledge/public")
    def public_knowledge(npc_id: Literal["venti", "varka"] = "venti", game: GameState = Depends(get_game)):
        result = app.state.shared_knowledge.retrieve("共享知识", npc_id, k=5)
        return {"status": result.status, "entries": result.hits}

    @router.post("/{world_id}/knowledge/public/{entry_id}/feedback")
    def knowledge_feedback(entry_id: str, req: KnowledgeFeedbackRequest, game: GameState = Depends(get_game)):
        return app.state.shared_knowledge.feedback(game, entry_id, req.version, req.helpful)

    @router.get("/{world_id}/state")
    def read_state(world_id: str, player: str = Depends(get_player), db: Session = Depends(get_db)):
        with ensure_store().lock, db.begin():
            row = load_owned(db, world_id, player)
            game, combat = unpack(row)
            version = row.version
        return {**public_state(game, combat), "version": version}

    @router.post("/{world_id}/action")
    def action(req: ActionRequest, world_id: str, player: str = Depends(get_player), db: Session = Depends(get_db)):
        with ensure_store().lock, db.begin():
            load_owned(db, world_id, player)
        result, status = execute_action(ensure_store(), player, world_id, req.model_dump(), seed=combat_seed)
        return JSONResponse(status_code=status, content=result)

    @router.get("/{world_id}/actions/{request_id}")
    def action_receipt(request_id: str, game: GameState = Depends(get_game)):
        old = read_receipt(ensure_store(), game.player_id, game.world_id, request_id)
        if old is None:
            raise HTTPException(404, "动作回执不存在")
        return {"response": old[0], "http_status": old[1]}

    @router.get("/{world_id}/ai/status")
    def game_ai_status(game: GameState = Depends(get_game)):
        return {"model_available": game_ai_model is not None,
                "budget": app.state.game_ai.status(game.player_id, game.world_id),
                "notice": "本地建议默认不请求模型；仅显式启用并提问时使用已配置模型。复盘不补发奖励。"}

    def ai_request(req, game, kind):
        with ensure_store().lock, ensure_store().sessions.begin() as db:
            save = load_owned(db, game.world_id, game.player_id)
            latest, combat = unpack(save)
            version = save.version
        if version != req.expected_version:
            raise RequestConflict("AI_STATE_CHANGED")
        if combat is None:
            raise HTTPException(409, "AI_COMBAT_NOT_STARTED")
        if (kind == "advice") != (combat.status == "active"):
            raise HTTPException(409, "AI_REVIEW_REQUIRES_FINISHED" if kind == "review" else "AI_ADVICE_REQUIRES_ACTIVE")
        packet = visible_packet(latest, combat, version, req.npc_id, req.goal, req.question, kind)
        fallback = render_selection(packet, default_selection(packet))
        identity = [type(game_ai_model).__module__, type(game_ai_model).__qualname__, getattr(game_ai_model, "model", None)]
        cache_input = [policy_fingerprint(), rules_fingerprint(), identity if req.use_model else "local", packet]
        compute = lambda on_call, deadline: model_task(packet, game_ai_model, on_call, deadline)
        result = app.state.game_ai.generate(game.player_id, game.world_id, version, "ai:" + req.request_id,
            {"kind": kind, **req.model_dump()}, cache_input, fallback, compute,
            enabled=req.use_model and game_ai_model is not None,
            slot="advice" if kind == "advice" else "terminal", reserve=2 if kind == "advice" else 1)
        return {**result, "budget": app.state.game_ai.status(game.player_id, game.world_id)}

    @router.post("/{world_id}/ai/advice")
    def game_advice(req: GameAIRequest, game: GameState = Depends(get_game)):
        return ai_request(req, game, "advice")

    @router.post("/{world_id}/ai/review")
    def game_review(req: GameAIRequest, game: GameState = Depends(get_game)):
        return ai_request(req, game, "review")

    @router.post("/{world_id}/actions/{request_id}/narration")
    def battle_narration(request_id: str, game: GameState = Depends(get_game)):
        key = (action_scope(game.player_id, game.world_id), request_id)
        with ensure_store().lock, ensure_store().sessions.begin() as db:
            receipt = db.get(ActionReceipt, key)
            if receipt is None or receipt.narration_facts is None:
                raise HTTPException(404, "没有已结算战斗场景")
            if receipt.narration is not None:
                return receipt.narration
            facts = dict(receipt.narration_facts)
        # 慢模型不持有业务事务；失败仅用模板，不再次扣材料/抽怪/结算。
        version = receipt.response["state"]["version"]
        milestone = ("opening" if facts["outcome"] == "COMBAT_STARTED" else
                     "camp:" + str(facts["next_round"]) if facts.get("phase") == "camp" else
                     "terminal" if facts["status"] != "active" and game_ai_model is None else None)
        # 普通回合仍立即显示模板；完整AI局仅开场/三营地/复盘最多5个节点。
        fallback = asdict(narrate_facts(facts))
        def compute_scene(on_call, deadline):
            if not on_call():
                return fallback
            return asdict(narrate_facts(facts, narrator))
        with ensure_store().sessions() as db:
            current_version = db.get(GameSave, game.world_id).version
        generated = app.state.game_ai.generate(game.player_id, game.world_id, version,
            "scene:" + digest(request_id), {"action_request": request_id},
            [policy_fingerprint(), "scene", facts, getattr(narrator, "model", None), rules_fingerprint()],
            fallback, compute_scene, enabled=narrator is not None and milestone is not None and current_version == version,
            slot=milestone or "template", reserve=1, check_current=False)
        scene = generated["content"] if generated["content"] is not None else fallback
        if generated["status"] == "running":
            return fallback  # 暂时模板不抢先写进正式叙事回执，等待原生成者提交。
        with ensure_store().lock, ensure_store().sessions.begin() as db:
            receipt = db.get(ActionReceipt, key)
            if receipt.narration is None:
                receipt.narration = scene
            return receipt.narration

    def execute_chat(req: ChatRequest, game: GameState, trace: RunTrace, job: dict) -> ChatResponse:
        memory = ChatMemory(ensure_store(), cache)
        scope = trace.scope
        persona = load_guard_skill(req.npc_id)
        skill_fingerprint = persona.fingerprint if persona else None
        trace.summary.update(npc_id=req.npc_id, skill_fingerprint=skill_fingerprint)
        factual = is_fact_question(req.message) or (persona is not None and beginner_question(req.message))
        try:
            if job["context"] is None:
                with trace.span("memory"):
                    history = memory.load_recent(scope)
                retrieved = RagResult("disabled")
                remembered = MemoryResult("skipped_facts" if factual else "skipped_irrelevant")
                shared = KnowledgeResult("disabled_legacy_guard")
                if persona is not None:
                    with trace.span("public_knowledge"):
                        shared = app.state.shared_knowledge.retrieve(req.message, req.npc_id)
                if not factual and is_preference_question(req.message):
                    with trace.span("long_memory"):
                        remembered = app.state.long_memory.retrieve(req.message, game)
                if rag is not None:
                    with trace.span("rag"):
                        retrieved = (RagResult("skipped_facts") if factual
                                     else rag.retrieve(req.message, game.world_id))
                app.state.recovery.save_context(trace.run_id, {
                    "history": history, "rag": asdict(retrieved), "memories": asdict(remembered),
                    "shared": asdict(shared),
                    "npc_id": req.npc_id, "skill_fingerprint": skill_fingerprint})
            else:
                # 恢复使用原上下文，不把不同检索结果混进同一个中断图。
                context = job["context"]
                if (context.get("npc_id", "guard") != req.npc_id or
                        context.get("skill_fingerprint") != skill_fingerprint):
                    raise RequestConflict("CHECKPOINT_SKILL_CHANGED")
                history = context["history"]
                retrieved, remembered = RagResult(**context["rag"]), MemoryResult(**context["memories"])
                shared = KnowledgeResult(**context.get("shared", {"status": "disabled"}))
                with ensure_store().sessions.begin() as db:
                    if db.get(GameSave, game.world_id).version != job["game_version"]:
                        raise RequestConflict("CHECKPOINT_STATE_CHANGED")
                if app.state.long_memory.refresh(remembered.hits, game) != remembered.hits:
                    raise RequestConflict("CHECKPOINT_MEMORY_CHANGED")
                if app.state.shared_knowledge.refresh(shared.hits, req.npc_id) != shared.hits:
                    raise RequestConflict("CHECKPOINT_KNOWLEDGE_CHANGED")
                trace.summary["resumed"] = True
            with trace.span("agent"):
                result = run_graph_turn(game, req.message, model, history=history["turns"],
                    game_facts={"has_permit": game.has_permit, "inventory": game.inventory,
                                "quest_stage": game.quest_stage}, lore=retrieved.hits + shared.hits, memories=remembered.hits,
                    checkpointer=app.state.recovery.checkpointer, thread_id=trace.run_id, persona=persona)
            used_ids = set(result["context_meta"].get("kept_source_ids", []))
            used_lore = [row for row in retrieved.hits if row["id"] in used_ids]
            used_shared = [row for row in shared.hits if row["id"] in used_ids]
            memory_ids = set(result["context_meta"].get("kept_memory_ids", []))
            used_memories = [row for row in remembered.hits if row["id"] in memory_ids]
            # turn_result已过滤文案；刷新快照后再校验，避免直接信模型执行前的旧状态。
            with trace.span("reply_guard"):
                with ensure_store().lock, ensure_store().sessions.begin() as db:
                    latest, _ = unpack(load_owned(db, game.world_id, game.player_id))
                used_memories = app.state.long_memory.refresh(used_memories, latest)
                used_shared = app.state.shared_knowledge.refresh(used_shared, req.npc_id)
                checked = check_reply(latest, req.message, result["reply"], lore=used_lore,
                                      memories=used_memories, persona=persona, public_faq=used_shared)
            saved = {"cache_status": "deferred" if cache else "disabled"}
            degraded = (result["stop_reason"] != "FINISHED" or
                        retrieved.status in {"embedding_error", "index_error"} or
                        remembered.status in {"embedding_error", "index_error"})
            degraded = degraded or "error" in shared.status
            trace.summary.update(status="degraded" if degraded else "completed",
                                 stop_reason=result["stop_reason"], model_calls=result["model_calls"],
                                 tool_calls=result["tool_calls"], reply_policy=checked.policy,
                                 memory_source=history["source"], cache_status=saved["cache_status"],
                                 node_trace=result["node_trace"], rag_status=retrieved.status,
                                 rag_error_code=retrieved.error_code, context_meta=result["context_meta"],
                                 source_ids=[row["id"] for row in used_lore],
                                 long_memory_status=remembered.status,
                                 long_memory_ids=[row["id"] for row in used_memories])
            trace.summary.update(public_knowledge_status=shared.status,
                public_knowledge_sources=[{"id": row["id"], "version": row["version"],
                    "rules_version": row["rules_version"]} for row in used_shared])
        except Exception:
            trace.summary.update(status="failed", stop_reason="CHAT_FAILED")
            trace.save(ensure_store())
            raise
        response = ChatResponse(
            reply=checked.reply, rule_reply=guard_reply(latest, req.message),
            stop_reason=result["stop_reason"], model_calls=result["model_calls"],
            tool_calls=result["tool_calls"], node_trace=result["node_trace"],
            memory_source=history["source"], history_turns=len(history["turns"]),
            cache_status=saved["cache_status"], run_id=trace.run_id,
            reply_policy=checked.policy, trace_saved=False,
            rag_status=retrieved.status, context_meta=result["context_meta"],
            long_memory_status=remembered.status, long_memory_ids=[row["id"] for row in used_memories],
            sources=[{"id": row["id"], "source": row["source"], "score": row["score"]} for row in used_lore],
            npc_id=req.npc_id, npc_name=persona.display_name if persona else "守卫",
            skill_fingerprint=skill_fingerprint,
            public_knowledge_status=shared.status,
            public_knowledge_sources=[{"id": row["id"], "version": row["version"],
                "rules_version": row["rules_version"]} for row in used_shared],
        )
        try:
            with trace.span("persist"):
                app.state.recovery.finish(trace.run_id, scope, response.model_dump(), cache)
        except Exception:
            trace.summary.update(status="failed", stop_reason="CHAT_FAILED")
            trace.save(ensure_store())
            raise
        response.trace_saved = trace.save(ensure_store())
        # 已提交的回复是恢复依据；Trace/缓存失败不得再次生成或追加聊天。
        try:
            memory.load_recent(scope)
            app.state.recovery.save_response_metadata(trace.run_id, response.model_dump())
        except Exception:
            pass
        return response

    def safe_chat_receipt(payload, game):
        response = ChatResponse(**payload)
        sources = response.public_knowledge_sources
        if sources and len(app.state.shared_knowledge.refresh(sources, response.npc_id)) != len(sources):
            # 回执保留原运行事实，但不能把已撤回FAQ伪装成新的有效回答。
            persona = load_guard_skill(response.npc_id)
            response.reply = check_reply(game, "许可", "", persona=persona).reply + "\n历史公共提示已撤回、过期或变更，本次不再展示。"
            response.rule_reply = guard_reply(game, "")
            response.reply_policy = "historical_public_faq_hidden"
            response.public_knowledge_status = "receipt_knowledge_changed"
            response.public_knowledge_sources = []
        return response

    def durable_chat(req, game, trace=None):
        recovery = app.state.recovery
        with recovery.execution_lock():
            # 文件锁取得后刷新可信快照，避免等待期间读到旧游戏事实。
            with ensure_store().lock, ensure_store().sessions.begin() as db:
                game, _ = unpack(load_owned(db, game.world_id, game.player_id))
            job = recovery.prepare(game, req.model_dump())
            if job["response"] is not None:
                return safe_chat_receipt(job["response"], game)
            trace = trace or RunTrace(ChatScope(game.player_id, game.world_id, req.session_id, req.npc_id))
            trace.run_id = job["run_id"]
            try:
                return execute_chat(req, game, trace, job)
            except Exception:
                recovery.mark_interrupted(trace.run_id)
                raise

    # 同步模型在def入口/同步流迭代器的线程池内执行，不直接阻塞事件循环。
    @router.post("/{world_id}/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, game: GameState = Depends(get_game)):
        return durable_chat(req, game)

    @router.post("/{world_id}/chat/stream")
    def chat_stream(req: ChatRequest, game: GameState = Depends(get_game)):
        # Depends在返回StreamingResponse前完成鉴权，不能先发200再拒绝越权。
        trace = RunTrace(ChatScope(game.player_id, game.world_id, req.session_id, req.npc_id))
        with app.state.recovery.execution_lock():
            trace.run_id = app.state.recovery.prepare(game, req.model_dump())["run_id"]

        def events():
            # request_id在首帧给出；run_id以持久化任务为准，不使用临时Trace ID。
            yield sse_frame("start", {"run_id": trace.run_id, "request_id": req.request_id})
            try:
                result = durable_chat(req, game, trace)
            except Exception as error:
                code = error.code if isinstance(error, RequestConflict) else "CHAT_FAILED"
                yield sse_frame("error", {"run_id": trace.run_id, "request_id": req.request_id, "code": code})
                return  # 不输出未经校验的模型内容，不发送成功done。
            yield sse_frame("result", result.model_dump())
            yield sse_frame("done", {"run_id": result.run_id})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/{world_id}/runs/{run_id}")
    def read_run(run_id: str, game: GameState = Depends(get_game)):
        result = load_run(ensure_store(), run_id, game.player_id, game.world_id)
        if result is None:
            raise HTTPException(404, "运行记录不存在")
        return result

    @router.get("/{world_id}/chat/runs/{run_id}")
    def chat_result(run_id: str, game: GameState = Depends(get_game)):
        result = app.state.recovery.read(game, run_id)
        if result is None:
            raise HTTPException(404, "聊天任务不存在")
        if result["response"] is not None:
            result["response"] = safe_chat_receipt(result["response"], game).model_dump()
        return {key: value for key, value in result.items() if key != "request"}

    @router.post("/{world_id}/chat/runs/{run_id}/resume", response_model=ChatResponse)
    def resume_chat(run_id: str, game: GameState = Depends(get_game)):
        result = app.state.recovery.read(game, run_id)
        if result is None:
            raise HTTPException(404, "聊天任务不存在")
        return durable_chat(ChatRequest(**result["request"]), game)

    @router.get("/{world_id}/chat/history")
    def chat_history(session_id: Annotated[str, Query(pattern=r"^[a-zA-Z0-9_-]{1,40}$")] = "main",
                     npc_id: Literal["guard", "venti", "varka"] = "guard",
                     game: GameState = Depends(get_game)):
        # 和chat使用相同的服务端归属校验，不能通过查询参数指定别人的player。
        return ChatMemory(ensure_store(), cache).load_recent(
            ChatScope(game.player_id, game.world_id, session_id, npc_id))

    @app.get("/api/v1/guards")
    def guards():
        return {"guards": list_guards(), "dialogue_mode": "reviewed_catalog"}

    @app.get("/", include_in_schema=False)
    def page():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    # Only bundled presentation assets are public; saves/configuration are not mounted.
    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "static" / "assets"), name="assets")

    app.include_router(router)
    return app


DEFAULT_DATABASE_URL = "sqlite+pysqlite:///" + (Path(__file__).parent / "data" / "echoworld.sqlite3").as_posix()
# 默认离线；文件存档只在启动时初始化。PG连接入口待真实联调。
app = create_app(model=DeepSeekAgentModel.from_environment()
                 if os.getenv("ECHOWORLD_NPC_MODE") == "llm" else None,
                 database_url=os.getenv("ECHOWORLD_DATABASE_URL", DEFAULT_DATABASE_URL),
                 cache=RedisRecentCache.from_environment(), rag=LoreRag.from_environment(),
                 checkpoint_path=os.getenv("ECHOWORLD_CHECKPOINT_PATH") or None,
                 environment_memory=True,
                 combat_seed=int(os.environ["ECHOWORLD_DEMO_SEED"]) if os.getenv("ECHOWORLD_DEMO_SEED") else None,
                 narrator=DeepSeekNarrator.from_environment()
                 if os.getenv("ECHOWORLD_NARRATION_MODE") == "llm" else None,
                 game_ai_model=DeepSeekAgentModel.from_environment()
                 if os.getenv("ECHOWORLD_GAME_AI_MODE") == "llm" else None)
