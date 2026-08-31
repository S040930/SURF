# r23 DREsS_CASE Rubric Sensitivity：预注册实验方案

## ARS Material Passport

| 字段 | 值 |
|---|---|
| Origin Skill | `ars-codex:academic-research-suite` |
| Origin Workflow | `experiment-agent` |
| Origin Mode | `plan` |
| Origin Date | 2026-08-30 |
| Verification Status | **UNVERIFIED**：平台与合成测试已验证，尚未运行真实模型实验 |
| Version Label | `code_plan_v1` |
| Protocol ID | `r23-dress-case-rubric-sensitivity-2026-08-v1` |

本文档是分析前预注册的研究协议。实现通过不代表假设得到支持；只有正式实验完成、报告哈希锁定并检查全部限制后，才能形成论文结论。

## 1. 研究目标与主张边界

DREsS 包含真实课堂、标准化既有数据和 corruption-based augmentation 三类子集；DREsS_CASE 是由 CASE 策略生成的合成破坏样本。本研究不评价真实作文评分准确性，而把 DREsS_CASE 当作**可控刺激材料**，检验 LLM 评分代理是否对预先指定的 rubric 维度产生有方向、有区分度且具选择性的响应。DREsS 与 CASE 的数据来源、构造动机和规模以原始论文为准（Yoo et al., 2025）。

允许的结论只有：

1. “模型对 CASE 预设等级表现出单调敏感性”；
2. “模型能够在一定程度上恢复 CASE 预设标签”；
3. 对 Organization，若选择性检验也通过，可称为 “rubric-selective”。

禁止把 QWK 写成“与人工评分一致性”，禁止把 CASE 标签当作人工真值，也禁止把结果自动推广到真实学生作文、公平性、教学有效性或跨语言评分准确性。

## 2. 研究问题、假设与证据等级

### RQ1：单调性

当目标维度的 CASE 预设等级从 1 增至 5 时，模型在相同目标维度上的预测分数是否单调上升？

- H1：目标维度的 monotonic pair accuracy（MPA）显著高于 0.5。
- 单次项目的检验族为 3 个目标维度单元，Holm 校正。

### RQ2：等级区分度

模型能否区分 1–5、间隔 0.5 的九个 CASE 预设等级？

- H2：目标通道的 quadratic weighted kappa（QWK）显著高于 0，同时报告 MAE、Spearman ρ、极端等级差和实际文本变化的相邻等级差。
- QWK 的名称固定为 **CASE intended-label recovery**。
- 单次项目的检验族为 3 个目标维度单元，Holm 校正。

### RQ3：Rubric selectivity

在只改变句序的 Organization 配对作文中，Organization 分数是否比 Content 与 Language 分数变化更明显？

- H3：`SI = slope(Organization) − mean[slope(Content), slope(Language)] > 0`。
- 单次项目只有一个预注册模型配置，H3 不作跨模型校正。

### 证据等级

| 语料层 | 等级 | 原因 |
|---|---|---|
| Organization | 确认性 | 可重建 base 内九级配对，保留两个 corruption repeat |
| Content | 次要 | 在共同 prompt 池分层，但没有 Organization 的完整 base 配对 |
| Language | 探索性 | 缺少配对结构，并存在篇幅和 prompt 混杂 |

只有某维度在**两个模型**上同时通过 H1 与 H2，才可称为稳定敏感；Organization 还必须在两个模型上通过 H3 才可称为 rubric-selective。

## 3. 材料、数据门禁与隐私

### 3.1 固定材料

| 维度 | 文件 | 行数 | 每档 | SHA-256 |
|---|---|---:|---:|---|
| Content | `DREsS_CASE_content.tsv` | 8,307 | 923 | `cb3e082996955d256068068297d78bfaebd75f00569d6f6e68a3ad1f016d2feb` |
| Organization | `DREsS_CASE_organization.tsv` | 31,086 | 3,454 | `005c1ffcd47db62902ffdc520fb1f424a63a84e1ad9765b2ae6b3f37c781c69a` |
| Language | `DREsS_CASE_language.tsv` | 792 | 88 | `5b81050c03a5ae678e4b3cd04563de481ae5aa4a233b817608a9dd0e89004406` |

字段分别固定为 `id, prompt, essay, <dimension>`；所有标签必须是 `{1, 1.5, …, 5}`，九档平衡且必填字段无空值。解析器使用标准 TSV 流式读取，作文内换行不计作新记录。任一哈希、字段、行数、标签分布或空值检查不匹配，项目不得开始。

### 3.2 Organization 配对重建

1. 对 prompt 与 essay 做 Unicode NFC 和换行标准化；
2. 以 `\w+|[^\w\s]` 分词，保留大小写、标点及 token 多重性；
3. 由 prompt 与排序后的 token `Counter` 生成内容签名；
4. 以九个连续的 3,454 行等级块验证行序：每块前 1,727 行为 corruption repeat 1，后 1,727 行为 repeat 2；
5. 得到 1,727 个 base，每个 base 9 级 × 2 repeat；
6. score=5 的两个重复原文必须完全相同，抽样时合并为一个槽，因此每个 base 有 17 个非重复观察槽；
7. 完全相同输入跨等级出现时标记为 collision/no-op。QWK 保留这些观察，但 MPA 和相邻等级比较排除完全相同输入的对比。

配对键是实施侧派生值，不声称是 DREsS 官方 ID。

### 3.3 受限数据

- DREsS 正文和完整 prompt 不进入 Git 仓库、公开导出、应用日志、事件、异常或调用审计。
- 只有被抽中的文本进入本地实验数据库与单次 Codex Runner 输入。
- Manifest 只含哈希、派生 ID、标签、长度、collision 和抽样元数据。
- 创建每个项目时，操作者必须确认所选模型服务获准处理该数据。
- 正式实施前还需遵循数据目录 `README.docx` 的分发与使用条件；平台检查该文件存在，但不替代人工许可审查。

## 4. 固定抽样与运行规模

抽样种子固定为 `20260830`。按 prompt/base 分层后，以 `SHA-256(protocol | seed | phase | dimension | level | id)` 排序；不能在查看模型结果后改变样本。

### 技术试点

| 语料 | 抽样 |
|---|---:|
| Content | 每级 5，共 45 |
| Language | 每级 5，共 45 |
| Organization | 5 base × 17，共 85 |
| 单模型配置 | 175 个评估槽位 |

试点只检查 Schema 合法率、失败类型、延迟、自动运行快照和端到端流程，不显示分数、趋势或效应，也不进入确认性推断。

### 正式实验

| 语料 | 主调用观察槽/模型 | 复测槽/模型 |
|---|---:|---:|
| Content | 共同 prompt 池内每级 60，共 540 | 每级 6，共 54 |
| Language | 排除试点后每级 83，共 747 | 每级 8，共 72 |
| Organization | 60 个未用于试点的 base × 17，共 1,020 | 6 base × 17，共 102 |
| 合计 | 2,307 | 228 |

单模型配置共有 2,307 个主槽和 228 个复测槽，协议上限 2,535。完全相同的 prompt+essay 输入在同一模型、同一运行阶段只调用一次，再映射回多个观察行；开始实验时生成的 Manifest 记录去重后的实际逻辑调用数。试点与正式样本严格互斥。

## 5. 模型、Rubric 与测量程序

### 5.1 模型控制

- 每个项目恰好绑定一个 Runner ID；平台不硬编码模型名，可为同一模型创建多个配置。
- `reasoning_effort` 可选 `low`、`medium` 或 `high`；`speed_mode` 可选 `standard` 或 `fast`；单篇调用 `timeout` 可设为 30–1800 秒，新配置默认 120 秒。三项选择值均进入配置哈希和运行快照，论文方法与结果表按项目披露实际值。
- `speed_mode=standard` 显式映射为 Codex `service_tier="default"`；`speed_mode=fast` 映射为 `service_tier="fast"` 并启用 CLI `fast_mode`。Fast 是处理速度/额度条件，不是可指定的精确 token/s 或完成秒数。OpenAI 当前说明其可提高受支持模型速度，但消耗更多额度；模型支持范围、额度倍率和可用性以运行时的 [Codex Speed 官方文档](https://learn.chatgpt.com/docs/agent-configuration/speed)为准。
- 同一配置的再次运行建立独立项目并复用固定样本；不同思考程度或不同速度模式属于不同实验条件，不作为随机复测合并。技术试点与其对应正式实验必须使用完全相同的 Runner 配置，因此速度模式也必须一致。
- 当前 `codex exec` 不暴露 temperature；如实披露，并用约 10% 复测估计随机性。
- 每篇作文运行新的 `codex exec --ephemeral`，只读沙箱、空临时目录、忽略用户配置和规则，严格 output schema。
- 调用前比较 Codex CLI 路径、可执行文件 SHA-256 和版本；漂移即停止。

### 5.2 Rubric 的来源与改编规则

本研究使用**文献对齐的研究专用分析性量表**，不称其为“DREsS 官方完整 rubric”：

- Content、Organization、Language 三个构念及 1–5、间隔 0.5 的九值范围来自 DREsS（Yoo et al., 2025）；
- 五个整数表现锚点参考 González、Trejo 与 Roux（2017）附录的大学 EFL 分析性写作量表后重新表述；
- 原量表中的 Content 与 Organization 分别对齐同名维度；Use of Language、Vocabulary、Mechanics/Spelling 合并为 DREsS 的 Language；
- González et al. 原量表包含 0 分，本研究因 DREsS 的有效范围为 1–5 而不使用 0 分；数据门禁已排除空作文；
- `1.5/2.5/3.5/4.5` 是本研究的预注册插值规则：仅当表现实质上介于相邻两个整数描述之间时使用；
- 各维度采用独立的 best-fit 判断，不把篇幅本身当作质量证据，也不以一个维度的优劣决定另一个维度。

该改编具有明确文献来源，但尚未经过独立的人类评分者验证，故不得称为“已验证量表”。Keller et al.（2024）对 Language quality、Content 与 Structure 的分维度 EFL 评分研究仅用于支持三个构念可分别观察，不提供本研究的九档标签真值。

平台默认且写入运行快照的完整英文 Rubric 如下：

```text
Score the essay independently on Content, Organization, and Language relative to the writing prompt. Use a best-fit judgment for each dimension. Do not let performance in one dimension determine a score in another dimension, and do not treat length alone as evidence of quality.

Allowed scores: 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, or 5.
Integer-anchor rule: choose the integer descriptor that best represents the essay's overall performance on that dimension.
Half-point rule: use a half-point only when the performance falls substantively between the two adjacent integer descriptors.

CONTENT — relevance, completeness, support, and development of ideas in response to the prompt.
5 = Fully addresses the prompt. Ideas are relevant, specific, and thoroughly developed with strong reasons, details, or examples.
4 = Addresses the prompt well. Main ideas are relevant and sufficiently developed, with only minor gaps, redundancy, or unnecessary information.
3 = Adequately addresses the prompt, but some relevant information is missing or ideas are unevenly or only partly developed.
2 = Shows limited relevance to the prompt. Major gaps, insufficient or inappropriate support, or substantial repetition weaken the response.
1 = Shows minimal relevance or development. The response provides little usable content and few or no supporting details.

ORGANIZATION — logical sequencing, paragraphing, cohesion, transitions, and clarity of progression.
5 = Ideas and paragraphs are purposefully and logically sequenced. Cohesion is smooth, and the structure is consistently easy to follow.
4 = The response is generally well organized and coherent. Sequencing and connections are clear despite minor breaks or incomplete transitions.
3 = An understandable structure is present, but uneven sequencing, weak transitions, or local jumps sometimes interrupt the progression.
2 = Organization is weak. Basic connections are present, but substantial sequencing or cohesion problems make much of the response difficult to follow.
1 = The response is fragmented or seriously disordered. Relationships among ideas are difficult to recover, and effective structure or cohesion is largely absent.

LANGUAGE — grammatical control, sentence formation, vocabulary and collocations, spelling, capitalization, and punctuation.
5 = Language is consistently controlled, varied, and precise. Grammar, word choice, and mechanics are accurate apart from minor slips that do not affect understanding.
4 = Language is generally accurate and appropriately varied. Some errors occur, but they rarely interfere with understanding.
3 = Noticeable errors in grammar, sentence formation, word choice, or mechanics occur, but meaning generally remains clear.
2 = Frequent basic errors and restricted or repetitive language sometimes obscure meaning and make the response difficult to understand.
1 = Pervasive errors and very limited language frequently obscure meaning; control of basic sentence forms, vocabulary, or mechanics is minimal.

Score only the submitted essay against the writing prompt and this rubric. Return no explanation or feedback.
```

Rubric 本身不出现 CASE、corruption、目标维度、预设等级或配对信息，避免向模型提示实验条件。平台保存正文 SHA-256，并在项目开始时将所选版本逐字写入不可变运行快照。已有旧 Rubric 记录不覆盖；正式实验必须新建并选用 `DREsS r23 literature-aligned rubric v2`。

### 5.3 评分输出

唯一允许的输出为：

```json
{"content": 1.0, "organization": 1.0, "language": 1.0}
```

三个字段均是九值枚举，不允许额外字段、反馈或推理。数据库用 `score_x2` 整数 2–10 存储，避免半分浮点误差。

CASE 维度、CASE 标签、源文件名、原始 ID、派生 base ID和 collision 状态绝不进入模型输入。作文中的指令、JSON 和提示词注入均被包在 `<essay>` 中，并明确视作待评分学生文本。

### 5.4 流程

1. 流式数据门禁；
2. 保存一个或多个 Runner 配置，并固定模型、思考程度、速度模式和 timeout；
3. 保存 Rubric；
4. 创建技术试点并记录数据处理确认；
5. 点击“开始实验”；平台自动记录数据、Runner、CLI 指纹、Rubric 和环境快照，并物化样本、去重调用、哈希 Manifest；
6. 串行运行单配置试点；失败进入 `attention_required`，只允许人工重试；
7. 试点完成后创建完全匹配的正式项目；
8. 正式运行期间结果 API 保持密封；
9. 全部调用成功后生成统计报告和 SVG，锁定哈希，才解除正式结果密封。

没有静默重试、可选停止或查看中间效应后改样本。

## 6. 统计分析

### 6.1 H1：MPA

在同一 cluster 内对不同 CASE 等级组成有序对。高等级预测更高计 1，平局计 0.5，反向计 0；完全相同输入的跨等级对排除。先计算 cluster 内 MPA，再宏平均：Organization 以 base 聚类，Content/Language 以 prompt 聚类。

### 6.2 H2：区分度

- QWK：九档 CASE 预设标签恢复；
- MAE：以 1–5 原尺度报告；
- Spearman ρ：等级秩相关；
- 极端等级差：level 5 的平均预测减 level 1；
- 相邻等级差：仅统计实际 input hash 不同的配对。

### 6.3 H3：选择性

只使用 Organization 语料。分别拟合三个输出通道对 CASE level 的线性斜率，并计算：

`SI = β_organization − (β_content + β_language) / 2`

SI 的方向、CI 和 Holm 校正 p 值均报告；只报告显著性而不隐藏效应大小和 CI。

### 6.4 不确定性与多重比较

- 5,000 次分层聚类 bootstrap；种子由协议 seed 与模型/维度命名空间确定；
- 报告 percentile 95% CI；
- 单次项目内 H1 的 3 个单元、H2 的 3 个单元分别作 Holm step-down 校正；H3 为单个预注册模型配置，不作跨模型校正；
- α = 0.05，方向性假设为大于零/机会水平；
- 所有失败、collision、排除对数与实际有效 N 同时报告。

### 6.5 负对照与敏感性分析

1. 在 prompt/base 内置换 CASE 标签；MPA 应接近 0.5、QWK 接近 0；
2. 把三个输出通道替换为逐作文均值；SI 应接近 0；
3. Language 拟合 `CASE label ~ log(word_count + 1) + C(prompt)` 基线，报告 R² 和 MAE；
4. Language 另报九档均存在的共同 prompt 子集；
5. 复测报告三通道完全一致率与平均通道绝对差，量化未受控随机性。

## 7. 结果判定模板

| 判定 | 必要条件 | 可用措辞 |
|---|---|---|
| 单模型/维度 H1 | MPA > 0.5 且 Holm p < .05 | “该模型在该维度表现出单调敏感性” |
| 单模型/维度 H2 | QWK > 0 且 Holm p < .05 | “该模型可恢复部分 CASE 预设等级” |
| 稳定敏感性 | 两模型同一维度 H1、H2 均通过 | “跨两个预注册模型稳定” |
| Rubric-selective Organization | 上述条件 + 两模型 H3 均通过 | “对 Organization rubric-selective” |
| 未通过 | 任一必要条件不满足 | 报告估计值与 CI，不写“证明无效” |

Language 即使通过，也必须在正文和图表标注“探索性、存在长度与 prompt 混杂”。

## 8. 效度威胁

### 构念效度

CASE 等级是合成破坏强度，不等同于自然作文质量或人工评分。单调响应可能来自表面线索；Organization 的跨通道 SI 和负对照用于缩小但不能消除该解释。

### 内部效度

Content/Language 缺少完整 base 配对；Language 尤其受篇幅与 prompt 混杂。Codex CLI 不提供 temperature；复测只能估计而不能消除随机性。模型服务可能随时间发生后端漂移，CLI 指纹无法完全观察服务端变更。`standard` 与 `fast` 只控制服务处理层级，不能保证固定延迟；若跨项目混用速度模式，必须视为不同运行条件而不能合并为同配置重复测量。

### 统计结论效度

cluster 数而非作文行数决定有效独立信息；因此使用聚类 bootstrap 和宏平均。固定样本是资源约束设计，不能以显著性替代实际效应大小和 CI。

### 外部效度

结果只覆盖当前 DREsS_CASE、运行快照中的 Rubric、所选模型配置和运行时段，不代表其他语言、文体、教育阶段或真实高风险评分场景。

### 数据与伦理

受限数据处理依赖操作者确实获得授权；平台门禁不是法律或伦理委员会批准。论文与补充材料不得包含可还原正文的输入。

## 9. 完整性、偏差与变更规则

- 开始实验时自动保存不可变运行快照：协议 ID、seed、数据哈希、单个 Runner（含思考程度、速度模式、service tier 与 timeout）、CLI 指纹、Rubric、样本 Manifest 和统计代码依赖；操作者无需执行人工冻结。
- 正式运行后不允许删除或覆盖 attempt；人工重试保留原 attempt 与 input hash。
- 报告 JSON 与 SVG 均存 SHA-256；任何再分析必须产生新版本标签和变更说明。
- 实际偏离本方案时，在论文 deviations 小节逐条说明原因、发生时间、是否在解盲前以及对结论的影响。
- 平台实施阶段不自动发送真实作文，也不产生模型费用。

## 参考文献

1. Yoo, H., Han, J., Ahn, S.-Y., & Oh, A. (2025). [DREsS: Dataset for Rubric-based Essay Scoring on EFL Writing](https://aclanthology.org/2025.acl-long.659/). *ACL 2025*, 13439–13454. https://doi.org/10.18653/v1/2025.acl-long.659
2. González, E. F., Trejo, N. P., & Roux, R. (2017). [Assessing EFL university students' writing: A study of score reliability](https://doi.org/10.24320/redie.2017.19.2.928). *Revista Electrónica de Investigación Educativa, 19*(2), 91–103.
3. Keller, S. D., Lohmann, J., Trüb, R., Fleckenstein, J., Meyer, J., Jansen, T., & Möller, J. (2024). [Language quality, content, structure: What analytic ratings tell us about EFL writing skills at upper secondary school level in Germany and Switzerland](https://doi.org/10.1016/j.jslw.2024.101129). *Journal of Second Language Writing, 65*, 101129.
4. Cohen, J. (1968). Weighted kappa: Nominal scale agreement with provision for scaled disagreement or partial credit. *Psychological Bulletin, 70*(4), 213–220. https://doi.org/10.1037/h0026256
5. Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian Journal of Statistics, 6*(2), 65–70. https://www.jstor.org/stable/4615733
6. Efron, B., & Tibshirani, R. J. (1993). *An Introduction to the Bootstrap*. Chapman & Hall/CRC. https://doi.org/10.1201/9780429246593
