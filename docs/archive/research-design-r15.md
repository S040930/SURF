# r15 固定高覆盖题基准下的外部记忆标签适应研究（历史归档）

> 本文是废弃的 r15 研究设计，仅供研究演进追溯；不适用于
> `r16-package-comparison-mechanism-audit-fixed-benchmark-2026-08`，不代表当前实现或结果来源。

> **协议 ID：** `r15-structured-action-memory-fixed-benchmark-2026-08`
> **状态：** 文档协议已定义，尚未实现。本文只定义研究设计，不代表代码、数据库或实验结果已经存在。
> **前序协议：** r14 设计已归档至 `docs/archive/research-design-r14.md`；当前代码仍是历史 r13 实现。

## 1. 研究问题与证据边界

本研究考察：在同一道题的自动评分过程中，历史人类评分形成的显式外部记忆是否能降低模型与历史标签之间的差距，以及自然语言规则记忆与结构化行动规则记忆是否产生不同结果。

研究对象是 JorGPT 固定 20 题高覆盖基准上的历史人类评分标签，不是从题目总体随机抽取的样本。MAE 降低只能表示与该基准标签的同题局部一致性，不能表述为客观评分准确性、教师个人偏好复现、教师工作量改善、学生学习成效、教师替代或真实课堂干预效果。

正式运行前必须核查数据描述符和可用元数据：教师分数与文字反馈是否来自同一评分者、是否使用相同评分流程、反馈是否与分数来自同一人。若无法证明，全文统一使用“历史人类评分标签”或“局部评分政策”，不得使用“某位教师的评分标准”。

### 研究问题

- **RQ1（主问题）：** 在 `h=30`，RM 和 SM 是否分别相对 NM 产生更低的固定 20 题题目等权 MAE？
- **RQ2（条件性次级问题）：** 只有当至少一种记忆表示在主轨迹和敏感性轨迹中都相对 NM 呈改善时，才描述 SM 与 RM 的 MAE 差异；此差异表示完整表示包的比较，不证明单一字段或更新操作的独立因果效果。

### 解释门槛

- 不预设 0.20、0.25 或其他最小实际效应阈值；本研究定位为探索性固定基准研究。
- 主轨迹上某条件的 20 题平均 MAE 低于 NM，且在敏感性轨迹中方向不反转，才称为“该固定基准上观察到改善”。
- 若 RM 与 SM 都不优于 NM，仍可报告 SM−RM 的数值，但不得称为更有效的记忆表示。
- 不使用总体推断型 p 值或置信区间；逐题差异、胜平负和 leave-one-question-out 结果用于固定基准稳健性描述。

## 2. 三种实验条件

三种条件使用同一个主模型、相同题目和答案、相同 0–10 整数评分尺度、相同公共评分框架、相同生成参数和相同输出格式。唯一实验差异是是否注入本条件已渲染的外部记忆。

| 条件 | 评分时的外部记忆 | 允许的历史信息 | 更新方式 |
|---|---|---|---|
| **NM**（No Memory） | 空 | 无 | 不更新 |
| **RM**（Rule Memory） | `Condition`、`Guideline`、`Evidence` | 仅当前题目的 RM 可见条目 | 教师信息后重建规则快照 |
| **SM**（Structured Action-rule Memory） | `Trigger`、受控 `Action`、`Evidence` | 仅当前题目的 SM 可见条目 | 教师信息后重建行动规则快照 |

SM 不具有 DSL、解释器或自动分数修正器语义；评分模型仍需理解并应用其行动指导。

### 2.1 RM：自然语言规则记忆

每条 RM 条目包含：

```text
Condition: 可从未来答案直接观察到的同题特征
Guideline: 对该特征应关注的自然语言评分原则
Evidence: 教师反馈模式的简短依据
```

RM 不得包含模型分数、模型理由、模型—教师分差、学生身份、教师画像或未由教师反馈支持的普遍标准。

### 2.2 SM：结构化行动规则记忆

每条 SM 条目包含：

```text
Trigger: 可从未来答案直接观察到的同题特征
Action: 受控动作类型及其明确评分对象
Evidence: 教师反馈模式的简短依据
```

`Action` 只能使用以下动作类型之一，并附带具体评分对象：

```text
AWARD_CREDIT: <criterion>
WITHHOLD_CREDIT: <criterion>
PRIORITIZE_CHECK: <criterion>
DO_NOT_PENALIZE: <criterion>
```

动作是供评分模型理解的结构化指导，不直接修改模型分数，不引入分数范围修正、启用/停用状态、补丁、版本、复杂冲突状态或自动执行器。

### 2.3 快照与来源元数据

- RM 与 SM 均从每道题的空快照开始，禁止跨题、跨条件或跨轨迹共享。
- 每个快照最多保留 10 条可见条目，总渲染文本最多 1,000 个英文词。
- 每个条目另保存不可见的 `source_record_ids`，用于更新追踪和 formal 后人工盲审；来源 ID 不进入评分提示。
- 评分只接收可见条目；更新调用可以读取可见条目和来源映射。
- 记录实际条目数、可见词数、动作类型、格式失败、容量超限和删除/合并事件。

## 3. 评分上下文隔离（硬性规则）

### 3.1 无状态评分接口

每次评分均使用全新的、无状态的模型调用。不得复用聊天会话、先前消息、先前模型回复、隐藏摘要、服务端会话状态、工具上下文或缓存的对话历史。

评分调用唯一允许的输入为：

```text
固定公共评分指令
当前 question
当前 student answer
当前条件已渲染的 external_memory_snapshot
```

当前题目和当前答案是任务输入，不属于历史记忆。除固定指令和当前题目/答案外，任何历史信息都必须来自显式外部记忆的可见条目。

公共评分指令必须明确禁止先前对话、历史答案、教师分数、教师反馈、few-shot 案例、隐藏摘要、缓存消息和未列出的历史信息。评分只返回：

```json
{"score": 0}
```

其中 `score` 是 0–10 的整数；不生成模型理由，因为理由不参与更新或主分析。

### 3.2 独立更新接口

教师信息只进入独立的记忆更新调用：

```text
question
answer
teacher_score
teacher_feedback
prior_external_memory_snapshot
```

更新输出只能产生新的可见快照与不可见来源映射。评分输出不得成为 RM/SM 更新输入。更新会话、消息历史、模型理由和中间输出在更新完成后立即丢弃。

运行器可以向更新调用附带当前记录的 `entry_id` 作为非语义 provenance 元数据，用于把新条目绑定到 `source_record_ids`；该 ID 不得进入评分提示，也不得被当作评分内容。

### 3.3 条件规则

- NM 评分永远使用空外部记忆，不接收任何历史教师信息。
- RM 评分只能接收当前题目的 RM 可见快照；SM 评分只能接收当前题目的 SM 可见快照。
- adaptation 每个位置先完成 NM、RM、SM 三个独立评分，之后才揭示该记录的教师分数与反馈。
- RM 与 SM 的更新调用可以读取同一教师记录，但只能读取各自 prior snapshot；不得读取另一条件输出。
- 每次评分或更新重试都创建新会话，不得以失败调用的短期上下文恢复。
- holdout 阶段快照只读；教师分数与反馈既不进入评分，也不进入 holdout 更新。

## 4. 数据冻结、题目选择与切分

### 4.1 题目角色

数据冻结前检查 50 道题的有效记录数量、题干一致性、记录 ID 唯一性、教师字段完整性和 0–10 整数标签。按有效记录数降序、`question_id` 升序破同分：

- 第 1–20 道：formal；每题至少冻结 40 条答案。
- 第 21–22 道：pilot，完全独立于 formal。
- 第 23–24 道：development，完全独立于 formal 和 pilot。

题目角色选择只使用有效性和记录数量，不读取教师分数值、教师反馈、模型输出或任何已生成记忆。formal 20 题是固定高覆盖基准，不代表 JorGPT 全部题目总体。

### 4.2 记录清洗与重复控制

- 空学生答案是合法非回答记录，不删除、不替换、不视为 API 失败。
- 先进行 Unicode 规范化、大小写归一、空白压缩和标点规范化，形成完全重复簇。
- 对至少 20 个规范化词的非空答案，再以“规范化字符相似度 ≥0.95 且 token 5-gram Jaccard ≥0.90”形成近重复簇；少于 20 个词的答案只按完全重复处理。
- 任一重复簇不得跨 adaptation 与 holdout。
- 记录被排除的重复簇、题目、成员数量和最终角色分配；不能在看到模型结果后重新分配。

### 4.3 分层 30/10 切分

每题只从有效记录中冻结 40 条。教师标签可用于离线切分，但绝不进入模型输入：

1. 按教师分数划分 `0–3`、`4–6`、`7–10` 三个分层。
2. 对每个非空分层，按最大余数法分配 holdout 配额，并尽量保证 holdout 与 adaptation 都覆盖该分层。
3. 使用固定协议种子 `r15-v1-split` 在各分层内随机选取记录；同一重复簇整体分配。
4. 冻结 10 条 holdout 后，从剩余记录冻结 30 条 adaptation。

每题使用同一组 holdout 答案评估 `h=0/5/10/20/30`。教师标签只用于离线角色冻结和结果计算，不进入任何评分请求。

## 5. 两条适应轨迹与运行流程

每题、每条件、每条轨迹均从空记忆开始；两条轨迹使用相同的 30/10 角色，只改变 adaptation 顺序。

- **主轨迹：** 使用种子 `r15-v1-main`，是唯一的主要固定基准结果来源。
- **敏感性轨迹：** 使用种子 `r15-v1-sensitivity`，只检查 RM/SM 相对 NM 及 SM 相对 RM 的方向是否反转，不与主轨迹合并进行总体推断。

对每条 adaptation 顺序循环：

1. 为当前答案分别创建 NM、RM、SM 三个全新的无状态评分调用。
2. 确认三个调用均完成并返回合法 0–10 整数。
3. 只有三者全部完成后，读取当前记录的教师分数和反馈。
4. 在独立更新调用中分别更新 RM 和 SM 快照，NM 不更新。
5. 丢弃全部评分和更新会话，仅保留下一位置需要的快照与来源元数据。

在第 5、10、20、30 条 adaptation 后冻结只读快照。之后对同一 10 条 holdout 分别运行：

- `h=0`：空记忆基线；
- `h=5/10/20/30`：对应 RM 与 SM 的只读快照。

holdout 评分期间不得揭示教师标签、更新任何快照或把某一次评分输出传给其他条件/节点。

## 6. Development、Pilot 与 Formal

### 6.1 Development

使用第 23–24 道隔离题。允许冻结前修正：公共评分提示、RM/SM 字段约束、SM 动作词、来源映射、快照容量和解析规则。不得使用 formal 题、formal MAE 或条件方向作选择依据。

Development 至少检查：RM 与 SM 字段可辨识、SM 动作类型合法、来源映射可追踪、快照可解析、空记忆可表示、评分只收到外部可见条目。

### 6.2 Pilot

使用第 21–22 道隔离题，完整执行 30/10 流程，但不显示 MAE、条件排名、学习曲线、胜负题数或方向性结果。Pilot 后冻结所有协议。

Pilot 检查：

1. 题目和记录角色正确；
2. 每次评分都是新会话且无历史消息；
3. 三条件评分完成后才揭示教师标签；
4. 更新调用独立且结束后会话被丢弃；
5. 记忆不跨题、跨条件或跨轨迹共享；
6. SM 动作词、RM/SM 字段和 1,000 词容量合法；
7. 来源 ID 映射完整但不进入评分提示；
8. holdout 标签不进入评分或更新；
9. 失败重试创建新会话且状态可见。

### 6.3 Formal

Formal 期间不得根据中间 MAE、条件方向、学习曲线、记忆条目内容或失败模式调整协议。只有 development 阶段允许修改设计；pilot 完成后所有提示、题目、切分、顺序和分析口径冻结。

## 7. 记忆忠实性审计

Formal 完成后分别从 RM 与 SM 抽取 40 个条目：`h=5` 和 `h=30` 各 20 个，使用种子 `r15-v1-audit` 并覆盖不同题目。审计者以中性格式查看记忆条目、当前题目及 `source_record_ids` 对应的答案、教师分数和教师反馈，不显示条件名称或模型调用信息。

每条条目标注：

- `supported`：实质性主张由引用教师记录直接支持；
- `partially_supported`：核心方向有依据，但存在过度概括、遗漏或范围扩大；
- `unsupported`：无法由引用记录支持，或与记录冲突。

审计结果只作为机制与效度报告，不改变评分结果、不删除条目、不进行第二模型自动审计。

## 8. 固定基准分析

对每道题、每个条件和每个历史节点，计算 10 条 holdout 的 MAE、RMSE、完全一致率、±1 分一致率和有符号误差方向。

主轨迹 `h=30` 报告：

- `RM−NM`、`SM−NM` 和 `SM−RM` 的 20 题平均差；
- 每题原始 MAE 与差值；
- RM/SM 胜题、平局和负向题数；
- 高估/低估方向、RMSE、完全一致率和 ±1 分一致率；
- leave-one-question-out 后每个比较的最小与最大平均差。

不报告可推广到题目总体的 p 值或置信区间。`h=0/5/10/20/30` 学习曲线和敏感性轨迹只作描述性结果。若敏感性轨迹改变主轨迹的方向，则对应比较标为路径不稳定或不确定。

### 失败边界

- 每个逻辑评分或更新调用最多三次，每次均为全新无状态会话。
- 三次失败后保留阶段、原因、完成数量和缺失位置；不插补、不静默跳过、不用失败响应恢复。
- 主结果表必须显示每题配对完整性。若缺失导致比较方向可在 0–10 评分范围内反转，则该比较只能报告为不确定。
- 可提供基于已知教师标签和 0–10 评分范围计算的最好/最坏误差边界，但不得把边界内的部分结果包装为完整 formal 结论。

## 9. 评分提示规范

### 公共评分提示

```text
Grade the current answer from 0 to 10 using only:
1. the fixed shared grading framework,
2. the current question,
3. the current student answer, and
4. the explicitly supplied same-question external memory items.

Do not use any previous conversation, previous answer, teacher score,
teacher feedback, hidden summary, few-shot example, cached message, or
information not present in the current question, current answer, or the
external memory items. The shared framework is not a teacher-specific
rubric. Use memory only when the current answer directly supports its
condition or trigger.

Return exactly one JSON object with an integer score from 0 to 10.

Question: {{question}}
Answer: {{answer}}
External memory items:
{{rendered_external_memory}}
```

### RM 更新提示

```text
Create a concise replacement snapshot of same-question natural-language
grading rules. Use only the current question, current answer, teacher score,
teacher feedback, and the prior RM snapshot. Do not use any model score,
model reason, model-teacher difference, previous conversation, or hidden
context.

Each visible rule must contain Condition, Guideline, and Evidence. Keep only
observable, reusable features supported by teacher feedback. Return at most
10 rules and keep the rendered snapshot within 1,000 English words.
Return source_record_ids only as separate provenance metadata; they must not
be rendered in the scoring prompt.
```

### SM 更新提示

```text
Create a concise replacement snapshot of same-question structured action
rules. Use only the current question, current answer, teacher score, teacher
feedback, and the prior SM snapshot. Do not use any model score, model reason,
model-teacher difference, previous conversation, or hidden context.

Each visible rule must contain Trigger, Action, and Evidence. Action must use
exactly one of AWARD_CREDIT, WITHHOLD_CREDIT, PRIORITIZE_CHECK, or
DO_NOT_PENALIZE followed by a concrete grading criterion. Keep only observable,
reusable features supported by teacher feedback. Return at most 10 rules and
keep the rendered snapshot within 1,000 English words.
Return source_record_ids only as separate provenance metadata; they must not
be rendered in the scoring prompt.
```

## 10. 数据治理、冻结对象与文档边界

Formal 前记录数据许可、自由文本去标识化、模型服务商保留/训练政策、数据处理地区以及伦理审批或豁免结论。学生匿名 ID、`ideal_answer`、预计算模型列、时间戳和其他非批准字段不进入评分、记忆、日志、报告或题目选择。

正式运行前冻结：

- 数据版本、有效性规则、题目排序、development/pilot/formal 角色；
- 40 条记录及 30/10 角色、分层规则、重复控制和两条顺序；
- 主模型、温度 0、公共评分提示、RM/SM 更新提示和输出 JSON；
- NM/RM/SM 条目格式、SM 动作词、10 条/1,000 词容量和五个历史节点；
- 无状态评分、独立更新、来源元数据隔离、失败重试和固定基准分析口径。

本协议不定义数据库迁移、API 结构、部署配置或代码函数；未来实现必须先满足本文行为边界，并不得把旧 r13 代码或 r14 历史设计描述为 r15 实现。
