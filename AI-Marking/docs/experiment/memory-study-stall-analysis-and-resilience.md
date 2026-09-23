# 实验中途停止原因分析与持续运行方案

对象：SAF 2.0 Luna-only 记忆研究平台 `/memory-study`（4 槽 worker）。
分析依据：`ai_marking_experiment` 数据库实际数据（研究 `f3460bca`，含 4.13 分片）、
`ms_scheduler_runtime` 运行时行、`ms_call_attempts` 账本、macOS 电源日志、代码走读。
分析日期：2026-09-18（时间均为本地时间 CST；数据库内为 UTC）。

## 一、现象还原（以 4.13 所在正式研究 f3460bca 为例）

| 本地时间 | 事件 | 证据 |
| --- | --- | --- |
| 09-17 15:37 | 研究创建，六题分片 materialise，`feedback_waves=["full","no_feedback"]` | `ms_studies.created_at` |
| 09-17 15:57 起 | 4.13 的 full 波次训练写入/评分正常推进 | `ms_memory_stores.updated_at` |
| 09-18 13:56:47 | **最后一次调用完成**（8.2_MM 的 mem0_full training score）；同时两个在途调用（123766、132325）从此再无消息 | `ms_calls.completed_at`、attempts 表 |
| 13:56 之后 | **4 个槽全部不再领取任何调用**，但心跳线程继续每 10 秒续租 | 最近 1 小时 started_1h=0；`ms_scheduler_runtime.heartbeat_at` 持续更新 |
| 14:49 | 5 个未完成分片被置为 `paused`（人工通过 UI 操作） | `ms_question_runs.updated_at` |
| 14:58:11 | 最后一次心跳 | `ms_scheduler_runtime` |
| ~15:00–15:26 之间 | API 进程彻底消失（无 uvicorn/python 进程、无端口监听），租约再无人续 | `ps`、`lsof` 检查 |

运行时行中残留的状态：4 个 slot 里 3 个永远停在 `state="starting"`、
1 个 `idle`，对应"槽线程已创建但从未/不再领取调用"。

## 二、根因分析

按因果层级排列，"运行一段时间就停止工作"不是一个原因，而是三层缺陷叠加：

### 1. 进程级：worker 是 API 进程内的守护线程，没有监督者（主因）

- `MemoryStudyWorkerManager`（`app/experiment/memory_study/manager.py`）用
  `daemon=True` 的 Python 线程在 **uvicorn API 进程内**跑整个实验。
- `start.prod.sh` 用多 worker 部署 uvicorn 时，实验线程只存在于其中一个
  进程；`start.sh` 走 `app.dev_supervisor`，仅监督"API 退出/源码变更"，
  **不监督实验线程**。
- 任何原因导致 API 进程退出（崩溃、断电、终端 SIGHUP、热重载、误 Ctrl+C、
  macOS 更新 Electron 触发的重启等），实验就整体蒸发。数据库里 123766/
  132325 两个调用留下的 `lease_expired` 尝试行正是"进程死亡 → 租约无人续 →
  被 sweep 回收"的化石证据。
- 电源日志显示 9-17 至 9-18 凌晨机器多次 `Idle Sleep / Clamshell Sleep`，
  13:54 还插拔过电源。睡眠唤醒、登出、终端关闭都可能终结后台 API 进程；
  由于心跳依赖真实时间推进，长睡眠同样会打断租约链。

### 2. 线程级：槽线程静默死亡无人重建（本次 13:56 停摆的直接原因）

- 13:56:47 之后整整 62 分钟，进程活着（心跳在跳、API 能响应 14:49 的
  分片暂停操作），但 4 个槽零调用。
- `MemoryStudyWorker.run()` 里 `ThreadPoolExecutor` 的任何一个 slot
  future 抛出未捕获异常时，主循环只是 `stop_event.set(); raise`，把异常交给
  `manager._run()` 记一条 `attention_required`。但如果异常发生在
  **`_run_slot` 自己捕获范围之外的路径**（例如 `_finalize_study`、心跳竞争、
  递归 requeue 中的并发问题），或者槽线程卡在永不返回的 IO 上，就会出现
  "心跳线程还在跑、槽线程已全灭"的僵尸运行态。
- 管理器与 worker 之间没有任何"槽存活性"核对：`_HeartbeatThread` 只续
  `owner_id` 的租约，不检查 4 个 slot 是否真的在领活。UI 因此显示
  "在线"，用户看到的就是"运行中但不再产出"。

### 3. 调度级：设计上就有多个"合法但致命"的空转陷阱

即使进程和线程都活着，以下门禁条件也会让槽永远领不到调用：

- **full→no_feedback 波次串行门禁**：`claim_next_call()` 里
  `condition.endswith("_no_feedback")` 的调用要求同 model 的
  `BASE_SLOT_CONDITIONS` 全部 absent（pending/leased/running 均无）。
  该检查覆盖**全研究所有分片**，因此只要任何一道题（如 4.2、5.11）还剩
  一条 full 波次调用，所有 no_feedback 槽就持续空转。
- **测试评分门禁** `_training_complete_for_call()`：test score 要求该
  model/question 的**所有** store `completed`。4.13 的 90 条 full test
  score 因 store 已 completed 而可领取，但如果任一 store 失败，其对应
  流的所有 test score 永久 pending。
- **失败阻塞**：同一 model/condition/order 流内 `id <= 当前 id` 的
  failed/leased/running 调用阻塞后续领取（设计如此，用于保序）。
- 这些条件组合出的静默死锁没有看门狗：没有任何组件在"pending>0 且
  连续 N 分钟零领取"时报警。

### 4. 次要放大器

- 每次 attempt 有自动重试上限（`MAX_AUTOMATIC_ATTEMPTS=3`），终态失败
  会把分片置 `attention_required`/`paused`，需要人工重试——网络抖动
  集中出现时实验自动大面积停下。
- `LEASE_SECONDS=600` 是固定值；memory 写入（Mem0/A-MEM 走多阶段 codex
  子进程 + 快照发布）单次耗时超过 10 分钟时，租约过期会让其他进程的
  sweep 误回收在途调用。

## 三、"一直运行到结束"的设计方案

目标：实验一旦启动，除非人工终止，平台必须自愈到 `completed` 或
`attention_required`（只允许数据级失败需人工决策，不允许基础设施级停止）。

### A. 进程与执行模型：把"长跑进程"和"交互 API"分离（核心）

1. **独立执行进程 + OS 级拉活**
   - 保留现有 `MemoryStudyWorker` 全部租约/领取逻辑，但改由
     `scripts/run_memory_study.py` 以**独立常驻进程**运行（它已是可用入口，
     `worker.run()` 对此无假设）。
   - 新增 `start.sh`/`start.prod.sh` 内的监督段：用 `while true; do
     python scripts/run_memory_study.py --study-id ...; sleep 5; done` 式
     的 OS 级循环（或 `launchd` plist + `KeepAlive=true`）拉活执行进程。
     进程死了 5 秒内自动重启，租约过期回收机制保证新进程接续。
2. **API 内嵌 worker 保留但降级为兜底**：API 仍允许"网页启动"，
   但启动前检查 `ms_scheduler_runtime`——若已有活租约则不再另起。
   去掉 `daemon=True`，改为非 daemon + 优雅关闭钩子，避免解释器退出时
   半途掐断。
3. **执行进程自动认领（auto-adopt）**：`run_memory_study.py` 增加参数
   `--study-id auto`：查询所有 `status='running'` 且没有活租约的研究，
   逐个恢复。这样 OS 监督循环只需要一条固定命令，无需人工传 ID。
4. **可选：机器睡眠豁免**：实验运行期间通过 `IOPMAssertion`
   （`caffeinate -i` 包裹执行进程即可）阻止空闲睡眠，在 README 说明
   合盖行为仍受硬件限制。

### B. 线程与槽级：自愈的槽循环 + 存活对账

1. **槽线程永不退出**：`_run_slot` 外层包一层
   `while not stop_event: try: ... except Exception: log; sleep(1); continue`
   ——任何领取/执行异常只作废当前调用（回队），槽线程必须继续转。
   递归上限类 bug（`RecursionError`）同理吞掉并记录。
2. **主循环重建死槽**：`run()` 不再让单槽异常终止全局；改为周期
   （如每 30 秒）核对每个 slot 的 `last_heartbeat`/`state`，发现槽线程
   消失（thread not alive）就重新 `pool.submit` 一个新槽线程。
3. **心跳与存活解耦**：`_HeartbeatThread` 续租前检查
   `any(thread.is_alive() for slot threads)`；若全部槽死亡且重建失败，
   主动 `_release_runtime()` 并退出进程，交给 OS 监督循环重启——
   宁可干净退出，不留僵尸租约。
4. **空转看门狗**：worker 内维护"最近一次成功领取时间"；当
   存在 pending 调用、研究仍 running、但连续 `STALL_THRESHOLD`
   （建议 5 分钟）零领取时：
   - 记录诊断快照（各门禁计数：no_feedback 波次阻塞数、test 门禁
     阻塞数、failed 阻塞数）到 `error_summary`/日志；
   - 若全部 pending 均被 no_feedback 波次门禁阻塞 → 这是正常串行等待，
     输出"等待 full 波次完成"状态而非告警；
   - 若是 failed 阻塞 → 自动转 `attention_required`（或见 C-3 自动重试）。

### C. 调度级：消除静默空转与人工卡点

1. **no_feedback 波次门禁细化到"本分片"**：把
   `claim_next_call()` 中 full 波次 pending 检查加上
   `MSCall.question_id == 当前分片`，让 4.13 的 no_feedback 波次不必等
   其他 5 题的 full 波次（波次语义按"流内顺序"保留：同 store 的
   full 写完才写 no_feedback）。
2. **自动重试预算放大**：`runner_transient/framework_transient` 类
   失败的自动重试从"每逻辑调用 3 次"改为"每调用 3 次 + 全局退避重试"
   ——失败调用回队为 pending（带 `retry_after` 时间戳，指数退避至
   上限如 30 分钟），只有 `authentication_error`、`schema_error`、
   `snapshot_validation_error` 等永久码才转人工。这样一夜的网络抖动
   不再把 5 个分片全部打停。
3. **失败自动恢复任务**：新增周期任务（执行进程内即可）：发现
   `status='pending'` 且 `retry_after <= now` 的失败调用自动重新入队；
   分片因 transient 失败进入 `attention_required` 后，若全部失败调用
   已被上述机制回队，则自动恢复为 `running`。保留人工重试入口不变。
4. **租约自适应**：memory_write 的租约改为
   `max(LEASE_SECONDS, 上一次同流耗时 × 2)`，或把长框架调用期间
   （参考 `_HeartbeatThread` 已有的机制）同时续调用级租约，杜绝
   "慢调用被误回收"。

### D. 可观测性：让"停止"在 1 分钟内可见

1. **进度停滞指标**：运行台轮询接口已返回 slots/progress；新增
   `stalled_seconds`（now - max(succeeded.completed_at)）与
   `blocked_reason` 字段，前端在停滞 >5 分钟时高亮显示原因
   （等待波次/等待重试/等待人工）。
2. **完成通知**：研究进入终态（completed/attention_required/failed）
   时，执行进程可选地发送 macOS 通知（`osascript -e 'display
   notification ...'`），研究者不必一直盯页面。
3. **审计日志落盘**：执行进程把 worker 关键事件（启动、领取、失败码、
   自愈动作）写入 `backend/logs/memory-study-<study>.log`，崩溃后可
   事后取证（本次分析若非数据库化石证据完整，将无法定位）。

### 实施优先级

| 序 | 改动 | 位置 | 收益 |
| --- | --- | --- | --- |
| 1 | OS 级监督循环 + `--study-id auto` 自动认领 | `start.sh`、`scripts/run_memory_study.py` | 进程死亡 5 秒自愈，彻底解决主因 |
| 2 | 槽线程永不退出 + 主循环重建死槽 | `worker.py::_run_slot/run` | 消除"心跳活着、槽全灭"僵尸态 |
| 3 | 空转看门狗 + blocked_reason 可观测 | `worker.py`、API、前端 | 任何停滞 5 分钟内可见且可解释 |
| 4 | transient 失败指数退避自动重试 | `worker.py::_execute_call_strict`、新增 sweep | 夜间网络抖动不再停实验 |
| 5 | no_feedback 门禁细化到分片 | `worker.py::claim_next_call` | 分片间不再互相阻塞 |
| 6 | 租约自适应 + 通知 + 日志 | `worker.py`、执行进程 | 长调用不再被误回收 |

方案 1、2、6 不改变任何实验语义（请求顺序、波次、账本结构），
方案 4、5 只放宽"调度时机"不影响测量结果；均不触碰冻结配置与
统计口径。按此实施后，实验可长时间无人值守运行至 `completed`；
仅数据级失败（需人工判断的 schema/鉴权/快照损坏）才会停下来等决策。
