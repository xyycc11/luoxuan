# EchoWorld · 双守卫与文字爬塔

一个可本地运行的游戏 NPC Agent 学习项目：通过许可任务进入十轮文字爬塔，与温迪、法尔伽两位守卫聊天。

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

闲聊第一版采用“模型选择有限 intent/variant，代码展示已审定话术”，不是无限自由生成的角色聊天。公共知识自进化、Sandbox 和生产多机调度未实现。

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
| `context.py`、`run_trace.py`、`evaluation.py` | 上下文预算、脱敏事件与离线评测 |
| `narration.py`、`deepseek_agent.py`、`embeddings.py` | 可选模型/向量化适配器与叙事降级 |
| `mcp_server.py` | 独立只读 MCP stdio 演示；主图仍本地调用工具 |
| `test_*.py`、`verify_main.py` | 回归测试与隔离本地 HTTP 验收 |

## 验证

```powershell
./.venv/Scripts/python.exe -m unittest discover -s echoworld -p "test_*.py" -q
./.venv/Scripts/python.exe -m echoworld.verify_main
```

2026-10-09 开发副本实测 202 项测试通过。真实临时本地服务的 12 组检查涵盖许可任务、十轮爬塔、模板叙事、进程重启、动作回执、请求冲突、SSE结果重放、聊天去重及双守卫权限/角色/历史隔离/重启重放。

这些验证使用离线或脚本模型、临时数据库；缓存测试使用可控替身。不能将其表述为真实模型聊天质量、真实 Redis/Milvus 全链或生产上线能力已验收。

## 角色来源与声明

本项目与米哈游 / HoYoverse 无关联，角色是非官方学习演示中的客串设定；话术为原创角色化表达，不冒充官方台词。项目使用 AI 辅助开发，运行与能力边界以实际代码和测试为准。

- 温迪表达框架参考 [DGP-Studio/Genshin.Skill](https://github.com/DGP-Studio/Genshin.Skill/tree/a57d1b9dc4d01996b0c5a53e9f4c83d717029cc7/venti-perspective)，MIT 固定版本。上游原文保留在 `echoworld/npc_skills/vendor/venti-perspective/SKILL.md`，许可证位于 `echoworld/npc_skills/vendor/LICENSE`。运行时只加载审定适配版，不执行上游自动研究流程。
- 法尔伽 Skill 为项目自建版，参考 [官方角色预告](https://genshin.hoyoverse.com/en/news/detail/162761)与 [HoYoWiki 米卡关于法尔伽指导的条目](https://wiki.hoyolab.com/pc/genshin/entry/3462?crawler=Googlebot&lang=)，不声称来自第三方成品 Skill。

第三方 MIT 许可仅对应所标注的角色参考文件，不代表本仓库所有内容已统一授权为 MIT。
