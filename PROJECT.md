# SURF：AI-Marking 实验工作区

## 当前入口

当前支持 `saf-memory-framework-v3`（历史基线）与
`saf-memory-framework-v3-r2`（当前默认协议）；执行链共用网页 `/memory-study`
与 API `/api/memory-study`，执行器为 `backend/app/experiment/memory_study/worker.py` 的 Luna 4 槽 worker；同一题目×模型×条件流严格串行，题目与条件之间并行。

V3-r2 固定采用 `12.2_PE`、`10.2_TC`、`8.2_MM`、`4.13`、`8.1_MM`、`4.3` 六道正式题，
检验普通完整案例检索、Mem0、A-MEM 是否相对无记忆降低新回答评分的归一化绝对误差；去掉人工
文字反馈后误差与资源开销如何变化。每题 40 条训练记录、10 条 `unseen_answers` 测试记录，
训练阶段照常评分并构建记忆。

- 应用入口：[AI-Marking](AI-Marking/PROJECT.md)
- 主比较：Retrieval / Mem0 / A-MEM 相对 `no_memory` 的答案级配对记忆增益；测试 NAE 先在答案内平均 V4 的 `original` 与 `shuffled` 顺序，再对六题等权。训练阶段逐答案 NAE 仅作记忆形成轨迹诊断。
- V3-r2 评分请求固定包含题目、参考答案、学生答案、题目级 score floor/ceiling 和 memory；连续分数越界由服务端拒绝，不四舍五入或裁剪。
- 研究边界：SAF 2.0 官方归档、Luna-only、六题分片正式运行；只主张已见题目的新答案泛化，不作跨模型比较。
- 记忆边界：历史记忆只向评分模型投影学生答案、教师分数和可选教师反馈；condition、ID、相似度、rank、原始框架 payload、测试人工标签和 `verification_feedback` 只保留在审计记录中。参考答案只用于当前评分，不写入记忆。
- 平台边界：无 token 上限、无检查点、无 bootstrap；分析为描述性、探索性，不做显著性检验。冻结后必须通过 Luna 真实结构化预检，站点配置不会动态覆盖运行中快照。

### 只读历史

以下协议已退役，其 API、服务与执行器均已从代码中移除，产物只作历史审计，**不得作为当前结论的依据**：

- `r20`（CRM vs ARM 边界研究）
- r21 / r22 / r23（含 r23 DREsS_CASE rubric sensitivity）
- r16 / r19 及 `exp_` 前缀的统一实验平台

旧 r20–r23 运行模块、模型/schema、模板、脚本与 QA 文件已按清单移除；数据库记录、Alembic 历史与备份保留。
网页面向用户的入口已收敛为 `/memory-study` 与 `/memory-study/config`。

DREsS_New 论文工作区产物（`outputs/exp_dress_new_paper_revision_0914/`、`outputs/exp_dress_new_paper_closeout_0913/`、
`docs/dress-new-argument-blueprint.md`）同样已移除，且**从未纳入版本控制，不可恢复**。生成脚本
`AI-Marking/backend/scripts/build_paper_revision.py` 与 `paper_closeout.py` 仍在仓库内，但已无输入产物可用。

## 文档索引

- [SAF 2.0 V3-r2 研究设计](AI-Marking/docs/experiment/saf-memory-framework-design.md)：固定六题、40/10 划分、V4 两种顺序、评分仪器和描述性分析。
- [SAF 2.0 Luna-only 记忆框架运行手册](AI-Marking/docs/experiment/saf-memory-framework-runbook.md)：`/memory-study` 操作、恢复、完整性审计与安全导出。
- [SAF 2.0 v2 退役协议归档](AI-Marking/docs/experiment/archive/saf-memory-framework-v2.md)：旧双模型项目的只读审计依据。

## 本地验证

```bash
cd /Users/mac/Desktop/SURF/AI-Marking/backend
.venv/bin/pytest -q --cov=app --cov-report=term-missing
.venv/bin/ruff check app tests
.venv/bin/alembic heads
.venv/bin/alembic upgrade head
.venv/bin/python scripts/verify_scoring_instrument.py   # 评分仪器指纹；恢复运行前必跑（只读）

cd /Users/mac/Desktop/SURF/AI-Marking/frontend
npm run lint
npm run test:run
npm run build
npm audit --omit=dev

cd /Users/mac/Desktop/SURF/AI-Marking
./start.sh
```
