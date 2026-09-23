# AI-Marking：SAF 2.0 记忆研究平台

当前研究主线是 SAF 2.0 短答题记忆辅助批改：V3 保留为历史基线，V3-r2 是默认的新协议。
更早的 DREsS、r20–r23 实验均为只读历史，不参与新的记忆研究结论。

## 架构索引

- 记忆研究执行器：`backend/app/experiment/memory_study/worker.py`（保留 V3 历史基线并默认运行 V3-r2；Luna 最多 4 槽；每个题目×模型×条件流严格串行，进程内线程并行，直接调用本地 Codex CLI）与 `manager.py`
- 独立持久化：`backend/app/models/memory_study.py`，仅使用 `ms_*` 表
- 领域服务与 REST：`backend/app/services/memory_study.py`、`backend/app/api/memory_study.py`
- 共享执行基础：`backend/app/experiment/common/`（`CodexExecRunner` 与冻结 tokenizer），供所有协议复用
- 网页仅保留 SAF 2.0 Luna-only 研究平台 `/memory-study` 与模型配置 `/memory-study/config`（SAF 模型配置）；r20/r21/r22/r23 及统一实验平台的历史入口已从网页收敛（数据仍在库与归档 `backups/r20-r23-*` 中）
- 旧执行链（`mcp_server.py`、`scripts/run_worker.py`、`start.mcp.sh`、r20–r23 与 exp_ 的 API/服务/执行器/模板）已清理；数据库记录与 Alembic 历史保留，`/memory-study` 是系统唯一的实验执行链。

## 文档

- [`docs/experiment/saf-memory-framework-design.md`](docs/experiment/saf-memory-framework-design.md)：V3-r2 固定六题、评分仪器、队列、审计与资源预算
- [`docs/experiment/saf-memory-framework-runbook.md`](docs/experiment/saf-memory-framework-runbook.md)：`/memory-study` 六题分片、4 槽 Luna-only 运行、恢复、审计与导出手册

## 核心决策

- 数据哈希、样本量、V3/V3-r2 协议、评分仪器和 4 个固定 Luna 条件槽由协议控制；Runner 可选择 `low`、`medium` 或 `high` 思考程度、`standard` 或 `fast` 速度模式，并设置 1–3600 秒超时（默认 120 秒）。项目创建/冻结时保存不可变运行时快照，站点修改只影响新研究。
- TSV 流式两遍读取，只物化抽中作文；正文与完整 prompt 不进入仓库、日志、异常或公开导出。
- SAF 分数按题目原始分数类型存储；V3-r2 允许连续小数，但服务端拒绝超出冻结题目上下限的结果。
- 冻结后必须为 Luna 执行一次真实结构化预检；结果、延迟、CLI 指纹与哈希进入审计，配置或 CLI 漂移会使预检失效。429、5xx、网络中断、超时最多自动三次（2s/4s + 抖动），Schema、鉴权、依赖、快照和未知错误立即停止。
- 六个题目是独立分片，每题可单独启动/暂停/终止/人工重试。Luna 最多 4 个固定槽；同一题目×模型×memory store×feedback wave 严格串行，训练评分和 40 条历史 memory 写入完成后开放该题测试。每个 attempt 使用独立临时工作区并原子提升 snapshot；失败、崩溃或终止不会污染上一提交点。无法验证快照时整条 store 清空重建，禁止残留叠加。
- 正式结果在完整性审计和报告哈希锁定前密封；开发题和试点只作工程诊断。
- SAF 记忆研究使用独立 `ms_*` 表、协议和 `/memory-study` 页面；默认仅开放数据审计，Embedding revision 固定后才能冻结。
- SAF 记忆研究可直接创建试点或正式六题项目；开发题和试点均为可选诊断，不阻塞正式运行。
- `backend/scripts/audit_memory_study_artifacts.py` 默认只读对账；只有显式 `--purge-orphans` 才清理没有数据库归属的顶层目录。

## 运行与验证

要求 Python 3.12、Node.js 22、PostgreSQL 和已登录 Codex CLI。生产/开发依赖分别精确锁定在 `backend/requirements-prod.txt` 与 `backend/requirements-dev.txt`。

```bash
./start.sh       # 网页与管理 API；实验执行器由独立监督循环启动（退出后 5 秒自动重启）
```

### 正式实验防休眠

- `start.sh` 已用 `caffeinate -is` 防系统空闲休眠，但 `-s` 仅在接通电源（AC）时有效；合盖且无外接显示器时 macOS 必然休眠，代码无法阻止。
- 跑正式实验时：接入电源、避免合盖（或外接显示器+电源）。本机当前 `pmset sleep=1`（空闲 1 分钟即睡）；如常离人运行可考虑 `sudo pmset -a sleep 0`（权衡：忘记改回会增加耗电）。
- 休眠证据排查：worker 日志出现 `heartbeat gap ...s; system likely slept` 即为整机休眠事件，期间 codex 连接会被掐断并在唤醒后自动重试。
- 执行器崩溃/被杀后由监督循环 5 秒内重启，租约持有者 PID 存活检测使接管窗口约 35 秒内完成（无需等 600 秒租约过期）；期间 UI 显示"执行器自动恢复中"而非"未连接"，唤醒后无需人工干预。

资源预算按四个固定槽计算：建议至少 4 vCPU（实际活动子进程上限 4），平台 Python
进程与有界向量缓存目标低于 1 GB 内存；磁盘需求按
`embedding cache + 已提交 native snapshot + 2 × 最大单 store attempt` 预留，运行中由
artifact 对账报告实际峰值。4 槽墙钟时间目标不超过串行基线的 110%；预检产生一次 Luna
少量调用，正式运行须由 pilot 记录 p50/p95、token、失败率、峰值磁盘和墙钟时间。
数据审计目标低于 2 分钟，临时 attempt 目录在成功、失败、重启后均应回收。

```bash
cd backend && .venv/bin/black --check app tests && .venv/bin/ruff check app tests && .venv/bin/python -m pytest -q
cd ../frontend && npm run lint && npm test -- --run && npm run build
```
