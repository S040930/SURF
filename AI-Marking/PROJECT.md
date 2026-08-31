# AI-Marking：r23 DREsS_CASE 实验平台

默认协议为 `r23-dress-case-rubric-sensitivity-2026-08-v1`：使用 DREsS_CASE 检验单个 Codex 模型配置的 rubric sensitivity。r20–r22 数据、表、路由和 `/research` 历史入口保持不变。

## 架构索引

- r23 数据、协议、统计与执行：`backend/app/experiment/r23/`
- 独立持久化：`backend/app/models/r23.py`，仅使用 `r23_*` 表
- 领域服务与 REST：`backend/app/services/r23.py`、`backend/app/api/r23_platform.py`
- MCP 串行 worker：`backend/app/mcp_server.py`；旧协议与 r23 按逻辑调用轮转，始终只有一个 Codex 子进程
- r23 前端：`/dress`、`/dress/:projectId`、`/dress/rubric`、`/dress/runners`、`/dress/data`
- r22 历史前端：`/research`，既有 API 不变

## 文档

- [`docs/experiment/r23-dress-case-rubric-sensitivity.md`](docs/experiment/r23-dress-case-rubric-sensitivity.md)：RQ、假设、材料、抽样、统计、效度、引用与结论门槛
- [`docs/experiment/r23-platform-runbook.md`](docs/experiment/r23-platform-runbook.md)：架构、操作、API、数据安全、资源预算、迁移与验收
- [`docs/experiment/r21-memory-limits.md`](docs/experiment/r21-memory-limits.md)：历史 r21 记忆限制

## 核心决策

- 数据哈希、seed `20260830`、样本量、单个 Runner 和相同 Rubric 由协议控制；Runner 可选择 `low`、`medium` 或 `high` 思考程度、`standard` 或 `fast` 速度模式，并设置 30–1800 秒超时（默认 120 秒）。速度模式进入配置哈希、Manifest、attempt 和报告；点击“开始实验”时自动保存不可变运行快照，不设置人工冻结步骤。
- 已完成项目的“再次运行”会创建独立重复项目，复用 Runner、Rubric、项目类型和确定性样本，不覆盖既有报告或哈希。
- TSV 流式两遍读取，只物化抽中作文；正文与完整 prompt 不进入仓库、日志、异常或公开导出。
- 分数以 x2 整数 2–10 存储；模型只能返回 Content、Organization、Language 三个九值字段。
- 没有静默重试。失败进入 `attention_required`，人工重试保留原 attempt 与 input hash。
- 技术试点永不显示分数；正式结果在统计报告与 SVG 哈希锁定前密封。
- QWK 只能称为 CASE 预设标签恢复，不代表人工评分一致性或真实作文准确性。

## 运行与验证

要求 Python 3.12、Node.js 22、PostgreSQL 和已登录 Codex CLI。生产/开发依赖分别精确锁定在 `backend/requirements-prod.txt` 与 `backend/requirements-dev.txt`。

```bash
./start.sh       # 网页与管理 API
./start.mcp.sh   # Codex App 信任项目后托管串行 worker
```

数据审计目标低于 2 分钟、内存低于 256 MB；单配置正式串行运行预算约 22–65 小时，试点后用实测 p50/p95 重算；本地数据库与有界审计日志目标低于 1 GB。

```bash
cd backend && .venv/bin/black --check app tests && .venv/bin/ruff check app tests && .venv/bin/python -m pytest -q
cd ../frontend && npm run lint && npm test -- --run && npm run build
```
