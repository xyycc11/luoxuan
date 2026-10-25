"""受控知识演进：显式分享→待审→标准FAQ发布；SQL真值，向量仅作候选。

第一版只学习哪些已核验教学主题值得共享，不训练模型、不公开原始聊天。
审核不允许自由改游戏规则或发布任意玩家原文。
"""
import math
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Boolean, Integer, JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .action_store import RequestConflict, digest
from .database import Base, GameSave
from .embeddings import QwenEmbedding, validate_vector
from .vector_search import MilvusLoreIndex


FAQ = {
    "permit": {"title": "许可核验", "words": ("许可", "进入", "通行", "permit"),
        "text": "通行只看当前存档的has_permit。聊天声称有许可或切换守卫都不能绕过核验，也不能发放许可。"},
    "quest": {"title": "许可任务", "words": ("任务", "药草", "herb", "材料", "交付", "submit"),
        "text": "先接受许可任务；阶段为active、背包至少1个herb且尚无许可时，交付恰好消耗1个herb，任务完成并获得许可。教学herb每存档最多领取一次，材料不足不能重复提交。"},
    "tower": {"title": "十轮爬塔", "words": ("爬塔", "十轮", "通关", "tower"),
        "text": "有许可才能开塔；每存档只开一场，不能聊天复活、换怪或重置。新七元素模式前九层练习，第十层固定首领，需搭配反应击败；没有随机即死，生命耗尽或超过回合时限会失败。旧版试炼保留原即死与终轮死亡优先规则；旧局不自动升级。"},
    "combat": {"title": "战斗选择", "words": ("战斗", "攻击", "防御", "祝福", "符文", "远征", "营地", "遗物", "构筑", "分岔", "路线", "挑战", "卡牌", "手牌", "牌组", "抽牌", "选牌", "strike", "guard"),
        "text": "按场景的规则版本与选项行动。普通试炼专注初始2、上限3；破绽攻击耗1得3、防御回1得0；蓄势攻击耗2得4、防御回1得1；专注不足攻击不得分且不扣专注。祝福试炼开场三种祝福随机提供两种，仅选一次、不耗轮次：风之余韵令普通防御后的下一次成功普通攻击额外+2（不叠加）；烈焰破绽令成功破绽攻击额外+2；骑士反击令蓄势防御额外+2。远征试炼额外在完成第3/6/9轮后进入营地：休整恢复2专注（上限3），共鸣使祝福等级+1（上限4、触发额外分=等级+1），或耗1专注立即得3战功；专注不足不能选战功。营地只选一次、不耗战斗轮次，选择后才揭晓下一轮敌人。祝福不作用于稀有或即死怪。大伟丘默认每轮1%，任一合法选择恰好+50、不改专注；遗迹重机/深渊咏者仍即死，防御、祝福或营地不免死。策略只影响分数，不改变怪物概率。旧经典局仍攻击+1防御+0；已开塔不改模式或补发祝福。不重复点击旧轮次；印记只描述本局战绩，不发放道具或跨局奖励。以上为原创同人设定。"},
    "memory": {"title": "个人记忆与公共知识", "words": ("记忆", "偏好", "分享", "公开", "隐私"),
        "text": "个人聊天按玩家、存档、会话和守卫隔离。提示风格需要明确确认；公共建议另外征求同意并审核，原始建议和私人聊天不向其他玩家公开。作者可撤回分享。"},
}
# 独立新模式，不追溯改写旧局规则；公共FAQ仍需显式人审核验后发布。
FAQ["combat"]["text"] += ("遗物构筑模式保留远征营地，休整后再三选一遗物；本局最多3件且不重复，领取不加分或推进轮次。"
                          "遗物仅按选项预览叠加普通分数/专注，不作用于大伟丘或即死怪，不提供免死，刷新不重抽。")
FAQ["combat"]["text"] += ("分岔构筑保留遗物，在第1/4/7/10轮出发前选择本章路线：风息回1专注，"
                          "试锋耗1专注，本章成功普通攻击2次额外+6；终章1次+3，只发一次。"
                          "稀有/失败攻击/即死不计进度，未达标不退款；两路抽怪和死亡概率相同，不提供免死。")
NPCS = ("venti", "varka")
FAQ["combat"]["text"] += ("战术牌组模式不叠加分岔：6张初始牌、每轮抽3选1，其余一起弃置，弃牌洗回继续抽。"
                          "营地后选遗物，再从2张新卡选1或跳过；新卡置顶，下手保证入手。"
                          "出牌消耗与加分以当前预览为准，专注不足不能打，全手不可用才可应急整备。"
                          "刷新不重抽，选牌不加分、不耗轮次，卡牌与遗物/祝福仅影响普通分数，不免死，大伟丘仍恰好50分。")
PUBLIC_WORLD = "echoworld_public"
FAQ["combat"]["words"] += ("元素", "反应", "首领", "冻结", "蒸发", "绽放")
# 分开说明两代规则，避免给七元素玩家推荐“防御也必死”的旧攻略。
FAQ["combat"]["text"] = ("七元素模式elements_v1：固定火水雷冰草风岩七张牌，每回合抽4、能量3，可连续出牌。"
    "结束回合后持续伤害先结算，未击败敌人才反击；随后弃余牌、抽4、能量回3、护盾清零，附着跨回合保留。"
    "火水蒸发+5、火冰融化+6、火雷超载+5并削弱2、水雷感电+3且回合末2伤、水冰冻结+2并削弱4、"
    "冰雷超导+3且下张+2、草雷激化+4且下张+3、草火燃烧+2且回合末3伤、草水绽放+1并生草原核（最多2）。"
    "核接火/雷额外8伤消耗1核；风扩散火水雷冰+3返1能量，岩结晶+1且护盾+4，二者保留附着。其他组合无反应。"
    "前九层无即死，第十层固定90生命/20屏障首领，反应额外削屏障4，破盾削弱下次攻击4。敌方攻击提前公开。"
    "旅人44生命；普通层8回合、首领12回合内击败，生命耗尽或超时失败。第4/7/10层前恢复12生命或七张牌永久伤害+1，二选一。"
    "大伟丘仅前九层各1%，击败额外50分一次。只按实际生命伤害计普通分，不重复计入削盾/治疗。"
    "旧模式仍按各自专注/祝福/营地/遗物/分岔/抽3选1规则，旧遗迹重机与深渊咏者仍即死。"
    "已开塔不改规则、不重置存档；聊天与顾问只读，不出牌、不发许可或复活。数值为原创同人简化，不是官方伤害公式。")
CONSENT_VERSION = "canonical-faq-only-v1"
FAQ["combat"]["text"] += ("七元素第二版edition=2：每回合可免费留1张当前手牌到下回合，再抽至4；"
    "若打出留牌则不保留，但次数仍已用，不能带到下一层。旧缺edition局按初版不留牌。"
    "普通怪各有可见攻击节奏；木盾暴徒开场6屏障，火史莱姆开场附着火（无官方免疫），遗迹守卫第三拍重击。"
    "界面预告当前敌人后续两次原始攻击，不显示未来牌序或下层怪物。留牌会改变后续抽取，不保证更优。")
FAQ["combat"]["text"] += ("新开第三版edition=3：每回合机变二选一，留1张或弃1张换4盾，不耗能量、不触发反应；两者共用一次机会。"
    "营地可放弃本次回血/加伤，学本局唯一流派且不能更换：首次反应额外3伤、获得3盾或返1能量（上限3），每回合限一次。"
    "刷新保留机变和流派触发次数，换回合才刷新；旧局不自动升级。")
FAQ["combat"]["text"] += ("第五版edition=5新增营地元素精炼：第4/7/10层前，从本营地固定3个未精炼候选中选1，"
    "与回血、全牌加伤、学习流派互斥。每张牌最多精炼一次，本局永久保留、不增加牌数、不刷新重抽。"
    "火牌基础+4伤；水牌治疗2变5；雷牌反应额外+4伤；冰牌护盾2变6；草牌反应额外4盾；"
    "风牌扩散额外+4伤；岩牌自身护盾5变9。费用、附着、反应与隐藏关时限不变；旧版不升级。")


def rules_fingerprint():
    # 规则变更后旧FAQ自动不可用，需重新审核；不只依赖开发者手改版本号。
    root = Path(__file__).parent
    files = {name: (root / name).read_text(encoding="utf-8") for name in
             ("rules.py", "combat.py", "blessings.py", "expedition.py", "relics.py", "routes.py", "cards.py", "elements.py", "game_actions.py", "state.py", "guard_skills.py")}
    return digest({"files": files, "faq": FAQ, "consent": CONSENT_VERSION})


class KnowledgeCandidate(Base):
    __tablename__ = "knowledge_candidates"
    __table_args__ = (UniqueConstraint("player_id", "world_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(80), index=True)
    world_id: Mapped[str] = mapped_column(String(80))
    npc_id: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(80))
    payload_hash: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(20))
    suggestion: Mapped[str] = mapped_column(String(600))
    consent_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[int] = mapped_column(Integer)


class PublicKnowledge(Base):
    __tablename__ = "public_guard_knowledge"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    topic: Mapped[str] = mapped_column(String(20), unique=True)
    candidate_id: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean)
    text: Mapped[str] = mapped_column(String(1000))
    rules_version: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(Integer)
    npc_ids: Mapped[list] = mapped_column(JSON)


class KnowledgeAudit(Base):
    __tablename__ = "knowledge_audits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidate_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(80))
    note: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class KnowledgeFeedback(Base):
    __tablename__ = "knowledge_feedback"
    __table_args__ = (UniqueConstraint("player_id", "world_id", "entry_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    player_id: Mapped[str] = mapped_column(String(80))
    world_id: Mapped[str] = mapped_column(String(80))
    entry_id: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    helpful: Mapped[bool] = mapped_column(Boolean)


@dataclass
class KnowledgeResult:
    status: str
    hits: list[dict] = field(default_factory=list)


class MilvusPublicKnowledgeIndex(MilvusLoreIndex):
    def __init__(self, uri, dimension, token=""):
        super().__init__(uri, dimension, collection="echoworld_lore_shared_faq_v1", token=token)

    def search(self, query, embedding_key, k):
        import json
        validate_vector(query, self.dimension)
        result = self.client().search(collection_name=self.collection, data=[query], limit=k,
            filter=f"world_id == {json.dumps(PUBLIC_WORLD)} and embedding_key == {json.dumps(embedding_key)}",
            output_fields=["version"], search_params={"metric_type": "COSINE"},
            consistency_level="Strong", timeout=3)
        return [{"id": hit["id"], "version": hit["entity"]["version"], "score": hit["distance"]}
                for hit in (result[0] if result else [])]


class SharedKnowledge:
    def __init__(self, store, embedder=None, index=None, *, clock=time.time):
        self.store, self.embedder, self.index, self.clock = store, embedder, index, clock

    @contextmanager
    def writing(self):
        # 本地审核CLI和Web进程可同时写同一SQLite，提前取写锁。
        with self.store.lock, self.store.sessions.begin() as db:
            if self.store.engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db

    def owned(self, db, game):
        row = db.get(GameSave, game.world_id)
        if row is None or row.player_id != game.player_id:
            raise RequestConflict("SAVE_NOT_OWNED", 403)

    def valid(self, row, candidate, npc_id, fingerprint):
        return bool(row and candidate and candidate.status == "approved" and
            candidate.consent_version == CONSENT_VERSION and row.active and
            row.expires_at > int(self.clock()) and npc_id in row.npc_ids and npc_id in NPCS and
            row.topic in FAQ and row.rules_version == fingerprint and
            row.text == FAQ[row.topic]["text"] and candidate.topic == row.topic)

    @staticmethod
    def view(row, score=1.0):
        return {"id": row.id, "version": row.version, "topic": row.topic,
                "source": "reviewed_public_faq:" + row.topic, "text": row.text,
                "rules_version": row.rules_version, "score": score}

    def propose(self, game, npc_id, topic, suggestion, *, consent, request_id):
        if consent is not True:
            raise RequestConflict("EXPLICIT_CONSENT_REQUIRED", 422)
        if (npc_id not in NPCS or topic not in FAQ or not isinstance(suggestion, str) or
            not 1 <= len(suggestion.strip()) <= 600 or not isinstance(request_id, str) or
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id)):
            raise RequestConflict("INVALID_KNOWLEDGE_PROPOSAL", 422)
        suggestion = suggestion.strip()
        # 辅助拒绝常见敏感内容/注入；不是完整隐私检测。最终公共文本从不取此字段。
        forbidden = (r"sk-[A-Za-z0-9]+", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"\d{7,}",
                     r"忽略.{0,8}(规则|指令)", r"system\s*prompt", r"系统提示", r"密码|密钥|身份证|手机号")
        if any(re.search(pattern, suggestion, re.I) for pattern in forbidden):
            raise RequestConflict("UNSAFE_OR_PERSONAL_CONTENT", 422)
        fingerprint = digest([npc_id, topic, suggestion, CONSENT_VERSION])
        with self.writing() as db:
            self.owned(db, game)
            old = db.scalar(select(KnowledgeCandidate).where(
                KnowledgeCandidate.player_id == game.player_id, KnowledgeCandidate.world_id == game.world_id,
                KnowledgeCandidate.request_id == request_id))
            if old:
                if old.payload_hash != fingerprint:
                    raise RequestConflict("REQUEST_ID_CONFLICT")
                return {"id": old.id, "status": old.status, "topic": old.topic}
            pending = list(db.scalars(select(KnowledgeCandidate.id).where(
                KnowledgeCandidate.player_id == game.player_id, KnowledgeCandidate.status == "pending").limit(20)))
            if len(pending) >= 20:
                raise RequestConflict("PENDING_LIMIT", 429)
            row = KnowledgeCandidate(id=uuid.uuid4().hex, player_id=game.player_id, world_id=game.world_id,
                npc_id=npc_id, request_id=request_id, payload_hash=fingerprint, topic=topic,
                suggestion=suggestion, consent_version=CONSENT_VERSION, status="pending", created_at=int(self.clock()))
            db.add(row)
            return {"id": row.id, "status": row.status, "topic": row.topic}

    def candidates(self, game=None):
        with self.store.lock, self.store.sessions.begin() as db:
            stmt = select(KnowledgeCandidate).order_by(KnowledgeCandidate.created_at.desc(), KnowledgeCandidate.id).limit(100)
            if game is not None:
                self.owned(db, game)
                stmt = stmt.where(KnowledgeCandidate.player_id == game.player_id, KnowledgeCandidate.world_id == game.world_id)
            rows = list(db.scalars(stmt))
            # 原文只由本人或受信本地审核工具读取，聊天/公共列表不调用此方法。
            return [{"id": row.id, "topic": row.topic, "npc_id": row.npc_id,
                     "suggestion": row.suggestion, "status": row.status} for row in rows]

    def audit(self, db, row, candidate_id, action, actor, note):
        snapshot = None if row is None else {"topic": row.topic, "text": row.text,
            "candidate_id": row.candidate_id, "rules_version": row.rules_version,
            "expires_at": row.expires_at, "npc_ids": row.npc_ids, "active": row.active}
        db.add(KnowledgeAudit(entry_id=row.id if row else None,
            candidate_id=candidate_id, action=action, version=row.version if row else 0,
            actor=actor, note=note, snapshot=snapshot, created_at=int(self.clock())))

    def review(self, candidate_id, *, approve, actor, note, ttl_seconds=2592000):
        if type(approve) is not bool or not isinstance(actor, str) or not actor.strip() or len(actor) > 80:
            raise ValueError("审核者不合法")
        if not isinstance(note, str) or not note.strip() or len(note) > 300:
            raise ValueError("需要简短核验说明")
        if type(ttl_seconds) is not int or not 600 <= ttl_seconds <= 2592000:
            raise ValueError("有效期范围600～2592000秒")
        fingerprint = rules_fingerprint()
        with self.writing() as db:
            candidate = db.get(KnowledgeCandidate, candidate_id)
            if candidate is None:
                raise RequestConflict("CANDIDATE_NOT_FOUND", 404)
            if candidate.status == "withdrawn":
                raise RequestConflict("CONSENT_WITHDRAWN")
            if candidate.status not in ("pending", "approved"):
                raise RequestConflict("CANDIDATE_NOT_PENDING")
            if not approve:
                if candidate.status != "pending":
                    raise RequestConflict("USE_DISABLE_FOR_PUBLISHED_ENTRY")
                candidate.status = "rejected"
                self.audit(db, None, candidate.id, "reject", actor, note)
                return {"id": candidate.id, "status": "rejected"}
            if candidate.consent_version != CONSENT_VERSION or candidate.topic not in FAQ:
                raise RequestConflict("INVALID_CONSENT_OR_TOPIC")
            entry_id = "faq_" + candidate.topic
            row = db.get(PublicKnowledge, entry_id)
            version = 1 if row is None else row.version + 1
            if row is None:
                row = PublicKnowledge(id=entry_id, topic=candidate.topic)
                db.add(row)
            # 不发布玩家原文；人审选择经过规则测试的标准FAQ，来源/授权仍可追溯。
            row.candidate_id, row.version, row.active = candidate.id, version, True
            row.text, row.rules_version = FAQ[candidate.topic]["text"], fingerprint
            row.expires_at, row.npc_ids = int(self.clock()) + ttl_seconds, list(NPCS)
            candidate.status = "approved"
            self.audit(db, row, candidate.id, "publish", actor, note)
            return {"id": row.id, "version": row.version, "status": "published", "index_status": "pending_explicit_ingest"}

    def withdraw(self, game, candidate_id):
        with self.writing() as db:
            self.owned(db, game)
            candidate = db.get(KnowledgeCandidate, candidate_id)
            if candidate is None or (candidate.player_id, candidate.world_id) != (game.player_id, game.world_id):
                raise RequestConflict("CANDIDATE_NOT_FOUND", 404)
            if candidate.status == "withdrawn":
                return {"ok": True}
            candidate.status, candidate.suggestion = "withdrawn", ""  # 清除原建议，保留最小授权墓碑。
            row = db.get(PublicKnowledge, "faq_" + candidate.topic)
            if row is not None and row.candidate_id == candidate.id:
                row.active, row.version = False, row.version + 1
                self.audit(db, row, candidate.id, "withdraw", "contributor", "用户撤回授权；清除原建议")
            return {"ok": True}

    def disable(self, entry_id, *, actor, note):
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 80 or not isinstance(note, str) or not note.strip() or len(note) > 300:
            raise ValueError("需提供审核者和说明")
        with self.writing() as db:
            row = db.get(PublicKnowledge, entry_id)
            if row is None:
                raise RequestConflict("KNOWLEDGE_NOT_FOUND", 404)
            row.active, row.version = False, row.version + 1
            self.audit(db, row, row.candidate_id, "disable", actor, note)
            return {"id": row.id, "version": row.version, "active": False}

    def rollback(self, entry_id, version, *, actor, note):
        """回滚生成新版本，不能复活撤回授权、旧规则或过期来源。"""
        if type(version) is not int or not isinstance(actor, str) or not actor.strip() or len(actor) > 80 or not isinstance(note, str) or not note.strip() or len(note) > 300:
            raise ValueError("回滚参数不合法")
        fingerprint = rules_fingerprint()
        with self.writing() as db:
            row = db.get(PublicKnowledge, entry_id)
            old = db.scalar(select(KnowledgeAudit).where(KnowledgeAudit.entry_id == entry_id,
                KnowledgeAudit.version == version, KnowledgeAudit.action.in_(["publish", "rollback"])))
            if row is None or old is None or not old.snapshot:
                raise RequestConflict("REVISION_NOT_FOUND", 404)
            snapshot = old.snapshot
            candidate = db.get(KnowledgeCandidate, snapshot["candidate_id"])
            if (candidate is None or candidate.status != "approved" or candidate.consent_version != CONSENT_VERSION or
                snapshot["topic"] not in FAQ or snapshot["text"] != FAQ[snapshot["topic"]]["text"] or
                snapshot["rules_version"] != fingerprint or snapshot["expires_at"] <= int(self.clock())):
                raise RequestConflict("REVISION_NO_LONGER_VALID")
            for key, value in snapshot.items():
                setattr(row, key, value)
            row.version += 1
            self.audit(db, row, row.candidate_id, "rollback", actor, note)
            return {"id": row.id, "version": row.version, "active": row.active}

    def refresh(self, hits, npc_id):
        fingerprint = rules_fingerprint()
        with self.store.lock, self.store.sessions.begin() as db:
            result, seen = [], set()
            for hit in hits:
                row = db.get(PublicKnowledge, hit.get("id"))
                candidate = db.get(KnowledgeCandidate, row.candidate_id) if row else None
                score = hit.get("score", 1.0)
                if (self.valid(row, candidate, npc_id, fingerprint) and type(hit.get("version")) is int and row.version == hit.get("version") and
                    row.id not in seen and type(score) in (int, float) and math.isfinite(score) and .4 <= score <= 1.00001):
                    result.append(self.view(row, score))
                    seen.add(row.id)
            return result

    def retrieve(self, message, npc_id, *, k=3):
        if type(k) is not int or not 1 <= k <= 5:
            raise ValueError("k范围1～5")
        if npc_id not in NPCS:
            return KnowledgeResult("disabled_legacy_guard")
        wanted = [topic for topic, data in FAQ.items() if any(word in message.lower() for word in data["words"])]
        if any(word in message for word in ("大家有什么经验", "学到了什么", "公共攻略", "共享知识")):
            wanted = list(FAQ)
        status = "sql_keyword"
        if not wanted and self.embedder is not None and self.index is not None:
            try:
                vector = self.embedder.embed([message])[0]
                validate_vector(vector, self.embedder.dimension)
                hits = self.index.search(vector, self.embedder.embedding_key, k)
                return KnowledgeResult("vector", self.refresh(hits, npc_id)[:k])
            except Exception:
                status = "vector_error_sql_fallback"
        fingerprint = rules_fingerprint()
        with self.store.lock, self.store.sessions.begin() as db:
            rows = list(db.scalars(select(PublicKnowledge).where(PublicKnowledge.topic.in_(wanted)).order_by(PublicKnowledge.id)))
            hits = [self.view(row) for row in rows if self.valid(row,
                    db.get(KnowledgeCandidate, row.candidate_id), npc_id, fingerprint)]
        return KnowledgeResult(status if hits else (status if "error" in status else "no_match"), hits[:k])

    def feedback(self, game, entry_id, version, helpful):
        if type(version) is not int or type(helpful) is not bool:
            raise ValueError("反馈参数不合法")
        with self.writing() as db:
            self.owned(db, game)
            row = db.get(PublicKnowledge, entry_id)
            candidate = db.get(KnowledgeCandidate, row.candidate_id) if row else None
            if not self.valid(row, candidate, "venti", rules_fingerprint()) or row.version != version:
                raise RequestConflict("KNOWLEDGE_VERSION_CHANGED")
            old = db.scalar(select(KnowledgeFeedback).where(KnowledgeFeedback.player_id == game.player_id,
                KnowledgeFeedback.world_id == game.world_id, KnowledgeFeedback.entry_id == entry_id, KnowledgeFeedback.version == version))
            if old is None:
                old = KnowledgeFeedback(id=uuid.uuid4().hex, player_id=game.player_id, world_id=game.world_id,
                                        entry_id=entry_id, version=version)
                db.add(old)
            old.helpful = helpful
        return {"ok": True}  # 反馈不自动批准知识，也不作为客观质量结论。

    def report(self):
        with self.store.lock, self.store.sessions.begin() as db:
            candidates = list(db.scalars(select(KnowledgeCandidate)))
            votes = list(db.scalars(select(KnowledgeFeedback)))
            audits = list(db.scalars(select(KnowledgeAudit).order_by(KnowledgeAudit.created_at, KnowledgeAudit.id)))
        return {"candidate_counts": {status: sum(row.status == status for row in candidates)
                    for status in ("pending", "approved", "rejected", "withdrawn")},
                "feedback": {"helpful": sum(row.helpful for row in votes), "not_helpful": sum(not row.helpful for row in votes)},
                "audit": [{"entry_id": row.entry_id, "candidate_id": row.candidate_id,
                           "action": row.action, "version": row.version, "actor": row.actor, "note": row.note} for row in audits],
                "meaning": "计数和发布审计；不等于模型质量提升或真实用户满意度评测"}

    def ingest(self):
        if self.embedder is None or self.index is None:
            raise ValueError("公共FAQ向量服务未配置")
        hits = self.retrieve("共享知识", "venti", k=5).hits
        vectors = self.embedder.embed([row["text"] for row in hits]) if hits else []
        if len(vectors) != len(hits):
            raise ValueError("向量批次长度不匹配")
        records = [{"id": row["id"], "version": row["version"], "world_id": PUBLIC_WORLD,
                    "embedding_key": self.embedder.embedding_key,
                    "vector": validate_vector(vector, self.embedder.dimension)} for row, vector in zip(hits, vectors)]
        if records:
            self.index.upsert(records)
        return len(records)  # 延迟到达的旧向量会被SQL版本/授权回查拦截。

    @classmethod
    def from_environment(cls, store):
        uri = os.getenv("ECHOWORLD_MILVUS_URI")
        embedder = QwenEmbedding.from_environment() if uri else None
        return cls(store, embedder, MilvusPublicKnowledgeIndex(uri, embedder.dimension,
            token=os.getenv("ECHOWORLD_MILVUS_TOKEN", "")) if embedder else None)

    def close(self):
        if self.index is not None:
            self.index.close()
