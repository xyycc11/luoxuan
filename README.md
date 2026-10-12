# EchoWorld v1.2 · 提瓦特双守卫与文字爬塔

一个可本地运行的原神主题游戏 NPC Agent 学习项目：通过许可任务进入蒙德附近的风之试炼塔，与温迪、法尔伽两位守卫聊天。当前版本 **v1.2**（2026-10-12），版本标记可通过`echoworld.__version__`查看。

**核心原则：模型参与表达与只读工具选择，代码决定许可、背包、任务、战斗和存档。**

当前是本机教学原型，不是商业游戏或生产级 Agent 平台。固定演示令牌不能用于真实账号认证，服务只监听本机。

## 能力与边界

- **双守卫 Skill**：选择温迪或法尔伽，提供不同口吻的有限闲聊、原创小诗、鼓励及依据真实任务阶段的新手指引。
- **受控 Agent 图**：LangGraph 编排 model → tools → model，仅允许查询许可、读取背包，设有调用预算与重复调用检测。
- **权威游戏状态**：显式动作推进许可任务与爬塔，SQL 保存进度；版本 CAS、同事务动作回执与幂等重放防止重复结算。
- **聊天记忆**：SQL 保存完整轮次，Redis 可选缓存最近几轮；按玩家、世界、会话、NPC 隔离。
- **恢复与可观测性**：SQLite 图 checkpoint、聊天结果回执、上下文/记忆/角色版本检查、脱敏 Trace、事件式 SSE 与网页未确认请求恢复。
- **可选模型与 RAG**：DeepSeek 用于只读决策和战斗叙事，千问用于向量化，Milvus 服务器适配用于检索；结果须做来源或 SQL 有效性回查。外部真实服务整链仍待配置与验收。
- **私有偏好**：明确同意后保存有限风格偏好，支持到期、撤回与版本核验；不会自动采集聊天并公开给所有玩家。

闲聊第一版采用“模型选择有限 intent/variant，代码展示已审定话术”，不是无限自由生成的角色聊天。已增加受控公共知识演进，但不是自动训练或开放式自我改写；Sandbox和生产多机调度未实现。

## v1.2新增：策略计分与可选祝福

新开普通试炼使用 `tactics_v1`：专注初始2/上限3，破绽攻击耗1得3分，蓄势攻击耗2得4分；防御回1专注，蓄势防御得1分。动作预览与结算共用规则，专注不足不能攻击。

可选 `blessings_v1`：三种原创同人祝福随机给两种，开局只选一次。风之余韵让防御后下一次成功普通攻击额外+2；烈焰破绽让破绽攻击额外+2；骑士反击让蓄势防御额外+2。网页/CLI同步展示权威计分与提示，模型不决定数值。

旧局保留classic及原有存档；怪物概率、十轮、两种即死怪与大伟丘恰好+50不变。策略改变得分，不提升固定规则的通关概率。已发布旧公共FAQ可能因规则指纹变化失效，须重新审核；真实向量资料需要显式重新导入。

## 原神主题与稀有奖励（classic旧局基准）

世界背景为提瓦特·蒙德，**风之试炼塔、许可任务、计分与即死判定为原创同人玩法，非原神官方内容**；没有上传官方游戏图片、音乐或其他游戏资源。

| 怪物 | 每轮抽取权重/概率 | 回合结算 |
| --- | --- | --- |
| 丘丘人 | 30/100 | 攻击+1，防御+0 |
| 木盾丘丘暴徒 | 20/100 | 攻击+1，防御+0 |
| 火史莱姆 | 15/100 | 攻击+1，防御+0 |
| 风丘丘萨满 | 10/100 | 攻击+1，防御+0 |
| 遗迹守卫 | 8/100 | 攻击+1，防御+0 |
| **大伟丘** | **1/100（1%）** | **不致死，完成任一合法选择恰好+50分** |
| 遗迹重机 | 8/100 | 本项目设为致死，攻击/防御均结束爬塔 |
| 深渊咏者 | 8/100 | 本项目设为致死，攻击/防御均结束爬塔 |

大伟丘刷出时不提前发奖，攻击不叠加普通1分，重复请求和重复叙事不重复领奖。十轮未死通关，固定seed便于复现，1%不保证每100轮必遇一次。旧开塔存档保留原怪物池和已有分数，不自动换怪或补发奖励。

## v1新增：双守卫受控知识演进

显式分享并同意公开 → 私人待审 → 受信本地人工核验 → 版本化标准FAQ → 两守卫引用 → 反馈/撤回/下线/回滚。

- 第一版只有许可、任务、爬塔、战斗、记忆5类规则核验后的标准FAQ；不会公开原始建议、私人聊天或作者身份。
- 已知主题可离线SQL检索，公共Milvus索引为可选显式导入，独立于私人记忆集合；候选需经SQL有效性、版本、授权、TTL及规则指纹回查。
- 审核仅通过本地`evolution_admin`，不开放玩家或模型审批权限；下线、撤回、回滚都有版本/审计。不是生产RBAC或完整隐私合规系统。
- 规则变更会使旧FAQ失效，必须重新核验。修改世界观后如已导入真实Milvus，应显式重导入；不会自动消耗Embedding额度。

网页提交建议后，将`<候选ID>`替换为真实值，并指向与服务相同的现有存档：

```powershell
./.venv/Scripts/python.exe -m echoworld.evolution_admin --database ./echoworld/data/echoworld.sqlite3 list
./.venv/Scripts/python.exe -m echoworld.evolution_admin --database ./echoworld/data/echoworld.sqlite3 approve <候选ID> --reviewer local-admin --note "已对照当前规则核验标准FAQ"
./.venv/Scripts/python.exe -m echoworld.evolution_admin --database ./echoworld/data/echoworld.sqlite3 report
```

玩家可撤回自己的建议；管理员也可`reject`、`disable`、`rollback`。共享检索有效性撤销不等于能够抹除已经送达别人的历史聊天。反馈不能代替事实核验，不宣称已证明满意度提升。可选`ingest`会调用外部Embedding并写专用集合，需自行确认配置与费用。

## 快速运行

建议使用 Python 3.12 或更新版本。先克隆仓库并在**仓库根目录**运行：

```bash
git clone https://github.com/xyycc11/luoxuan.git
cd luoxuan
python -m venv .venv
```

Windows PowerShell：

```powershell
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m echoworld.server --offline --demo-seed 2
```

macOS / Linux：

```bash
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m echoworld.server --offline --demo-seed 2
```

打开 `http://127.0.0.1:8019/`。端口占用时增加 `--port 8020`。

演示流程：Alice → 接受任务 → 交付 herb → 进入爬塔 → 每轮选择攻击/防御；Bob 需要先接受任务、领取一次教学 herb，再交付。`--demo-seed 2`用于稳定通关演示，不代表所有随机局都能通关。

聊天区可选择**温迪 / 法尔伽**，尝试“唱首诗”“鼓励我”“我是新手，下一步怎么做”。玩家说“我有许可”或要求忽略规则，不会改变许可。

存档位于 `echoworld/data/`，重启服务保留进度；每份存档只能开一次爬塔。想重新体验可使用新的数据库，而不是删除旧存档：

```powershell
./.venv/Scripts/python.exe -m echoworld.server --offline --demo-seed 2 --port 8020 --database ./echoworld/data/another-demo.sqlite3
```

### 命令行演示

```powershell
./.venv/Scripts/python.exe -m echoworld.cli --scenario quest --guard venti
```

输入 `ask 唱首诗`；`npc varka` 切换；`ask 我是新手` 查看指引；`exit` 退出。CLI 使用内存状态，退出后丢失，不等同于网页 SQL 存档。

## 可选服务配置

复制 `echoworld/.env.example` 为 `echoworld/.env`，仅在本机填写凭据。真实密钥、Redis 密码、存档和虚拟环境不应提交到 Git。

- DeepSeek：配置 `ECHOWORLD_LLM_API_KEY`，将 NPC/NARRATION 模式设为 `llm`，启动时不加 `--offline`。失败降级仅影响文案，不重新结算业务。
- Redis：配置 `ECHOWORLD_REDIS_URL`；服务故障回源 SQL，Redis 不是权威游戏数据库。
- 千问/Milvus：配置 Embedding key、Milvus URI 和必要 token。导入是显式操作，会产生 API 费用及写入，不会在启动时自动执行。

```powershell
./.venv/Scripts/python.exe -m echoworld.knowledge
# 上一行仅报告配置存在与否，不输出密钥。
# 确认配置与费用后，再按需执行：
./.venv/Scripts/python.exe -m echoworld.knowledge --ingest-public
```

Windows 使用 Milvus 服务器，不把内存替身或 Milvus Lite 当作已完成真实集成。当前主要验证 SQLite，本地文件锁不代表生产多 worker / 多主机租约机制。

## 文件导航

| 模块 | 职责 |
| --- | --- |
| `echoworld/state.py`、`rules.py`、`game_actions.py`、`combat.py` | 权威状态、许可任务、确定性动作与战斗规则 |
| `api.py`、`server.py`、`static/index.html` | FastAPI、启动配置、网页交互与 SSE |
| `agent_steps.py`、`agent_graph.py`、`tools.py` | 只读模型闭环、图路由、工具白名单与参数校验 |
| `guard_skills.py`、`npc_skills/`、`reply_guard.py` | 双守卫角色资产、有限话术与事实回复校验 |
| `database.py`、`action_store.py`、`chat_recovery.py` | 存档、CAS/幂等回执、持久化图与恢复 |
| `chat_memory.py`、`long_memory.py`、`lore_rag.py` | NPC隔离近期记忆、私有偏好、授权世界观检索 |
| `shared_knowledge.py`、`evolution_admin.py` | 公共标准FAQ的授权候选、人工审核、版本/审计、有效性回查和反馈 |
| `context.py`、`run_trace.py`、`evaluation.py` | 上下文预算、脱敏事件与离线评测 |
| `narration.py`、`deepseek_agent.py`、`embeddings.py` | 可选模型/向量化适配器与叙事降级 |
| `mcp_server.py` | 独立只读 MCP stdio 演示；主图仍本地调用工具 |
| `test_*.py`、`verify_main.py` | 回归测试与隔离本地 HTTP 验收 |

## 验证

```powershell
./.venv/Scripts/python.exe -m unittest discover -s echoworld -p "test_*.py" -q
./.venv/Scripts/python.exe -m echoworld.verify_main
./.venv/Scripts/python.exe -m echoworld.verify_guard_evolution
```

2026-10-12 v1.2发布副本全量276项测试通过（20.056秒），包含策略计分、祝福选择/结算、存档兼容及旧能力回归；本轮没有重跑独立浏览器与外部服务验收。历史v1为246项，前次临时本地HTTP知识演进10组通过，均不冒充本轮复验。详见`CHANGELOG.md`。

这些验证使用离线或脚本模型、临时数据库；缓存测试使用可控替身。不能将其表述为真实模型聊天质量、真实 Redis/Milvus 全链或生产上线能力已验收。

## 角色来源与声明

本项目与米哈游 / HoYoverse 无关联，角色是非官方学习演示中的客串设定；话术为原创角色化表达，不冒充官方台词。项目使用 AI 辅助开发，运行与能力边界以实际代码和测试为准。

- 温迪表达框架参考 [DGP-Studio/Genshin.Skill](https://github.com/DGP-Studio/Genshin.Skill/tree/a57d1b9dc4d01996b0c5a53e9f4c83d717029cc7/venti-perspective)，MIT 固定版本。上游原文保留在 `echoworld/npc_skills/vendor/venti-perspective/SKILL.md`，许可证位于 `echoworld/npc_skills/vendor/LICENSE`。运行时只加载审定适配版，不执行上游自动研究流程。
- 法尔伽 Skill 为项目自建版，参考 [官方角色预告](https://genshin.hoyoverse.com/en/news/detail/162761)与 [HoYoWiki 米卡关于法尔伽指导的条目](https://wiki.hoyolab.com/pc/genshin/entry/3462?crawler=Googlebot&lang=)，不声称来自第三方成品 Skill。

第三方 MIT 许可仅对应所标注的角色参考文件，不代表本仓库所有内容已统一授权为 MIT。
