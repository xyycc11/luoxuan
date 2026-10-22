# 深境尖塔（EchoWorld）· 七元素与双守卫试炼

一个可本地运行的原神主题游戏 NPC Agent 项目：通过许可任务进入蒙德附近的风之试炼塔，与温迪、法尔伽两位守卫聊天。**当前main新增七元素卡牌、真实生命/护盾战斗与可击败的第十层首领**，并保留战术牌组、遗物构筑、分岔路线与只读战术顾问（2026-10-21同步）。最近正式Git标签仍为[v1.2](https://github.com/xyycc11/luoxuan/tree/v1.2)，本次不新增版本标签；包内`__version__`仍保留v1.2，不代表main只含旧标签功能。

**核心原则：模型参与表达与只读工具选择，代码决定许可、背包、任务、战斗和存档。**

当前是本机教学原型，不是商业游戏或生产级 Agent 平台。固定演示令牌不能用于真实账号认证，服务只监听本机。

## 能力与边界

- **旅人与角色外观（2026-10-22）**：网页开局选择空或荧，随存档保存且不影响数值；双守卫使用用户提供的图片，八种敌人使用游戏图标。图片区域不叠加人物文字，操作与无障碍标签保留；营地不提前展示下层怪物。旅人图片为前瞻宣传图裁切，并非透明立绘；素材来源和权利说明见[第三方素材清单](echoworld/static/assets/THIRD_PARTY_ASSETS.md)。没有生成新人物或怪物图片。

- **双守卫 Skill**：选择温迪或法尔伽，提供不同口吻的有限闲聊、原创小诗、鼓励及依据真实任务阶段的新手指引。
- **受控 Agent 图**：LangGraph 编排 model → tools → model，仅允许查询许可、读取背包，设有调用预算与重复调用检测。
- **权威游戏状态**：显式动作推进许可任务与爬塔，SQL 保存进度；版本 CAS、同事务动作回执与幂等重放防止重复结算。
- **聊天记忆**：SQL 保存完整轮次，Redis 可选缓存最近几轮；按玩家、世界、会话、NPC 隔离。
- **恢复与可观测性**：SQLite 图 checkpoint、聊天结果回执、上下文/记忆/角色版本检查、脱敏 Trace、事件式 SSE 与网页未确认请求恢复。
- **可选模型与 RAG**：DeepSeek 用于只读决策和战斗叙事，千问用于向量化，Milvus 服务器适配用于检索；结果须做来源或 SQL 有效性回查。外部真实服务整链仍待配置与验收。
- **私有偏好**：明确同意后保存有限风格偏好，支持到期、撤回与版本核验；不会自动采集聊天并公开给所有玩家。

闲聊第一版采用“模型选择有限 intent/variant，代码展示已审定话术”，不是无限自由生成的角色聊天。已增加受控公共知识演进，但不是自动训练或开放式自我改写；Sandbox和生产多机调度未实现。

## 当前推荐：七元素 · 七曜试炼

### 最新：机变与唯一流派（2026-10-22）

新开网页/CLI七元素局使用`edition=3`，仍沿用`elements_v1`容器；旧edition=1/2局不升级、不清库。

- **机变二选一**：每回合留1张当前手牌到下一手，或弃1张获得本回合4护盾，共用一次机会、不耗能量。招架不抽牌、不挂元素、不加分；满盾禁用，刷新不重置次数，换回合恢复。
- **唯一流派**：营地可放弃本次回血/加伤，选择烈风破阵（首次反应额外3伤）、磐岩守誓（首次反应获得3盾）、清泉流转（首次反应返1能量，上限3）。本局只能学一种，不能更换，每回合首次反应触发一次。
- 网页提供折叠招架选项与流派触发提示；CLI使用`element_parry_元素名`招架，营地`element_doctrine_assault/bastion/flow`学习。旧版说明按存档edition区分，不能把新机制用于旧局。
- 参考三国杀的手牌转用取舍与Slay the Spire的局内构筑方向，均为原创同人简化；没有新增服务或改变SQL/CAS/幂等/模型只读边界。已有网页服务需重启后新开局体验，不会重置真实存档。

固定seed离线推演中，守誓更安全但更慢，流转更快但余血不更高；这是启发式脚本对照，不是真人胜率、最优保证或乐趣提升证据。以下第二版机制对第三版仍适用，但留牌与新招架共享次数。

网页选择“七元素 · 七曜试炼”，CLI输入`tower_elements`，规则容器`elements_v1`，最新新局使用`edition=3`，旧局保持各自版本。**本节为新模式；下方旧模式的即死与抽3选1说明不适用于七元素局。**

- **新版留牌**：每回合可免费选择保留一张当前手牌，即使能量为0也能选择；结束回合留下该牌并抽至4张。打出已选牌不会复制，也不会返还本回合的留牌次数；换层清除留牌标记。
- **敌人节奏**：五种普通怪有不同的三拍攻击，木盾丘丘暴徒开场带屏障、火史莱姆开场附着火；公开当前及后续两次攻击，帮助选择防御与留牌。普通怪不采用官方元素免疫公式。

- 火、水、雷、冰、草、风、岩各一张，共七张。每回合抽4张、3能量，可连续出牌；出牌后只弃掉用过的牌，结束回合才弃余牌并抽新手牌，刷新/重启不重抽。
- 火耗2打7；水耗1打3并治疗2；雷耗2打5；冰耗1打3并获2盾；草耗1打3；风耗1打2，扩散额外3伤并返1能量；岩耗1打2并获5盾，结晶额外1伤/4盾。当前准确伤害、削盾、治疗和可用状态见牌面。
- 蒸发+5、融化+6、超载+5并削弱攻击2、感电+3且回合末2伤、冻结+2并削弱4、超导+3且下张+2、激化+4且下张+3、燃烧+2且回合末3伤；绽放+1并产草原核（最多2），核接火/雷额外8伤并消耗1核。扩散/结晶只与火水雷冰反应并保留附着，其余反应通常消耗附着。护盾上限30、削弱上限6；同类持续伤害取较高值。
- 旅人初始44生命。前九层普通敌人12至28生命，**没有随机即死**；第4/7/10层前二选一：恢复最多12生命，或七张牌基础伤害永久+1（最多3级）。不叠加旧祝福/遗物/路线。
- 第十层固定“深渊咏者 · 七曜试炼”：90生命、20屏障，反应额外削屏障4，破盾削弱下一次攻击4。预告攻击7/12/20循环，第7回合起每回合额外增加2伤；合理搭配反应破盾、冻结/结晶防重击，再爆发收尾。
- 显式结束回合后，先结算持续伤害；若敌人未被击败，按公开攻击预告扣除护盾和削弱后损失生命。随后能量回3、护盾/本次削弱清零，附着和草原核跨回合保留。普通敌人8回合/首领12回合内击败，否则撤离；生命耗尽失败，不是遇怪直接死亡。
- 大伟丘只在前九层各1%，不攻击；击败额外50分一次。普通分只计实际生命伤害，削盾、过量伤害、治疗、营地不加分。SQL保存牌堆、生命、反应、回合和日志，同层多次动作仍经版本CAS/唯一回执，不重复伤害或领奖。
- 新战斗叙事使用本地规则模板；顾问默认本地建议，可沿用显式模型开关，但仍只读公开局面，玩家确认才执行。没有模型写状态或自动代玩。

从仓库根目录运行：

```powershell
python -m echoworld.cli --scenario quest --seed 2
```

依次`accept`、`claim`、`submit`、`tower_elements`，之后按当前`element_*`选项出牌，`element_end`结束回合、`status`查看、`exit`退出。网页已有服务需手动重启；旧已开塔局不切换、不清库，可用CLI独立内存局体验新模式。

七元素和草反应主题参考[原神官方3.0更新说明](https://genshin.hoyoverse.com/id/news/detail/23257)，所有数值与首领是项目原创同人简化；无官方元素量/精通/抗性免疫公式，不搬用官方美术。不是完整七圣召唤或杀戮尖塔复刻。

离线规则推演：`python -m echoworld.verify_elements`。新版edition=2的100组固定seed对照中，当前手牌连招不留牌通关100组、启发式留牌100组、即时伤害贪心34组、随机合法出牌66组；策略使用同样营地规则。留牌平均余血25.29，不留牌25.67，留牌并非总是更优。这只是脚本对照，不是真人胜率、全局最优或乐趣提升证明。

2026-10-22外观版发布副本实测`python -X utf8 -m unittest discover -s echoworld -p "test_*.py" -q`：469项67.826秒通过，退出码0。使用临时SQL/本机HTTP/假模型，不是外部LLM、Redis、Milvus整链验收。

## 兼容保留：旧牌组、遗物与分岔路线

- **战术牌组 `tower_cards` / `cards_v1`**：6张初始牌，每轮抽3选1，其余弃置，牌堆空时确定性洗回；8种原创正常招式，专注成本与架势收益不同。第4/7/10轮前先营地、再遗物、再选新卡或跳过，最多9张；新卡置顶，下手保证入手。全手不可用才有应急整备，刷新/重启不换牌。复用祝福、营地和遗物，不叠加分岔契约，避免营地过多选择。
- **分岔构筑 `tower_routes` / `spire_routes_v1`**：每章选补1专注或先付1专注接进攻目标；本章成功普通攻击2次额外+6（终章1次+3），只领奖一次、跨章失效不退款。这是资源/计分契约，不是完整分支地图，也不是生存安全/危险分支。
- **遗物构筑 `tower_spire` / `spire_v1`**：营地休整后三选一遗物，本局最多3件，6种原创条件联动；候选不重抽，效果与祝福叠加。结局分解已发生贡献，不重复计分。
- **章节远征 `tower_expedition`**：四段章节、三处营地，选择补专注、强化祝福或换即时战功；不使用遗物或手牌。原普通/祝福入口仍保留。
- **新版网页**：原创AI生成风城插画、纸张与风绿操作台、手牌成本与准确效果、牌组组成/抽弃数量、真实手记与结局，手机自适应。资源来源与生成提示词见[素材说明](echoworld/static/assets/README.md)，不搬运官方游戏资源。
- **战术顾问与复盘**：默认本地规则，明确标注来源；可选模型最多两步只读工具链，只依据公开局面推荐当前合法且可用的动作。玩家确认后走原归属/轮次/CAS/幂等入口。支持缓存、预算/并发限制、过期建议失效与真实事件引用；没有聊天写游戏状态工具，也不保证最优策略。

上述旧模式参考[Slay the Spire](https://www.megacrit.com/press-kits/slay-the-spire/)与[Monster Train](https://www.themonstertrain.com/)的局内构筑方向，均为本项目原创简化规则。旧模式仍不含HP/多卡连打，保留十轮、原怪物概率、两类即死、终轮死亡优先和大伟丘恰好50分；七元素新模式则使用上节独立规则，不混用生死或奖励口径。

已有网页服务需手动重启载入新代码。每个存档仍只开一场，旧已开塔不升级；尝试新模式请另开数据库，不删除原存档。规则指纹已更新，旧公共FAQ需人审，真实Milvus资料需显式重新导入，不会自动消费Embedding额度。

## v1.2标签已有：策略计分与可选祝福

新开普通试炼使用 `tactics_v1`：专注初始2/上限3，破绽攻击耗1得3分，蓄势攻击耗2得4分；防御回1专注，蓄势防御得1分。动作预览与结算共用规则，专注不足时攻击不得分且不扣专注。牌组模式则禁止打出不可支付的牌。

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

演示流程：Alice → 接受任务 → 交付 herb → 选择战术牌组或其他模式 → 选择祝福 → 按当前合法手牌/选项行动；Bob 需要先接受任务、领取一次教学 herb，再交付。`--demo-seed 2`用于固定演示，不代表所有随机局都能通关。当前牌组模式每轮只出1张，其余弃置；营地按提示选择。

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

固定种子牌组演示：

```powershell
./.venv/Scripts/python.exe -m echoworld.cli --scenario quest --seed 2
```

依次输入`accept`、`claim`、`submit`、`tower_cards`，选择当前`boon_*`，再按当前`card_*`出牌；营地使用`camp_*`、`relic_*`、`draft_*`或`draft_skip`。`status`只读查看当前局面。想玩分岔/遗物模式，在尚未开塔时改选`tower_routes`/`tower_spire`，不能在同一已开塔存档中途切模式。

## 可选服务配置

复制 `echoworld/.env.example` 为 `echoworld/.env`，仅在本机填写凭据。真实密钥、Redis 密码、存档和虚拟环境不应提交到 Git。

- DeepSeek：配置 `ECHOWORLD_LLM_API_KEY`，将 NPC/NARRATION 模式设为 `llm`，启动时不加 `--offline`。失败降级仅影响文案，不重新结算业务。
- 战术顾问：默认本地规则；`ECHOWORLD_GAME_AI_MODE=llm`显式配置后，网页仍需勾选模型使用才能付费请求。建议不自动执行，过期不会自动重试写动作；真实模型质量和费用需自行验证。
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
| `cards.py`、`relics.py`、`routes.py`、`expedition.py` | 牌堆/手牌与选牌、遗物、章节契约和营地规则 |
| `game_ai.py`、`ai_generation.py` | 公开战况顾问/复盘、只读两步模型链、生成预算与幂等缓存 |
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

2026-10-21当前发布副本全量**398项测试通过，55.905秒，退出码0**，CLI入口检查通过。包括牌守恒、同源预览/结算、选牌/出牌幂等、跨SQL实例竞争、重启恢复、旧JSON兼容、稀有奖励和终轮死亡优先、60组全怪seed序列对照及假模型两步只读链。源目录此前独立临时网页seed2牌组十轮47分、最终7张牌/跳过2次，手机390/桌面1360无横溢出；本次发布副本未重新跑浏览器或真实外部服务。v1.2标签历史为276项，v1标签为246项，不能当作本次运行结果。详见`CHANGELOG.md`。

选牌对照可运行`python -m echoworld.verify_cards`，仅200组固定seed/纯普通怪/确定性贪心脚本，不是最优策略、全怪通关率、模型质量或真人更喜欢的证据。

这些验证使用离线或脚本模型、临时数据库；缓存测试使用可控替身。不能将其表述为真实模型聊天质量、真实 Redis/Milvus 全链或生产上线能力已验收。

## 角色来源与声明

本项目与米哈游 / HoYoverse 无关联，角色是非官方学习演示中的客串设定；话术为原创角色化表达，不冒充官方台词。项目使用 AI 辅助开发，运行与能力边界以实际代码和测试为准。

- 温迪表达框架参考 [DGP-Studio/Genshin.Skill](https://github.com/DGP-Studio/Genshin.Skill/tree/a57d1b9dc4d01996b0c5a53e9f4c83d717029cc7/venti-perspective)，MIT 固定版本。上游原文保留在 `echoworld/npc_skills/vendor/venti-perspective/SKILL.md`，许可证位于 `echoworld/npc_skills/vendor/LICENSE`。运行时只加载审定适配版，不执行上游自动研究流程。
- 法尔伽 Skill 为项目自建版，参考 [官方角色预告](https://genshin.hoyoverse.com/en/news/detail/162761)与 [HoYoWiki 米卡关于法尔伽指导的条目](https://wiki.hoyolab.com/pc/genshin/entry/3462?crawler=Googlebot&lang=)，不声称来自第三方成品 Skill。

第三方 MIT 许可仅对应所标注的角色参考文件，不代表本仓库所有内容已统一授权为 MIT。
