# AI-Marking 实验平台

当前默认（也是唯一）入口是 SAF 2.0 Luna-only 记忆研究平台 `/memory-study`：在同一份历史人工批改
材料上比较普通案例检索、Mem0、A-MEM 与无记忆基线。平台使用独立的 `ms_*` 表、数据清单、
记忆库、调用账本与描述性报告。模型配置页为 `/memory-study/config`。

新项目默认使用 `saf-memory-framework-v3-r2`（固定六题和独立评分仪器）；
`saf-memory-framework-v3` 仅作为历史基线保留。两种协议的数据和报告不混合。

旧的执行链已退役：统一实验平台（`/experiments`）、r20–r23 API、旧 worker、旧模板和旧脚本均已清理。数据库记录、Alembic 历史与 `backups/r20-r23-*` 归档仍保留；新运行只通过 `/memory-study`。

## 最短工作流

1. 运行 `./start.sh`，打开 `http://127.0.0.1:5173/memory-study`。
2. 在「数据清洗与抽样审计」确认固定 SAF 归档通过哈希与角色门禁；Embedding revision
   必须配置为不可变值，冻结前平台会拒绝启动。
3. 选择规模（开发题 / 试点题 / 正式六题）、为 Luna 绑定一个速度模式，勾选
   受限数据处理授权后创建研究。创建只生成调用计划，不启动模型、不产生费用。
4. 点击「冻结项目」。冻结会锁定 Luna 的 reasoning、速度、超时、Embedding 版本和运行时配置；站点配置之后的修改只影响新研究。
5. 在运行台点击「运行预检」。Luna 产生一次少量真实结构化调用，验证登录、模型、速度模式、严格 Schema、输出解析和 CLI 指纹；通过后才能开始运行。
6. 在六个题目卡片中按需启动任意题目。后端以 Luna 四个条件流组成最多 4 个固定槽；浏览器不会启动本机进程。
7. 在「运行监控」查看题目分片、4 槽、模型级训练/评分门禁、重试倒计时和 ETA；终态后页面停止轮询。
8. 完成后运行「完整性审计」解封结果；如仍有失败调用，用「重试全部失败」批量重新入队，
   或进入「批改明细」逐条重试。

## 执行器与失败处理

- 实验默认由 `start.sh` 内的**独立执行器监督循环**运行：`scripts/run_memory_study.py
  --study-id auto` 自动认领所有处于 running 且没有活租约的研究；执行进程意外退出
  后 5 秒内自动重启，从数据库租约回收点接续，无需人工干预。API 进程内的网页启动
  仍然可用，但执行不再依赖 API 进程存活。
- 4 槽 Luna worker 用 `ms_scheduler_runtime` 租约做跨进程互斥；心跳每 10 秒续租，
  并同步续租在途调用的租约（长框架调用不会被误回收）。槽线程捕获自身异常后继续
  服务其他分片；主循环每 30 秒对账并重建意外死亡的槽线程。
- 每个题目×模型×条件×顺序流严格串行；分片级 no_feedback 波次门禁只等待同一分片
  的 full 波次，分片之间互不阻塞；full 波次测试评分屏障只看相同 feedback_mode
  的 store。
- 408/429、5xx、连接中断等瞬态错误先按每次调用三次快速重试（退避 2 秒、4 秒）；
  快速重试耗尽后转为**指数退避自动重新排队**（1/2/4 分钟起步，封顶 30 分钟），
  到期自动重试，不再把分片打入 `attention_required`。鉴权、Schema、模型/依赖
  配置、快照校验等永久错误仍立即终态并要求人工重试（人工重试不受自动次数限制，
  保留完整 attempt 与 input hash）。
- 运行台 runtime 接口返回 `stalled_seconds` 与 `blocked_reason`：pending 存在但
  超过 5 分钟无调用完成时，页面直接给出等待原因（退避中/等待人工/等待波次）。
- 每次 attempt 保留 stderr、失败码、token、延迟、内部 invocation 和 CLI 账本。
  memory 写入先在临时目录完成，成功后原子提交；失败或崩溃自动丢弃临时目录。
- 暂停不领取新调用并等待当前调用收尾；终止会中断活动 Codex 子进程并取消未开始
  调用。全部调用结束后若仍有 failed：研究进入 `attention_required`，否则自动置
  为 `completed`。

若网页后端重启，可用
`backend/.venv/bin/python backend/scripts/run_memory_study.py --study-id <ID>` 恢复运行。

无法证明某个 memory store 的 snapshot/native artifact 哈希时，在详情页点击「校验并恢复 memory store」；系统会从最后提交点继续，校验失败则清空该 store 并重新排队全部训练写入。对账命令默认只读：

```bash
cd backend
.venv/bin/python scripts/audit_memory_study_artifacts.py --json
# 仅在明确需要时清理数据库无归属的顶层目录：
.venv/bin/python scripts/audit_memory_study_artifacts.py --purge-orphans --json
```

Embedding API Key 仅保存在服务端的冻结运行配置中，不进入预检审计、memory 快照或导出
manifest；实际模型调用由已登录的 Codex CLI 通过全新 `codex exec --ephemeral` 会话完成。
共享的 CLI runner 与冻结 tokenizer 位于 `backend/app/experiment/common/`。

研究方案见 [`docs/experiment/saf-memory-framework-design.md`](docs/experiment/saf-memory-framework-design.md)；
运行、恢复、审计与导出手册见 [`docs/experiment/saf-memory-framework-runbook.md`](docs/experiment/saf-memory-framework-runbook.md)。
