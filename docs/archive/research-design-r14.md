# r14 外部记忆表示与教师评分标准适应实验方案（历史归档）

> 本文是废弃的 r14 研究设计，仅用于研究演进追溯；不适用于
> `r15-structured-action-memory-fixed-benchmark-2026-08`，不代表当前实现或实验结果来源。

> **协议 ID：** `r14-external-memory-representation-frozen-holdout-2026-08`
> **状态：** 文档协议已定义，尚未实现。本文只定义研究设计，不代表代码、数据库或实验结果已经存在。

## 1. 研究问题与证据边界

本研究考察：当大语言模型持续参与同一道题的自动评分时，历史教师反馈形成的**外部记忆**是否能帮助模型适应教师的评分标准，以及外部记忆的表示方式是否影响这种适应。

研究只估计模型与 JorGPT 历史教师标签之间的同题局部一致性。MAE 降低不能表述为客观评分准确性、完整教师评分能力、教师工作量改善、学生学习成效、教师替代或课堂干预效果。数据没有独立教师复评，因此历史教师标签是本研究的比较目标，而不是被证明的绝对真值。

### 研究问题

- **RQ1：** RM 和 PM 是否分别比 NM 降低冻结同题 holdout 答案上的模型—教师 MAE？
- **RQ2（主要）：** 结构化程序记忆 PM 是否比自然语言规则记忆 RM 降低冻结同题 holdout 答案上的 MAE？

### 预注册比较

- 主要比较：`Δq = MAE(PM, q, h=30) − MAE(RM, q, h=30)`。
- 方向性预期：若 `Δq < 0`，表示 PM 在该题上的平均误差低于 RM；这是方法包比较，不是单独证明某个 PM 子组件的因果作用。
- `PM−NM` 与 `RM−NM` 是次要比较；适应阶段误差和学习曲线是描述性分析。

## 2. 三种实验条件

三种条件使用同一个主模型、相同题目和答案、相同 0–10 整数评分尺度、相同公共评分指令、相同生成参数和相同输出格式。评分处理的唯一差异是是否注入本条件的外部记忆快照。

| 条件 | 评分时的外部记忆 | 允许的历史信息 | 更新方式 |
|---|---|---|---|
| **NM**（No Memory） | 空 | 无 | 不更新 |
| **RM**（Rule Memory） | `Condition`、`Guideline`、`Evidence` | 仅当前题目的 RM 快照 | 教师反馈后重建规则快照 |
| **PM**（Program Memory） | `Trigger`、`Action`、`Evidence` | 仅当前题目的 PM 快照 | 教师反馈后重建程序快照 |

### 2.1 RM：自然语言规则记忆

RM 描述“在什么情况下，评分时应该关注什么”。每条规则包含：

```text
Condition: 可从答案直接观察到的同题特征
Guideline: 与该特征对应的评分关注点
Evidence: 来自历史教师反馈的简短依据
```

规则不得包含模型分数、模型理由、模型—教师分差、学生身份、教师画像或未由教师反馈支持的普遍评分标准。规则更新可以合并重复项、收窄表述或删除无用项，但不建立状态、版本、支持/反对计数或冲突生命周期。

### 2.2 PM：结构化程序记忆

PM 描述“满足什么触发条件时，评分应采取什么动作”。每条程序包含：

```text
Trigger: 可从当前答案直接观察到的同题特征
Action: 对该特征采取的评分关注或评分处理
Evidence: 来自历史教师反馈的简短依据
```

`Action` 必须是可执行的评分指导，例如“不给予未解释复杂度的部分信用”或“优先检查是否说明了算法的时间复杂度”。PM 不得引入机器强制分数范围、程序启用/停用、补丁、版本或自动冲突处理。

### 2.3 简洁性与隔离

- RM 与 PM 均从每道题的空快照开始，禁止跨题共享。
- 每个快照最多保留 10 条条目，总渲染文本最多 1,000 个英文词。两种表示使用相同上限。
- 每条 `Evidence` 只保留教师反馈模式的简短摘要；原始教师反馈只能在独立更新调用中使用。
- 外部快照是历史信息的唯一载体。历史答案或教师反馈不得以 few-shot 示例、对话消息、隐藏摘要或系统缓存形式进入评分。

## 3. 评分上下文隔离（硬性因果控制）

### 3.1 无状态评分调用

每次评分均使用全新的、无状态的模型调用。不得复用聊天会话、先前消息、先前模型回复、隐藏摘要、服务端会话状态、工具上下文或缓存的对话历史。

评分调用唯一允许的输入为：

```text
固定评分指令
当前 question
当前 student answer
当前条件的 external_memory_snapshot
```

当前题目和当前答案是评分任务输入，不属于历史记忆。除固定指令和当前题目/答案外，任何历史信息都必须来自显式外部记忆快照。

### 3.2 独立记忆更新调用

教师信息只进入独立的记忆更新调用：

```text
question
answer
teacher_score
teacher_feedback
prior_external_memory_snapshot
```

更新调用的输出只能是新的 `external_memory_snapshot`。更新调用完成后，会话、消息历史、模型理由和中间输出全部丢弃；下一次评分不得引用这些内容。评分输出也不得作为 RM/PM 更新输入。

### 3.3 条件具体规则

- NM 评分调用的外部记忆为空，并且不接收任何历史教师信息。
- RM 评分调用只能接收当前题目的 RM 快照。
- PM 评分调用只能接收当前题目的 PM 快照。
- 适应阶段每个位置先完成 NM、RM、PM 三个独立评分，之后才揭示该记录的教师分数和反馈。
- RM 与 PM 的更新调用可以读取同一条教师记录，但分别读取各自的 `prior_external_memory_snapshot`。
- 每次重试都必须创建新的无状态评分或更新调用；不得用失败调用的短期上下文恢复。
- holdout 阶段只读取在指定历史节点冻结的快照；教师分数和反馈在全部 holdout 评分结束前保持不可见。

## 4. 数据、抽样与实验流程

### 4.1 数据来源与清洗

实验使用 JorGPT 英文翻译数据中的题目、学生答案、0–10 历史教师分数和教师文字反馈。`ideal_answer`、预计算机器评分、学生匿名标识和教师时间戳不进入评分或记忆。

冻结前排除空题目、缺失教师标签、非 0–10 整数分数、缺失记录 ID、重复记录 ID、系统噪声和同一题目 ID 对应不一致题干。空学生答案是合法的非回答记录，不得因为空而删除或替换。

题目与记录角色冻结后，选择、分组和排序不得读取教师分数、教师反馈、模型输出或外部记忆内容。

### 4.2 题目与记录数量

- pilot：2 道题，独立于 formal。
- formal：20 道题。
- 每道题：40 条有效答案。
- 每道题随机分配 30 条 adaptation 与 10 条 holdout test。
- 30 条 adaptation 使用一个冻结随机顺序；10 条 holdout 使用一个冻结评估顺序。
- 每题、每条件、每条轨迹均从空记忆开始；不跨题、不跨运行共享快照。

### 4.3 适应阶段

对每道题按冻结的 30 条 adaptation 顺序重复以下流程：

1. 使用三个全新的无状态评分调用，分别运行 NM、RM、PM。
2. 确认三种评分调用均完成且输出为合法 0–10 整数分数。
3. 仅在三种评分都完成后揭示当前记录的教师分数和教师反馈。
4. 在独立更新调用中更新 RM 与 PM 外部快照；NM 不更新。
5. 丢弃评分与更新调用的上下文，仅保留下一位置所需的外部快照。

在第 5、10、20、30 条 adaptation 之后分别冻结 RM/PM 快照，供学习曲线和 holdout 评估使用。

### 4.4 冻结评估阶段

对每道题的同一组 10 条 holdout 答案，分别使用：

- `h=0`：空记忆 NM 基线；
- `h=5`、`h=10`、`h=20`、`h=30`：对应历史节点冻结的 RM 和 PM 快照。

评估评分调用均为全新无状态调用。评估阶段不得揭示教师分数或反馈，不得更新任何快照，不得把同一 holdout 答案的预测结果传入后续评分调用。

## 5. Pilot 与 Formal

### 5.1 最小 pilot

pilot 使用 2 道隔离题，完整执行 30/10 流程，但不显示 MAE、条件优劣、学习曲线或任何方向性效果。

pilot 只检查：

- 题目和记录切分正确；
- 30/10 角色没有交叉；
- 教师标签在每条 adaptation 评分完成前不可见；
- NM、RM、PM 的评分调用均为新会话；
- 更新调用结束后没有消息历史进入下一次评分；
- 记忆不跨题、不跨条件串用；
- holdout 标签不进入评分或更新；
- 输出分数与记忆格式可解析；
- 单次失败最多重试三次，失败后运行状态可见。

pilot 不用于选择提示、模型、记忆格式、题目、顺序或分析方法。

### 5.2 Formal

只有最小 pilot 的流程检查完成后才运行 20 道 formal 题。formal 运行期间不得根据中间 MAE、条件方向或学习曲线调整协议。

任何未完成 formal 运行只报告运行状态、失败阶段和已完成数量，不生成部分效果结论，不用插补填充失败评分。

## 6. 结果指标与统计分析

### 6.1 主要结果

对每道题、每个条件和每个历史节点，计算 10 条 holdout 的：

```text
absolute error = |model_score − teacher_grade|
MAE = mean(absolute error)
```

主要结果只使用 `h=30`，每道题产生一个 RM MAE、PM MAE 和 NM MAE。20 道题是唯一的主要推断单位；10 条答案不是 10 个独立题目。

### 6.2 主要比较与 bootstrap

主要比较为：

```text
Δq = MAE(PM, q, h=30) − MAE(RM, q, h=30)
```

重采样 20 道题的配对差值 10,000 次，报告：

- `mean(Δq)`；
- 双侧 95% percentile bootstrap CI；
- PM 胜题数、RM 胜题数和平局数；
- 相对 RM 的 MAE 降幅：`(MAE_RM − MAE_PM) / MAE_RM`；
- PM 胜题比例。

### 6.3 辅助与描述性分析

- 以题目级差值执行单侧 exact sign test，报告有效题数、PM/RM 胜负和平局及 p 值；全平局时标记为不可计算。
- `PM−NM` 与 `RM−NM` 使用相同的题目级配对 bootstrap，但标为次要比较。
- 报告 RMSE、exact agreement、高估/低估方向和题目级差值分布。
- 学习曲线展示 `h=0/5/10/20/30` 的题目等权 MAE 与 bootstrap CI；曲线只作描述性机制分析，不在每个节点进行确认性检验。

## 7. 失败、重试与记录边界

- 每个逻辑评分或更新调用最多尝试三次；每次尝试都是新的无状态会话。
- 不得使用失败调用的对话历史、部分响应或模型理由进行恢复。
- 三次均失败时，该条记录或该项目标记为未完成；不插补、不静默跳过、不生成部分主要效果结果。
- 只保留最低限度的运行记录：协议 ID、数据版本、题目/记录角色、模型标识、提示版本、调用阶段、成功/失败和失败原因。
- 不要求额外模型核验、复杂上下文审计、SHA 门禁、tokenizer 冻结、指纹校验、状态机日志或语义案例门禁。

## 8. 有效性、伦理与局限

- 结果只代表 JorGPT 数据集内同题历史教师标签的一致性。
- RM 与 PM 是两种完整外部记忆表示，不能把结果解释为某一个提示词、字段或更新操作的独立因果效果。
- 20 道题来自同一数据集和课程，不能直接外推到其他课程、学科或教师。
- 英文翻译文本不等同于原始语言文本。
- 单轨迹、温度 0 仍可能受供应商非确定性影响；本研究不估计多次运行稳定性。
- 外部记忆容量、条目格式和五个历史节点是协议条件；结论不外推到其他记忆容量或其他表示格式。
- 不得把外部记忆适应解释为学生学习、教师替代或真实课堂干预。

## 9. 冻结对象与文档边界

正式运行前冻结：

- 数据版本、清洗规则、2 道 pilot 题、20 道 formal 题、每题 30/10 角色和顺序；
- 主模型、温度 0、公共评分指令和三种输出格式；
- RM/PM 条目字段、10 条/1,000 词容量限制和五个历史节点；
- 无状态评分调用与独立更新调用的上下文隔离规则；
- 主要 MAE、10,000 次 paired bootstrap、单侧 sign test 和学习曲线口径。

本协议不冻结数据库迁移、API 结构、部署配置或代码函数；这些属于未来实现工作，不应在本文中伪装成已实现能力。

## 附录 A：公共评分提示规范

```text
Grade the current answer from 0 to 10 using only:
1. the fixed grading instructions,
2. the current question,
3. the current student answer, and
4. the explicitly supplied same-question external memory snapshot.

Do not use any previous conversation, previous answer, teacher score,
teacher feedback, hidden summary, few-shot example, cached message, or
information not present in the current question, current answer, or the
external memory snapshot. Treat the external memory as guidance, not as
a complete rubric. Apply it only when the current answer directly supports
the stated condition or trigger.

Return exactly one JSON object with an integer score from 0 to 10 and a
brief reason. Do not return or infer hidden conversation state.

Question: {{question}}
Answer: {{answer}}
External memory snapshot:
{{external_memory_snapshot}}
```

## 附录 B：RM 更新提示规范

```text
Create a concise replacement snapshot of same-question natural-language
grading rules. Use only the current question, current answer, teacher score,
teacher feedback, and the prior external memory snapshot. Do not use any
model score, model reason, model-teacher difference, previous conversation,
or hidden context.

Each rule must contain Condition, Guideline, and Evidence. Keep only
observable, reusable features supported by teacher feedback. Merge redundant
rules and remove rules that are no longer useful. Return at most 10 rules
and keep the rendered snapshot within 1,000 English words.
```

## 附录 C：PM 更新提示规范

```text
Create a concise replacement snapshot of same-question structured grading
programs. Use only the current question, current answer, teacher score,
teacher feedback, and the prior external memory snapshot. Do not use any
model score, model reason, model-teacher difference, previous conversation,
or hidden context.

Each program must contain Trigger, Action, and Evidence. Trigger must be an
observable feature of a future answer. Action must state the grading handling
to apply when the trigger is directly evidenced. Merge redundant programs
and remove programs that are no longer useful. Return at most 10 programs
and keep the rendered snapshot within 1,000 English words.
```
