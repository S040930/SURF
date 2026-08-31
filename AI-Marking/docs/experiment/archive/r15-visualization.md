# r15 实验展示文档边界（历史归档）

> 历史 r15 方案，仅供追溯；不适用于 r16，也不代表当前实现或结果来源。

> 本文只描述未来 r15 页面应展示的研究阶段；当前前端仍服务历史 r13 平台，不得把现有页面当作 r15 已实现能力。

协议：`r15-structured-action-memory-fixed-benchmark-2026-08`

## 页面阶段

- `/experiments`：显示协议 ID、数据冻结状态、development、pilot 和 formal 运行状态。
- `/experiments/:projectId`：按“数据治理与冻结、development、pilot、formal 适应、holdout 评估、固定基准分析、忠实性盲审”顺序显示阶段。
- `/experiments/:projectId/adaptation`：显示当前题目、轨迹、位置、NM/RM/SM 完成状态和已生成的外部快照节点；不显示教师标签提前值。
- `/experiments/:projectId/evaluation`：显示 `h=0/5/10/20/30` 节点、主轨迹与敏感性轨迹完成状态；不显示 holdout 教师标签。
- `/experiments/:projectId/report`：仅在 formal 配对完整性检查通过后显示固定 20 题 MAE、逐题差异、胜平负、leave-one-question-out 和描述性学习曲线。

## 上下文隔离展示

页面可以显示当前阶段、题目、轨迹、条件、位置、快照节点、成功/失败状态、容量计数和有限错误原因，但不得显示或传递：

- 任一评分调用的先前会话消息；
- 更新调用的模型理由、中间上下文、来源 ID或隐藏摘要；
- holdout 教师分数、教师反馈或未解封的方向性结果；
- few-shot 历史案例、历史答案或被评分调用隐式复用的短期记忆。

评分详情只能标明读取了哪个已渲染外部快照；不能把更新调用上下文作为评分详情返回。重试状态必须显示为新的独立尝试，不得提供“继续上一会话”操作。

## 报告内容

formal 完整结束并通过完整性检查后，报告显示：

- 每题、每条件、每历史节点的 10 条 holdout MAE、RMSE、完全一致率、±1 分一致率和高估/低估方向；
- 主轨迹 `h=30` 的 RM−NM、SM−NM、SM−RM 逐题差、平均差和胜平负；
- leave-one-question-out 的最小/最大平均差；
- 主轨迹与敏感性轨迹的方向一致性；
- `h=0/5/10/20/30` 描述性学习曲线；
- RM/SM 条目来源盲审的 `supported`、`partially_supported`、`unsupported` 计数。

不显示总体推断型 p 值或置信区间，不把固定 20 题结果推广到全部题目、其他教师或课堂效果。Pilot 和 development 页面不显示 formal MAE、条件排名或学习曲线。

## 兼容边界

旧 r13 页面中的可执行程序记忆、案例/规则/图结构排名、复杂调用审计、第二模型核验和统计门禁不属于 r15 展示需求。当前代码与本文件不表示这些页面已经被修改或可运行。
