# SAF 2.0 Luna-only 记忆框架运行手册

本手册默认用于 `saf-memory-framework-v3-r2`。原 `saf-memory-framework-v3` 仅作历史项目
读取与分析；新建项目、冻结、评分和报告都必须保持协议 ID 隔离。V3-r2 固定六题、40/10
训练测试划分、V4 的两种历史顺序（原始顺序与 seed=42 随机顺序）、top-5 检索和连续分数上下限。

## 入口

- 页面：`/memory-study`
- API：`/api/memory-study`
- worker：由项目详情页任一题目卡片的“启动/恢复”自动启动；命令行恢复入口为
  `cd AI-Marking/backend && .venv/bin/python scripts/run_memory_study.py --study-id <ID>`
- 迁移：`cd AI-Marking/backend && .venv/bin/alembic upgrade head`

## 启动前门禁

1. `GET /api/memory-study/audit?kind=formal` 必须通过归档哈希、互斥角色、40/10 抽样和训练满分检查。
2. 设置固定的 `MEMORY_STUDY_EMBEDDING_REVISION`；默认 `unresolved` 会阻止冻结。
3. 按 `backend/requirements-memory-study.txt` 安装并验证固定提交的 Mem0 与 A-MEM、Chroma 和 A-MEM 依赖。为普通检索、Mem0 与 A-MEM 配置同一个 OpenAI-compatible embedding API 的模型、端点、密钥和不可变版本标识；本实验禁止本地 embedding。Mem0 入口必须是 `mem0.Memory.from_config()`；A-MEM 入口必须是 `agentic_memory.memory_system.AgenticMemorySystem`。适配器缺失或接口不匹配时进入 `framework_unavailable`，平台不自动降级为普通检索。
4. 确认 Codex CLI、Luna 登录状态和四槽运行资源。Luna 有无记忆、确定性检索、Mem0、A-MEM 四个固定条件槽；同一题目×模型×memory store 严格串行，题目与条件之间并行，最多四个活跃调用。
5. 可直接创建试点或正式六题项目；开发题是可选的工程诊断阶段，试点不再是正式项目的前置条件。创建项目、冻结配置不会触发模型调用；冻结后必须点击项目详情页的“运行预检”，执行一次 Luna 最小真实结构化输出，验证登录、模型、速度模式、严格 Schema、解析和 CLI 指纹。通过后，六个题目卡片可按任意顺序启动，才会唤醒 `MemoryStudyWorker`。若网页后端重启，可使用命令行入口恢复，worker 会再次验证预检新鲜度。

## 状态与恢复

`ready → frozen → running → completed` 是正常路径；`paused` 和 `attention_required` 可人工恢复；`terminated` 为终态。每个模型按其 `MSCall.id` 严格串行；一个模型的全部 training store 提交后即可开放该模型评分，不等待另一模型。worker 重启会重用已经成功的 call、attempt 和文件 snapshot；失败只能通过 retry 重新入队，不能覆盖成功结果。若同一模型历史账本出现多个执行槽位，审计会判定为非串行，项目不得继续 resume，必须新建项目。

单次调用先写入完整 attempt。408/429、5xx、网络中断、临时 DNS/传输错误、网络或进程超时等瞬态错误自动重试最多两次（共三次，退避 2 秒、4 秒并带少量抖动）；Schema、鉴权、依赖、配置、快照、输出验证和未知错误立即终止当前题目。单题失败不阻塞其他题目；后续调用保持 `pending`，不会被跳过。人工可在题目卡片或批改明细逐条重试，或用 `POST /api/memory-study/projects/{id}/questions/{question_id}/retry-failed` 重新入队。

暂停只停止领取新调用并等待当前调用安全收尾；终止会中断活动 Codex 子进程，记录 `interrupted` attempt，并取消未开始调用。

所有关键目录都位于 `MEMORY_STUDY_ARTIFACT_ROOT/<study-id>/`：embedding 缓存和每个 store 的原子 `snapshot.json` 分开保存；每次 memory attempt 从最后成功快照复制出独立临时工作区，成功后原子提升，失败或崩溃目录自动回收。Mem0 同时保存独立 Chroma 目录与 history DB，A-MEM 保存 `MemoryNote` 清单和索引重建信息；`native-artifacts` 是已提交的 Mem0 侧车，只有带唯一后缀的 `native-*` 构造暂存目录会在重启时清理。数据库 `ms_*` 记录状态、调用、attempt、租约、token、延迟、worker、slot、快照原生 artifact 哈希和 `ms_framework_invocations` 内部调用账本。无法证明 snapshot 或 native artifact 哈希时，详情页恢复动作会清空该 store、重排全部训练写入，禁止在残留状态上叠加。

## 串行与并发

`MSSchedulerRuntime` 记录全局 owner、心跳和 4 个固定 Luna×条件槽。心跳线程每 10 秒续租，长调用期间不会出现租约过期导致的“未连接”假象；每个槽按所属题目×模型×条件×顺序的 `MSCall.id` 冻结创建顺序扫描，并使用 `SELECT … FOR UPDATE` 保证同一流同一时刻只有一个调用。租约过期（worker 崩溃或 API 热重载遗留）的调用会自动回收为 `pending`，但仍然挡住所属流后续调用，直到它成功。任一题目分片终态失败只暂停该题，不触发全局停机。

## 审计与导出

运行结束后在项目详情点击“完整性审计”。只有所有调用终态、失败为空、每次成功评分差值完整、测试未写回记忆且协议配置未出现 token 上限/检查点/bootstrap 时，结果才解封。可导出 manifest JSON；审计通过后再导出结果 CSV、报告 JSON 和 HTML。默认导出不含学生正文、完整 prompt、原始框架秘密或密钥。V3-r2 的参考答案只作为当前评分上下文；历史记忆只投影学生答案、教师分数和可选教师反馈，条件标签与检索元数据不进入评分请求。对账命令 `python scripts/audit_memory_study_artifacts.py --json` 默认只读，报告数据库缺失目录、磁盘孤儿目录、快照哈希异常和磁盘占用；仅显式加入 `--purge-orphans` 才清理孤儿顶层目录。

### 单题中期草稿导出（只读）

六题可任意顺序启动；某题分片达到 `completed` 后即可用只读脚本把该题结果整理成本地文件，供 Codex 等编程助手直接读取分析。脚本不写数据库、不产生审计事件、不受 `results_embargoed` 影响（研究级导出的密封策略不变）：

```bash
cd AI-Marking/backend
.venv/bin/python scripts/export_memory_study_question.py --study-id <ID>                    # 全部 completed 分片
.venv/bin/python scripts/export_memory_study_question.py --study-id <ID> --question-id q1   # 单题
.venv/bin/python scripts/export_memory_study_question.py --study-id <ID> --format json      # 机器可读（默认 html；另有 csv）
```

默认写入 `backend/outputs/memory-study-exports/<study-id>/<question_id>-interim-report.html`。HTML/JSON 包含该题模型×条件描述统计、train/test 分离、模型对比、复评稳定性、失败列表与逐条评分明细；CSV 仅逐条明细。输出顶部（HTML 横幅与 JSON statements）标注：单题中期草稿，正式结论以完整性审计通过后的全量六题报告为准。分片未完成、题目未知或无成功评分时脚本报错退出，不产出文件。

## 验收命令

```bash
cd AI-Marking/backend
.venv/bin/ruff check app tests
.venv/bin/pytest -q tests/experiment/memory_study
.venv/bin/alembic heads

cd ../frontend
npm run lint
npm run test:run
npm run build
```

若本机未缓存 `o200k_base`，诊断 token 统计使用确定性有界 fallback；真实预检仍会校验 CLI 与运行时指纹。

开发题和试点题都只生成 `original`、每条测试回答一次评分，即预期 `70` 次评分、`160` 次官方框架写入；正式基础 wave 预期 `2,400/1,440`（评分/记忆写入），含无反馈 wave 时为 `4,200/2,880`。开发题和试点均可跳过。真实运行前还要记录 `MEMORY_STUDY_EMBEDDING_REVISION`、官方依赖版本、资源、额度与预检费用；如运行 pilot，结束后记录串行基线与四槽 p50/p95 延迟、token、失败率、峰值磁盘、墙钟和临时目录回收情况。预检产生一次 Luna 真实调用，计入额度但不计入研究 `MSCall` 规模。

## 资源预算

- CPU：建议至少 4 vCPU；最多 4 个 Luna 条件槽可并行，每个题目×模型×条件流内部始终串行。
- 内存：平台 Python 进程目标低于 1 GB；进程内向量缓存有界（最多 4,096 个向量），官方框架/CLI 的外部峰值需在 pilot 中单独记录。
- 磁盘：按 `embedding cache + 所有已提交 native snapshot + 2 × 最大单 store attempt` 预留；attempt 成功、失败、重启后都应回收，实际占用以 `audit_memory_study_artifacts.py` 的 `disk_bytes` 和峰值监控为准。
- 墙钟：四槽运行目标不超过串行基线的 110%；预检是一次额外的 Luna 真实结构化调用，应单独计入额度和墙钟记录。
