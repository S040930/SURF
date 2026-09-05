# r23 DREsS_CASE 平台运行手册

## 1. 范围与安全状态

r23 是新增的独立协议；不删除、不迁移、不覆盖 r20–r22 的表、数据或 URL。默认网页入口是 `/dress`，r22 历史入口仍为 `/research`。

实施已完成：数据门禁、确定性抽样、单配置项目、独立重复运行、REST、MCP 单 worker、人工重试、结果盲化、统计报告、无正文导出和 r23 前端。尚未运行真实模型试点与正式实验，因此当前没有论文结果，也不会由测试自动产生模型费用。

## 2. 架构

| 层 | 位置 | 责任 |
|---|---|---|
| 协议与评分 Schema | `backend/app/experiment/r23/protocol.py` | 固定 seed、规模、九值输出与安全 envelope |
| 数据门禁与抽样 | `backend/app/experiment/r23/dataset.py` | TSV 流式解析、哈希、Organization 配对、两遍物化 |
| 统计 | `backend/app/experiment/r23/analysis.py` | MPA、QWK、MAE、ρ、SI、bootstrap、Holm、负对照 |
| 持久化 | `backend/app/models/r23.py` | 11 张 `r23_*` 表，`score_x2` 整数存储 |
| 领域服务 | `backend/app/services/r23.py` | 自动运行快照、生命周期、盲化、报告锁定与导出 |
| 单调用执行 | `backend/app/experiment/r23/executor.py` | 领取一个逻辑调用、严格验证、attempt 审计 |
| REST | `backend/app/api/r23_platform.py` | `/api/r23` 公共接口 |
| MCP host | `backend/app/mcp_server.py` | 旧协议与 r23 按逻辑调用轮转，只运行一个 Codex 子进程 |
| 前端 | `frontend/src/pages/R23*.tsx` | 创建、Runner、Rubric、数据审计、监控、结果、调用审计 |

数据库迁移头为 `r3h4i5j6k7l8`。r23 表包括 runner、rubric、project、model binding、sample observation、unique evaluation、evaluation-observation mapping、call attempt、run group、event 和 locked report。

## 3. 环境与启动

### 要求

- macOS 或 Linux；
- Python `>=3.12,<3.13`；
- Node.js 22；
- PostgreSQL；
- 已登录的 Codex CLI；
- DREsS 目录位于 `/Users/mac/Desktop/SURF/DREsS`，或以 `R23_DRESS_ROOT` 指向等价受限目录。

生产与开发 Python 依赖分别精确锁定于 `backend/requirements-prod.txt` 和 `backend/requirements-dev.txt`。科学计算版本固定为 NumPy 2.4.6、Pandas 2.3.3、SciPy 1.17.1、statsmodels 0.14.6。

### 启动

```bash
./start.sh
```

打开 `http://127.0.0.1:5173/dress`。管理网页与数据门禁不要求 MCP 在线。

执行模型调用时，Codex App 在可信项目 `/Users/mac/Desktop/SURF/AI-Marking` 中保持连接；项目级 `.codex/config.toml` 调用：

```bash
./start.mcp.sh
```

不要同时手动启动多个 worker。全局 r21 租约仍是唯一 worker 租约，r23 与旧协议在同一串行 host 中轮转。

## 4. 操作流程

### 4.1 数据校查

打开 `/dress/data`，确认：

1. 三份文件均为“已验证”；
2. 行数为 8,307 / 31,086 / 792；
3. SHA-256 与预注册协议一致；
4. `README.docx` 存在；
5. Organization 重建为 1,727 个 base。

任一门禁失败时不要绕过或替换哈希；先核查数据版本。

### 4.2 Runner

1. 打开 `/dress/runners`；
2. 输入当前真实可用的 Codex 模型标识；
3. 选择 `low`、`medium` 或 `high` 思考程度；
4. 选择 `standard` 或 `fast` 运行速度；`fast` 只适用于当前账号和模型支持的 Fast mode，并会消耗更多额度；
5. 设置单篇调用超时，允许 30–1800 秒，新配置默认 120 秒；
6. 保存 Runner。可为同一模型保存多个配置，配置哈希会包含思考程度、速度模式和超时。

平台将 `standard` 映射为 `service_tier="default"`，将 `fast` 映射为 `service_tier="fast"` 和 CLI `fast_mode`。它不能指定精确 token/s 或保证单篇完成时间；受支持模型、额度消耗和可用性以 [OpenAI Codex Speed 文档](https://learn.chatgpt.com/docs/agent-configuration/speed)为准。若模型或账号不支持 Fast，调用失败并进入 `attention_required`，平台不自动降级到 Standard。

不需要人工冻结。点击“开始实验”时，平台自动记录所选模型配置、速度模式、请求的 CLI service tier、CLI 路径、版本和可执行文件 SHA-256。模型配置改变时创建新 Runner，不覆盖已用于实验的记录。

### 4.3 Rubric

1. 打开 `/dress/rubric`；
2. 保留默认的 `DREsS r23 literature-aligned rubric v2`，逐项检查 Content、Organization、Language 的五个整数锚点和半分规则；
3. 保存为新的 Rubric 记录；平台立即计算 SHA-256；
4. 正式实验选择该新记录，不覆盖或继续使用早期 `v1` 自拟锚点版本。

默认量表是 González et al.（2017）的文献改编版并与 DREsS 构念对齐，不是 DREsS 官方完整 rubric，也尚未独立完成人类评分者验证。每个项目绑定一个 Rubric；项目开始时正文逐字进入运行快照。作文内的指令始终只是待评分文本。

### 4.4 技术试点

1. 返回 `/dress`；
2. 选择“技术试点”、一个 Runner 和 Rubric；
3. 勾选受限数据处理授权；
4. 创建项目；
5. 在项目页点击“开始实验”；按钮会显示“正在生成运行快照…”。平台先重新哈希数据，自动保存 Runner、CLI 指纹、Rubric 与环境快照，再确定抽样并批量写入 Manifest 与调用队列；浏览器为这一步保留 3 分钟请求时间；
6. 快照成功后才开始产生模型调用费用；Manifest SHA-256 可在项目页核对和导出。

单配置试点约 175 个评估槽位。运行与完成后，结果页始终密封；技术报告只显示合法率、失败率和 p50/p95 延迟。

### 4.5 正式实验

正式实验可以不绑定技术试点；也可以选择一个完全匹配且已完成的技术试点。若选择试点，平台会校验 Runner、Rubric 和数据哈希完全一致；正式项目开始时再次自动记录这些输入、可选的 pilot ID 和 Manifest。单配置协议上限是 2,535 个槽；完全相同输入去重后，项目页显示实际逻辑调用数。为缩短时间，可为试点和正式实验统一选择 `fast`；不要把 `standard` 与 `fast` 项目合并为同配置重复测量。

正式运行期间：

- “运行监控”显示状态、进度和延迟；
- “结果分析”保持密封；
- “调用审计”只显示 call ID、模型 binding、阶段、状态、错误类型、延迟、input hash 和 attempt 数；
- 不返回任何通道分数或中间趋势。

全部调用成功后，平台运行 5,000 次聚类 bootstrap、负对照、Language baseline 和 Holm 校正，锁定报告 JSON 与 SVG 哈希，然后一次性解除正式结果密封。

已完成项目可点击“再次运行”。平台会创建一个新的草稿项目，复用原 Runner、Rubric、类型与 pilot，并依靠固定 seed 生成相同样本；旧项目、attempt、报告和哈希保持不可变。不同思考程度或速度模式应创建不同 Runner 和项目，不能作为同一配置的重复测量合并。

## 5. 生命周期与失败处理

```text
draft → [开始时自动生成运行快照] → running → analyzing → completed
                                      ↕
                                    paused
                                      ↓
                              attention_required
                                      ↓
                             manual retry → running
                                      ↓
                                  terminated
```

- 超时、断线、CLI 漂移、运行错误或非法输出进入 `attention_required`；
- 分数输出 schema 强制要求仅含 `content`、`organization`、`language` 三个数值字段，且每项只能为 1–5 的 0.5 步长；
- 非法输出会以脱敏分类显示：`invalid_score_schema` 表示三字段 JSON 结构不符，`invalid_score_value` 表示分值不在半分网格；不保存模型原文或 stderr；
- timeout 从单篇调用开始计时；调小可更快暴露卡住的调用，但也会提高正常慢调用被中止的概率；
- 平台没有静默重试；
- 人工重试保留原 attempt、input SHA-256 和失败分类；
- 监控页在“需要处理”计数下提供“查看并重试”入口；调用审计始终优先返回并单独置顶所有当前可见的 `attention_required` 调用，避免较早失败被最近调用的分页上限隐藏；
- 日志和异常只存预定义摘要，不存 stderr 原文，以免正文回显；
- 已开始或完成的项目不可删除；草稿和终止项目可以删除；
- 终止不删除已经完成的 attempt。

## 6. API

### 数据、Runner 与 Rubric

- `GET /api/r23/data-status`
- `GET|POST /api/r23/runner-configs`
- `GET /api/r23/runner-configs/{id}`
- `DELETE /api/r23/runner-configs/{id}?confirm=true`
- `GET|POST /api/r23/rubrics`
- `GET /api/r23/rubrics/{id}`
- `DELETE /api/r23/rubrics/{id}?confirm=true`

### 项目

- `GET|POST /api/r23/projects`
- `GET /api/r23/projects/{id}`
- `POST /api/r23/projects/{id}/{start|pause|resume|terminate}`
- `POST /api/r23/projects/{id}/repeat?confirm=true`
- `GET /api/r23/projects/{id}/groups`
- `GET /api/r23/projects/{id}/calls`
- `POST /api/r23/projects/{id}/calls/{call_id}/retry?confirm=true`
- `GET /api/r23/projects/{id}/report`
- `GET /api/r23/projects/{id}/analysis`

`R23ProjectCreate` 不接收 seed、样本量或临时速度覆盖，只接收一个 Runner ID、一个 Rubric ID、项目类型、可选的 pilot ID 和数据处理确认。`R23RunnerCreate` 的 `speed_mode` 只允许 `standard|fast`。

### 导出

- `.../exports/manifest`：无正文 JSON，项目开始并生成运行快照后可取；
- `.../exports/results`：无正文 CSV，正式报告解密后可取；
- `.../exports/report`：统计 JSON，正式报告解密后可取；
- `.../exports/figure`：可编辑 SVG，正式报告解密后可取。

每个导出响应包含 `X-Content-SHA256` 和 `Cache-Control: no-store`。不要把本地数据库复制到公开补充材料。

## 7. 性能与资源预算

| 阶段 | 预计时间 | CPU/内存 | 磁盘 |
|---|---|---|---|
| 数据哈希与审计 | < 2 分钟 | 单核为主，目标 < 256 MB | 不产生语料副本 |
| 开始时抽样与快照 | 两遍顺序读取，通常 < 2 分钟 | 只保留元数据和抽中正文，目标 < 256 MB | 抽中正文进入本地 DB |
| 技术试点 | 约 175 × 实测延迟 | 同时 1 个 Codex 子进程 | attempt 线性增长 |
| 正式运行 | Standard 单配置约 22–65 小时；Fast 以试点实测重估 | 同时 1 个 Codex 子进程 | 数据库与有界审计目标 < 1 GB |
| 统计报告 | 预期数分钟 | NumPy 与 cluster bootstrap；若 >5 分钟需 profile | JSON/SVG 很小 |

正式时间区间是实施前预算，不是保证。试点完成后，用实测 p50/p95 重新计算：

`预计时长 = 运行快照 Manifest 的实际剩余逻辑调用数 × 单调用延迟`

数据管线采用流式两遍读取，不把约 91 MB 原始语料整体载入内存。所有捕获输出有界，正文不进入事件日志。

## 8. 验证与发布前检查

实施阶段只用合成夹具和 Fake Runner；不要在测试中发送真实作文。

```bash
cd backend
.venv/bin/alembic heads
.venv/bin/black --check app tests
.venv/bin/ruff check app tests
.venv/bin/python -m pytest -q

cd ../frontend
npm run lint
npm test -- --run
npm run build
```

迁移到隔离实验数据库后运行：

```bash
cd backend
.venv/bin/alembic upgrade head
```

本协议的增量迁移基线是 r22 head `p1f2a3b4c5d6`。发布前先用
`alembic current` 核对既有实验库，再升级到 `r3h4i5j6k7l8`；不要为跳过历史迁移而对含数据的数据库手工 `stamp`。2026-08-30 的实施验收曾在一次性 PostgreSQL 14 实例中完成 `p1f2a3b4c5d6 → q2g3h4i5j6k7` 并确认创建 11 张 r23 表；新增迁移移除 formal signature 唯一约束，使相同配置可生成独立重复项目。仓库更早的空库迁移链含有依赖运行时 ORM 元数据的历史迁移，因此“从完全空库重放全部 r18–r22 历史”不属于本次 r23 增量迁移的验证结果。

验收必须覆盖：

- 数据哈希、嵌入换行、九档平衡、1,727 配对、collision 和试点/正式互斥；
- 严格三字段评分 Schema 与 prompt injection 隔离；
- MPA 平局、QWK、MAE、SI、Holm、bootstrap 复现、负对照和 Language baseline；
- 恰好一个模型配置、可调思考程度、独立重复运行、开始时快照不可变、CLI drift、单 worker、手动重试、盲化与导出哈希；
- 运行期密封、完成后图表、失败恢复、暗色、移动端、键盘与打印。

## 9. 界面基准

实现以已确认的创建页和结果页概念稿为视觉基准：冷靛蓝研究后台、固定研究导航、四步开始前检查、运行/结果/审计页签、只读哈希区、原生趋势图和统计表。概念稿中的模型名、日期、样本数与效果值都是占位符；应用只显示 API 返回的审计真实值。人工冻结步骤已移除，点击“开始实验”时自动生成运行快照；正式结果仍在报告锁定前密封。

`qa/` 中的概念实现截图保留为历史视觉基准，其中早期画面可能仍出现人工冻结文案，不再作为当前操作说明。2026-08-30 的最新浏览器验收确认：Runner、Rubric 和项目页均无冻结按钮；草稿项目可直接进入“开始实验”；390 px 视口页面宽度与视口一致；新页面上下文无控制台错误或失败 API 响应。QA 项目只使用临时数据库和虚构配置，未点击“开始实验”、未启动 MCP worker，也未发生模型评分调用。
