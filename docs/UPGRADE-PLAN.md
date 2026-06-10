# Marginalia 升级计划（2026-06）

本计划由代码审查 + 设计评估 + 2026 前沿对照得出，供执行 Agent 分任务实施。
每个任务卡独立可交付：自带背景、改动点（具体到 file:line）、验收标准、依赖、规模。

规模图例：S = 半天内 / M = 1-3 天 / L = 1 周+。

---

## 执行约定（每个 Agent 必读）

1. 先读 `DESIGN.md`，尤其 §11 Invariants 和 §14 Non-goals。任务不得违反不变量；
   若实现迫使某条不变量变化，**停下来报告**，不要擅自改。
2. 项目设计原则（来自维护者，强制遵守）：
   - **LLM 是元数据的第一公民**：AI 填充元数据，不新增用户手填元数据的入口。
   - **`section_id`（s1/s2…）是 LLM 内部标识**：用户可见界面只展示
     heading/page/sheet 锚点，绝不渲染 s1/s2。
   - **audio profile 在管线落地前保持隐藏**：不要恢复其 UI 或写路径。
   - **reprocess 端点接受任意状态的文件**：清 ingest 状态后重跑。
3. 测试为 e2e 风格（`tests/test_*_e2e.py`），每个行为改动配 e2e 测试。
   运行：`python -B -m pytest tests -q`。
4. 文档同步：行为变化必须同步更新 `DESIGN.md`（声明"描述当前代码行为"）、
   `README.md` / `README.zh-CN.md`、`CHANGELOG.md`。
5. **有界声明文化**：性能/质量改进的文档表述必须绑定具体验证配置，禁止通用 SOTA 声明。
6. Schema 变更：同时更新 `db/bootstrap.py`（幂等 shim）与新增 `alembic/versions/`
   revision，两轨一致（见 bootstrap.py 头部注释的设计）。

## 阶段总览与依赖

| 阶段 | 主题 | 任务 | 可并行 |
| --- | --- | --- | --- |
| P0 | 工程基础 | T1 T2 T3 | 三者并行 |
| P1 | 安全修复 | T4 T5 | 与 P0 并行 |
| P2 | 理念对齐 | T6 T7 T8 | T6/T7 并行；都建议在 T14 之后做以便量化 |
| P3 | 检索正确性 | T9 T10 | T10 依赖 T9 |
| P4 | 自适应路由 | T11 | 依赖 T14（用其验证） |
| P5 | 前沿对接 | T12 T13 T14 T15 | T14 优先，其余并行 |
| P6 | 模块健康 | T16 T17 | 与任意阶段并行 |

**建议执行顺序**：P0+P1 先行（解锁 + 止血）→ T14（eval 归因，给后续所有改进提供度量）
→ P3（检索正确性，T9/T10）→ P2（理念对齐，用 T14 度量）→ P4（路由）→ P5 其余 → P6 穿插。

---

## Phase 0 — 工程基础

### T1. Python lint 进入 dev 依赖与 CI 【S】
- 背景：4.3 万行 Python 无 lint/类型检查；CI（`.github/workflows/ci.yml`）只有 pytest，
  前端却有 `npm run lint`。`.gitignore` 含 `.ruff_cache/` 说明本地用过 ruff 未固化。
  代码里已有 `# noqa: BLE001` 标注，说明风格意识在，只差强制执行。
- 改动：
  - `pyproject.toml`：dev extras 加 `ruff>=0.8`；新增 `[tool.ruff]`，规则集起步保守
    （E, F, W, B, BLE, ASYNC），line-length 对齐现有代码。
  - 修复或显式 `# noqa` 存量告警（BLE001 那批先 noqa，留给 T18 类清理）。
  - `.github/workflows/ci.yml` backend-tests job 加 `uv run ruff check src tests`。
- 验收：CI 三平台全绿；本地 `ruff check src tests` 零未处理告警。
- 依赖：无。

### T2. .gitignore 补漏 【S】
- 背景：`.codex-pycache/`、`.codex-run/` 长期挂在 `git status`；e2e 遗留的 PID 后缀
  目录（`tests/_cli_upgrade_e2e_64588_1b12c9bf` 等）不匹配现有 `tests/_*_e2e_data/`。
- 改动：`.gitignore` 增加 `.codex-pycache/`、`.codex-run/`、`tests/_*_e2e_*/`；
  删除工作区现存遗留目录。
- 验收：`git status --short` 干净。
- 依赖：无。

### T3. e2e 临时目录清理加固 【S】
- 背景：`tests/test_cli_upgrade_e2e.py:28-30`、`tests/test_folders_ingest_status_e2e.py:37`
  用 `atexit + shutil.rmtree(ignore_errors=True)`，Windows 句柄未释放时静默失败、目录泄漏。
- 改动：在 `tests/conftest.py` 提供共享 helper（带重试的 rmtree：Windows 上 3 次、
  间隔递增），或迁移到 pytest `tmp_path_factory`；替换所有自建 `_TEST_ROOT` 的测试。
- 验收：全量测试后 `tests/` 无新增残留目录（含 Windows CI）。
- 依赖：无。

---

## Phase 1 — 安全修复（成本极低）

### T4. DuckDB SQL 工具引擎级沙箱 【S】
- 背景：`src/marginalia/agent/tools/query_sql.py:55-65` 用正则黑名单拦 LLM 生成的 SQL，
  但 DuckDB 有黑名单覆盖不到的本地文件读取途径：`SELECT * FROM 'D:/path/file.csv'`
  （FROM 子句路径字面量，不经函数）、`parquet_scan`/`csv_scan` 别名、`glob()` 列目录；
  正则也未拦 `SET`。连接建于 `query_sql.py:272`。
- 改动：在 `_run_duckdb` 里加载完表之后、执行 LLM SQL 之前，执行
  `SET enable_external_access=false;` 与 `SET lock_configuration=true;`，用引擎级开关
  替代正则兜底。正则保留作为第一道（更友好的报错），但安全性不再依赖它。
- 验收：新增 e2e——`SELECT * FROM 'some.csv'`、`parquet_scan(...)`、`glob(...)`
  在加载授权表后均被引擎拒绝；正常的针对已加载表的 SELECT 仍工作。
- 依赖：无。

### T5. compose 端口绑定 localhost + 可选 bearer 中间件 【S/M】
- 背景：API 层无请求鉴权（`api/` 下 `api_key` 命中均为存储的 LLM 密钥，非请求认证）。
  桌面绑 localhost 无碍，但 `docker-compose.yml:90` 写 `"8000:8000"`、`:39` 写
  `"9001:9001"`，等于局域网任何人可读整库、并能 PUT `/settings` 改 LLM endpoint
  到攻击者服务器（后续请求会把 LLM 密钥作为 Authorization 头发出）。
- 改动：
  - compose 默认改 `"127.0.0.1:8000:8000"`、`"127.0.0.1:9001:9001"`；
    在 compose 注释和 `README.md` 部署节说明如何显式开放。
  - 中期（M）：加可选 bearer 中间件——`MARGINALIA_API_TOKEN` 设置存在时，
    `main.py` 注册一个校验 `Authorization: Bearer` 的中间件（`/health` 豁免）；
    未设置则保持现状（嵌入式/桌面默认免认证）。CLI/desktop client 支持带 token。
- 验收：默认 compose 起栈后宿主外网卡无法访问 8000/9001；设置 token 后无 token 请求 401。
- 依赖：无。

---

## Phase 2 — 理念对齐（核心赌注校正）

### T6. journal 时态失效机制 【M/L】★高价值
- 背景：journal 是设计中最前瞻的部分，但 `repositories/journal.py` 只在
  `summarize_session` 主动归并时 supersede，**没有机制在文件删除/重新 ingest/内容变更后
  让引用它的 journal 行失效**。错误或过时结论会在 `search_journal`/`recall_knowledge`
  中被持续复读，且因其处于漏斗第一层，污染自我强化。
  2026 前沿参照：Zep/Graphiti 的双时态事实失效（valid/invalid 时间窗 + LLM 矛盾检测，
  非破坏性、可时点查询）；OpenAI Temporal Agents cookbook 采纳同模式。
- 改动（分两步，可只做第一步）：
  1. **存活校验降权**（必做，低风险）：`recall_knowledge`/`search_journal` 返回 journal
     行时，校验其 `entry_ids` 仍存活且未在该 journal 行 `created_at` 之后被重新 ingest；
     不满足则降权并在结果里标注"引用实体已变更"。需要 journal 行能拿到引用 entry 的
     `ingested_at`（join files）。
  2. **矛盾失效**（进阶）：`reflect_turn` 写新行时，让 LLM 对比同 `entry_ids` 的现存
     非 superseded 行，发现矛盾则给旧行设失效标记（复用 `superseded_by_id` 或新增
     `invalidated_at`/`invalidated_reason` 列）。失效非删除，保留可审计。
- 验收：构造"先得出结论 A，源文件重新 ingest 后结论应变 B"的 e2e，验证旧 journal 行
  被降权/标注；矛盾失效路径有单独 e2e。
- 当前实现备注：已落地存活/重 ingest 校验降权，以及 `reflect_turn` 的矛盾失效路径。
  journal 新增 `invalidated_at`/`invalidated_by_id`/`invalidated_reason`；默认
  `search_journal`、stable snapshot、summarize/mining/lifecycle 等活跃读取会过滤
  invalidated 行，`include_invalidated=true` 可用于审计查询。
- 依赖：建议在 T14 之后，用 eval 量化 journal 召回质量的前后变化。
- 注意：不变量"journal 是长期投资记忆"不变，本任务是给它加正确性维护。

### T7. 关系挖掘 lazy 化 【M】
- 背景：`mine_relations → vet_relations` 是 eager 的——后台用 LLM vet 每条噪声边，
  成本前置且不论该边将来是否被查询用到。2026 前沿（LazyGraphRAG）证明 lazy 大胜：
  索引时只做廉价信号，LLM 成本推迟到查询命中时。这是架构里唯一"成本确定、收益未证"
  的子系统。
- 改动：
  - 廉价信号边（session 共现、tag 重叠）直接入 `entry_relations`，**不预先 LLM vet**，
    标 `vetted=false`。
  - `find_related`/`/discover` 真正命中某条未 vetted 边时，**按需** vet 并缓存
    （写回 `vetted`/`vetted_reason`/`vetted_at`）。
  - `vet_relations` 后台任务降级为可选的低优先维护（或在 T8 预算耗尽时跳过）。
- 验收：上传后不再触发整批 LLM vet（观察 task 队列）；`/discover` 首次命中未 vetted
  边时触发单边 vet 且结果被缓存（第二次不再调 LLM）。
- 当前实现备注：已落地 `/discover` 按需 vet 直接命中边并缓存 verdict；
  periodic `vet_relations` 默认关闭，可用 `RELATION_BACKGROUND_VETTING_ENABLED=true`
  或 `/tend` 作为可选批量维护。
- 依赖：与 T6 并行；建议 T14 之后量化对 `/discover` 质量的影响。

### T8. 维护预算与分级 ingest 【M】
- 背景："LLM 第一公民"带来随库规模/时长线性增长的持续税：每次上传 ingest、每 turn
  reflect、后台 vet_relations/tag_quality/restructure_catalogs/propose_views 全是 LLM。
  `sessions` 表已记 token 成本，但后台维护无预算上限。`LLM_INGEST_MODEL` 只有一档。
- 改动：
  - 引入"维护预算"：每日后台 LLM token 上限（新设置 `MAINTENANCE_DAILY_TOKEN_BUDGET`），
    低优先任务（vet_relations、restructure_catalogs、propose_views）在预算耗尽时跳过，
    下一周期续做。预算消耗从 `task_outcomes`/sessions 成本汇总读取。
  - 分级 ingest（可选）：允许按 folder/catalog 配置 ingest 模型档位——普通文件用便宜
    模型，重要目录用好模型。保持向后兼容（未配置时用 `LLM_INGEST_MODEL`）。
- 验收：构造预算耗尽场景，验证低优先后台任务被跳过且记录原因；核心任务（ingest_file、
  reflect_turn）不受预算限制。
- 当前实现备注：已落地 `MAINTENANCE_DAILY_TOKEN_BUDGET` 后台维护预算；分级 ingest
  仍保持为可选后续项。
- 依赖：无强依赖；与 T7 配合效果好（T7 已削减 vet 成本）。

---

## Phase 3 — 检索正确性

### T9. Postgres 检索路径补 FTS（消除部署形态不对称）【M】
- 背景：`repositories/entries.py:127-148` 的 `_metadata_fts_query` 对非 SQLite 方言直接
  返回 None，元数据文本检索在 Postgres 上退化为无索引 `ILIKE` 顺序扫描。导致反直觉的
  不对称：**面向多机的 remote 部署检索能力反而弱于单机 SQLite**。
- 改动：Postgres 分支用 `to_tsvector`/`websearch_to_tsquery` + GIN 索引（参照 Atlas 的
  `search/fts.py` 实现），或 `pg_trgm`。新增对应 alembic + bootstrap 索引创建。
  保持现有 `_apply_metadata_fts_filter`/`_join` 的接口，仅替换方言实现。
- 验收：同一查询在 SQLite 与 Postgres 后端返回可比结果；Postgres 上有 GIN 索引、
  `EXPLAIN` 不是 seq scan。
- 依赖：无。

### T10. CJK 短词处理 + 中文 eval 集 【M/L】★风险点
- 背景：trigram 分词器（`db/bootstrap.py:264`）对 CJK 合理，但
  `_metadata_fts_query_from_terms`（`entries.py:101-116`）把 <3 字符的词静默丢弃
  （`_MIN_TRIGRAM_FTS_TERM_LEN`），中文双字词（"数据"/"模型"）极常见。更大问题：
  唯一验证集 SciFact 是英文，**"LLM 元数据 + trigram 词法"在中文语料上的表现未知**，
  而这正是该架构相对 embedding 方案最可能露怯处。
- 改动：
  - 短词不再静默丢弃：<3 字符 CJK 词用 LIKE 子句 OR 进 FTS 查询（混合查询里补充，
    不是替代）。
  - 构建小型中文 eval 集（可用公开中文检索数据集转 BEIR 格式，或自建 30-50 query），
    纳入 `marginalia eval` 流程。
  - 跑中文集，记录 recall_knowledge / +rerank 的指标，写入 DESIGN.md §8（有界声明）。
- 验收：中文双字词查询能命中预期文档；中文 eval 报告产出且数字入文档。
- 依赖：T9（Postgres 用户的中文检索同样依赖 FTS 实现）。

---

## Phase 4 — 自适应路由（AI 自决规划档位）

### T11. plan 阶段输出预算档位 + 升级检查点 【M】★
- 背景：当前 quick/deep 由用户手动 `/mode` 二选一。目标：让 AI 自己决定规划深度。
  **关键设计**：不要让 AI 一次性预测轮次（检索难度只有在第一批召回回来后才显现，
  LLM 先验校准差）。成本不对称——低判可升级恢复，高判的浪费已发生（前沿数据：复杂
  agentic 查询成本可达简单的 20-40 倍）。所以**让路由器大胆往低判，靠升级兜底**。
  相关代码：`NO_PLAN:` 前缀协议在 `runtime.py:116`；预算初始化
  `runtime.py:951-953`（`QUICK_EXECUTE_MAX_TURNS=4` / `agent_execute_max_turns`）；
  强制回答分支 `runtime.py:980-995`；SSE `plan` 事件已存在。
- 改动：
  1. **planner 输出档位**：计划首行输出 `BUDGET: quick|standard|deep`
     （映射如 4/8/15 轮）。复用 `NO_PLAN:` 同款前缀解析。`NO_PLAN` 作为第零档保留。
     planner prompt 明确"拿不准选低档，系统会自动升级"（把不对称成本编码进指令）。
  2. **预算告知执行器**：execute 系统提示写明"本轮约 N 次工具调用预算"。
     **只给数字，不要把 planner 的难度判断传给执行器**（避免锚定压低回答质量）。
  3. **升级替代硬截断**：初始档位耗尽时，若 (a) 未到 `agent_execute_max_turns` 硬顶、
     (b) doom-loop 守卫未触发过、(c) 最近工具调用仍在产新证据（非重复），则升一档继续，
     每会话最多升级 1-2 次；否则走现有强制回答路径（`runtime.py:980` 附近）。
  4. **护栏不变**：`agent_execute_max_turns` 仍是绝对硬顶；doom-loop 对升级一票否决；
     用户手动 `/mode quick|deep` 保留为强制钉死（quick=低档不升、deep=直接最高档），
     `types.py` 的 `ChatMode` 加 `auto` 并设为默认。
  5. **UX 透明**：SSE `plan` 事件加 budget 字段；CLI/GUI 显示"快速/标准/深入"预期与
     升级发生（用户失去手动选择，必须能看见系统替他选了什么）。
- 当前实现备注：已落地默认 `auto` chat mode；planner 使用非 JSON 的
  `BUDGET: quick|standard|deep` 控制线，执行期按档位设置预算并在新工具证据持续产出、
  doom-loop 未触发、未达到硬顶时自动升级；CLI/GUI 会显示初始预算和升级通知。
- 验收（用 T14 eval 框架，三组对照）：
  - always-deep（基线）/ auto-无升级（纯预测，弱版本）/ auto-带升级（本任务）。
  - 三个数：citation hit 相对基线损失（验收：噪声范围内）、平均工具调用次数
    （预期显著下降）、升级触发率（路由器校准读数——过高说明系统性低估，调 prompt/锚点）。
  - 若"纯预测"质量损失明显大于"带升级"，即验证核心论点。
- 依赖：T14（验证手段）。

---

## Phase 5 — 前沿对接

### T14. eval 升级为组件归因（消融矩阵）【M】★先做
- 背景：eval 基础设施（BEIR 导入、answer probe、盲评对比）是同类项目稀有资产，但现在
  只回答"整体打不打得过 one-shot RAG"。架构有多个贵赌注（两阶段 plan、关系挖掘、
  journal 召回、semantic recall、rerank）全靠端到端总分背书。
- 改动：在 `marginalia eval` 加消融开关矩阵——plan 阶段开/关、关系扩展开/关、
  semantic recall 开/关、rerank 开/关，输出每个子系统对最终质量的边际贡献。
- 验收：能跑出一张消融表（各配置 × 指标）；结果写入 DESIGN.md §8。
- 价值：边际成本最低、信息量最大；为 T6/T7/T11 提供度量。**建议 P0/P1 之后立即做。**
- 依赖：无。

### T15. MCP server 暴露检索工具集 【M/L】★战略
- 背景：精心设计的漏斗（`recall_knowledge`/`read_files`/`search_journal` 等 13 个工具）
  目前只有自家 agent 能用。暴露为 MCP server 后，Claude Desktop 或任何前沿 agent 可把
  Marginalia 当"个人图书馆后端"。对冲长期风险：前沿模型内建 agent 能力会持续超过自建
  ReAct loop，护城河应从"agent 本身"转移到"数据模型 + 检索工具集"。
- 改动：新增 MCP server 入口（复用现有 tool 实现与 schema），暴露只读检索工具
  （recall/read_files/search_metadata/search_journal/read_entries_metadata 等）。
  写类工具（reflect 等）暂不暴露。提供配置文档。
- 验收：用 MCP inspector 或 Claude Desktop 连接，能调用 recall_knowledge 并 read_files
  拿到带引用的结果。
- 依赖：无；建议工具接口在 T16 等重构稳定后做。

### T16. 引用契约升级为可验证属性 【M】
- 状态：已完成（2026-06-10）。展示层脚注会附加
  `quote_status=verified|unverified`，PDF quote 定位与通用 quote 验证共享空白/标点
  归一化匹配；失败只标注不删除答案。
- 背景：DESIGN.md §1.3 引用契约靠 LLM 自觉。展示层的 quote-location 逻辑
  （`agent/runtime.py:788` 附近的 PDF quote locator）已做了一半。
- 改动：服务端校验 footnote 的 `quote` 确实存在于该 entry 原文中（容忍空白/标点归一化），
  向用户暴露 verified/unverified 标志。校验失败不删答案，只标注。
- 验收：构造 LLM 编造 quote 的场景，验证被标 unverified；真实 quote 标 verified。
- 依赖：无。

### T17. 文档化 known gaps 【S】
- 背景：mirror vault + SQLite 被 Syncthing 类工具同步会损坏数据库，local-first 用户
  迟早踩。多设备同步是真实缺口。
- 改动：`README.md`/`USAGE.md` 增加"多设备同步"小节，明确：不要用文件同步工具同步
  `MARGINALIA_HOME`（SQLite 会损坏）；多设备请用 remote（Postgres+S3）部署形态。
- 验收：文档存在且中英文一致。
- 依赖：无。

---

## Phase 6 — 模块健康（穿插进行）

### T18. eval/core.py 拆分 【M】
- 背景：`src/marginalia/eval/core.py` 3198 行混了五种职责（dataclass 定义、BEIR 导入、
  运行、序列化、格式化输出）。
- 改动：拆为 `eval/types.py`（dataclass）、`eval/io.py`（BEIR 导入/迭代器）、
  `eval/runner.py`（run/answer/compare）、`eval/format.py`（to_dict/format_*）。
  保持 `eval/core.py` 作为兼容 re-export 或更新所有引用。
- 验收：导入路径不破坏现有 CLI；测试全绿。
- 依赖：与 T14 协调（T14 也动 eval，建议先 T18 再 T14，或合并做）。

### T19. pdf.py 事件循环阻塞修复 【S】
- 背景：`pipelines/pdf.py:724-725` 在 async 的 `_answer_with_vlm` 里同步执行
  `PdfReader(io.BytesIO(pdf_bytes)).pages`——agent 聊天延迟路径，大 PDF 页数统计会卡住
  事件循环。同文件其他处已用 `asyncio.to_thread`（3 处），这是漏网。
- 改动：把该 `PdfReader` 调用包进 `asyncio.to_thread`。顺带扫一遍 pdf.py 其他 async
  函数内的同步 pypdf/pdfium 调用。
- 验收：功能不变；代码审查确认无 async 内裸同步重 IO。
- 依赖：无。

### T20.（可选）BLE001 系统清理 【M】
- 背景：104 处 `except Exception`，抽查多数带 `log.exception`，但 `runtime.py:228-232`
  这类双层 fallback 完全无日志。
- 改动：启用 ruff BLE001 后逐一处理——能收窄异常类型的收窄，必须宽捕的补日志。
- 验收：ruff BLE001 零未处理（或全部有理由的 noqa）。
- 依赖：T1。

---

## 不做清单（明确排除，避免执行 Agent 跑偏）

- 不引入 vector DB 作为主检索层（违反 §14 Non-goal）。
- 不做自动用户文件删除（违反 §11 / §14）。
- 不把 audit log 或原始对话历史变成检索记忆（违反 §11）。
- 不恢复 audio profile 的 UI/写路径。
- 不新增用户手填元数据入口。
- ColPali 类视觉嵌入：架构上保留 vision profile 作为未来后端接入点即可，**本轮不实现**
  （每页千级向量对个人库成本不划算）。
