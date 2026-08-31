# 大模型外部记忆（External Memory for LLMs）调研笔记

> 目的：为 AI-Marking 实验（NM / CRM / ARM 三种记忆条件）提供「外部记忆怎么做」的参照。
> 口径：外部记忆 = 存在模型参数之外的记忆，通过上下文注入；不包括 fine-tuning。
> 来源：2026-08 检索确认（GitHub API、官方 README、Bing 摘要、arXiv 摘要片段）；未亲自打开全文的机制用「(常识，未逐条核验)」标注。
> 关联实验：SURF AI-Marking r21 pilot（单模型 gpt-5.6-luna，题目 4.13 / 5.7，条件 NM/CRM/ARM）。

---

## 0. 一句话脉络

业界做「外部记忆」基本都在回答五个问题，任何记忆系统 = 这五个答案的组合：

1. **记忆长什么样**（结构）：自然语言文本块？key-value 事实？向量？知识图谱？代码库？
2. **怎么写入**（编码）：直接存原文？LLM 抽取要点？事后反思/总结？冲突怎么合并？
3. **怎么更新/遗忘**（维护）：覆写？追加？失效标注（不删除）？遗忘曲线衰减？
4. **怎么检索**（读取）：全文灌入？向量相似度？recency×importance×relevance 打分？图遍历？
5. **怎么进上下文**（注入）：系统提示前缀？agent 自编辑工具调用？RAG 检索结果拼装？

---

## 1. 代表性学术论文

### 1.1 MemGPT / Letta（记忆分层 + agent 自编辑记忆）
- 出处：arXiv 2310.08560，MemGPT（后改名 Letta）。2026 起源码迁至 `letta-ai/letta-code`（landing page 在 `letta-ai/letta`，约 24k star）。
- 核心思想：把 LLM agent 当操作系统，做**多级记忆**。
- 结构：**core memory**（始终在上下文里的工作集，一段固定文本块）+ **archival memory**（外部存储，按需换入换出，对应 OS 的分页）。
- 写入/更新：模型**自己通过工具调用编辑记忆**（如 `core_memory_replace` / `memory_insert` / `memory_search`），类似进程调 OS 的 paging。
- 检索：上下文满了就把旧内容「分页」到 archival，需要时检索回来。
- 进上下文：core memory 常驻 system prompt；archival 通过函数调用按需取回。
- 与你实验的对照：MemGPT 的「记忆在上下文里的文本块」≈ 你的 CRM（规则文本）/ARM（量表文本）注入方式；它的「自编辑」是更进阶的形态（agent 改自己的记忆）。

### 1.2 MemoryBank（遗忘曲线 + 情景/语义记忆）
- 出处：arXiv 2305.10250，MemoryBank；配套个人助手 SiliconFriend。
- 核心：模拟人类记忆的**存-取-更新**闭环。
- 结构：长期记忆库（可含情景记忆 episodic + 语义记忆 semantic 两类内容）。
- 更新：持续对对话做**总结/性格画像抽取**，随交互演进。
- 遗忘：用 **Ebbinghaus 遗忘曲线**（记忆强度随时间指数衰减，强化可重置）来调度「哪些旧记忆该被淡出、哪些该强化」。
- 进上下文：按遗忘曲线+相关性挑出的记忆以文本形式注入提示。
- 与你实验的对照：它是「带遗忘」的记忆；你 r21 是固定记忆块（无遗忘）。这提示可以加「记忆随历史滚动/衰减」的变体。

### 1.3 Generative Agents（记忆流 + 反思）
- 出处：arXiv 2304.03442，Stanford 的交互式智能体小镇（Smallville）。
- 结构：**memory stream**——一条按时间追加的自然语言记忆列表（每个记忆含时间戳）。
- 写入：观察即时追加成一条记忆；**反思（reflection）**——积累到一定量后由 LLM 生成更高层、更抽象的自我总结，回写到 memory stream。
- 检索：打分 = **recency（新近）× importance（重要）× relevance（相关）** 三者加权，取 top-k。
- 进上下文：检索出的记忆拼进 prompt，驱动规划与行动。(机制为领域常识，未逐条核验原文)
- 与你实验的对照：它的「反射=把多条原始经历总结成抽象规则」非常接近你的 ARM（从历史分数抽象出量表）——可以引用这条理论依据支撑 ARM 的设计动机。

### 1.4 Reflexion（语言强化：失败教训入记忆）
- 出处：NeurIPS 2023，`noahshinn/reflexion`（约 3.2k star）。
- 核心：不用梯度，用**自然语言「自我反思」作为强化信号**。
- 结构：一段 **episodic memory buffer（情景记忆缓冲）**——每次尝试后把「这次错在哪、下次该怎么改」写进去。
- 写入：LLM 根据试错结果生成反思文本，追加进 buffer。
- 进上下文：下一轮把这段反思文本注入提示。
- 与你实验的对照：它证明「把失败/修正经验写成文本喂给模型」确实能改善后续行为——这是你 CRM「规则记忆能纠正评分」假说的同类证据。

### 1.5 HippoRAG（海马索引理论 + 知识图谱检索）
- 出处：arXiv 2405.14831，NeurIPS 2024。
- 核心：用 **hippocampal indexing theory**（海马体索引理论）解释记忆——LLM 先抽取实体/关系建一个轻量知识图谱当作「记忆索引」，查询时用 **Personalized PageRank** 在图上游走取关联节点。
- 结构：知识图谱（节点=实体，边=关系）。
- 写入：每来一段新信息，用 LLM 抽取实体与关系，增量写入图谱。
- 检索：单跳与多跳推理都用 PPR 在图上检索，而非表面文本匹配。
- 进上下文：检索到的图节点（对应原文片段）拼进上下文。
- 与你实验的对照：它是「结构化图记忆」的标杆；你若要给评分规则建关联（如 4.13 与 5.7 共用某条规则），可借鉴图结构。

### 1.6 Voyager（技能库 = 程序化记忆）
- 出处：NVIDIA + 加州理工，Minecraft 探索 agent；官方 repo `MineDojo/Voyager`。(机制为领域常识，未逐条核验)
- 核心：**skill library（技能库）**——把验证过的、可复用的代码程序作为「程序化记忆」。
- 写入：探索中产生可执行代码，验证成功后存入技能库，并带描述/接口。
- 检索：新任务到来时，检索库中匹配的技能代码注入上下文复用。
- 与你实验的对照：CRM 的「规则」若写成可执行判定逻辑（而非纯文本），就是 Voyager 式的程序化记忆——更稳但更难维护。

### 1.7 CoALA（认知架构的通用框架）
- 出处：arXiv 2309.02427《Cognitive Architectures for Language Agents》。
- 贡献：把 agent 记忆统一定义为 **working memory（工作记忆，即上下文）** 与 **long-term memory（长期记忆，细分为 episodic / semantic / procedural）**，动作分 **internal（推理）与 external（工具/环境交互）**。
- 价值：给你一套给条件命名的标准词表：NM=基本只有 working memory；CRM=长期记忆里的 semantic（规则）；ARM=长期记忆里的 semantic（抽象量表/统计）。写论文时用这套词表能跟文献对齐。(机制为领域常识，未逐条核验原文)

### 1.8 Graphiti / Zep（时间感知上下文图）
- 出处：arXiv 2501.13956《Zep: A Temporal Knowledge Graph Architecture for Agent Memory》；`getzep/graphiti`（约 30k star），Zep 的底层。
- 结构：**temporal context graph**——节点=实体（带随时间演化的摘要），边=事实/关系（三元组），每条边有**有效性窗口（validity window）**；所有派生事实都追溯回原始 **episode**（来源数据流）。
- 写入：从非结构化数据**自主抽取实体与事实**（依赖模型结构化 JSON 输出），增量入图，**无需批量重算**。
- 更新/遗忘：信息变化时**旧事实被标为失效（invalidate）而非删除**；支持「现在是什么」和「过去某时刻是什么」的双时查询（bi-temporal）。
- 检索：**混合检索** = 语义向量 + BM25 关键词 + 图遍历三路并行融合。
- 冲突：靠自动失效 + 时间历史保留处理矛盾信息。
- 与你实验的对照：这是最贴近「评分记忆」的工程范式——教师给分的历史就是「随时间变化的事实」；可用它的「事实带时间窗+失效不删除」来处理学生水平随时间变化、或评分标准变更的版本。

---

## 2. 主流开源框架（怎么落地的）

| 框架 | star 量级 | 记忆结构 | 写入/更新 | 检索 | 进上下文 |
|---|---|---|---|---|---|
| **Mem0** | ~64k | 向量 + 图（实体链接）混合存储 | 2026 新算法：**单次 ADD-only 抽取**（一次 LLM 调用，不覆写不删除）；老算法是两阶段 抽取→UPDATE/DELETE | 多信号：语义 + BM25 + 实体匹配，并行打分融合；时间感知排序 | 检索到的记忆拼进 system/user |
| **Letta (MemGPT)** | ~24k | core memory 文本块 + archival 向量 | 模型工具调用自编辑（replace/insert/search） | 分页式换入换出 | core 常驻，archival 按需 |
| **Zep** | 云平台（底层 Graphiti ~30k） | 时间上下文图 | 增量抽取入图；事实失效不删 | 混合（语义+BM25+图） | 检索结果注入 |
| **LangGraph** | ~40k | **checkpointer（状态检查点）** + thread 会话状态 | 每个节点执行后自动存状态快照，可随时回滚/恢复 | 按 thread/checkpoint 精确恢复 | 状态整体恢复进上下文 |
| **MemoryBank** | 论文 | 长期记忆库 | 对话总结 + 性格画像 | Ebbinghaus 遗忘曲线 + 相关度 | 精选记忆注入 |
| **Reflexion** | 论文(3.2k) | episodic buffer | 反思文本追加 | 全部/最近注入 | 注入提示 |
| **HippoRAG** | 论文 | 知识图谱 | LLM 抽实体/关系增量 | Personalized PageRank | 图节点对应原文 |

> 注：GitHub star 数取自 2026-08 检索，仅作量级参考。

---

## 3. 行业/社区「记忆分类」共识

- **按时长**：工作记忆（working，= 上下文窗口）/ 短期 / 长期。
- **按内容**：情景（episodic，具体发生过的事，如「第 3 次评 4.13 给了 0.8」）/ 语义（semantic，抽象知识，如「评分规则：结论点 0.5 分」）/ 程序（procedural，怎么做，如「先查 rubric 再打分」）。
- **典型演进**：原始 RAG（向量文本块）→ 结构化事实记忆（key-value / 图）→ agent 自编辑记忆（MemGPT）→ 时间感知图记忆（Graphiti）→ 遗忘/衰减（MemoryBank）→ 程序化技能库（Voyager）。

---

## 4. 对你实验（NM / CRM / ARM）的直接启示

1. **ARM 有文献支撑但注意命名**：Generative Agents 的「反思=从实例抽象出高层规则」正是 ARM 的动机；但文献里这叫 semantic memory 的「schema/统计抽象」，写论文时建议用 CoALA 词表对齐（semantic vs episodic）。
2. **CRM 与 ARM 的本质差异 = 记忆内容的抽象层级**，文献里普遍发现**更高层抽象（反思/语义）在小样本下更稳**（Generative Agents、MemGPT 的总结机制），与你 r21「ARM 在 4.13 上反而更差（NAE 0.5067 vs CRM 0.4667）」形成有趣对照——值得讨论「为什么更高层抽象没赢」。
3. **记忆的写入质量是成败关键**：Mem0 新算法强调「单次抽取、不覆写」，Graphiti 强调「依赖模型结构化 JSON 抽取、失败会丢事实」——你的 ARM 量表/CRM 规则是**离线人工构造**的，跳过了抽取环节，这是一大优势（可复现、无抽取噪声），也是和这些系统不同的点，值得写进边界。
4. **遗忘/失效机制是下一步候选变体**：所有「真实记忆」系统都有遗忘（MemoryBank 曲线、Graphiti 失效不删）。你当前固定记忆块不变，可以加一个「记忆随历史衰减/替换」的条件做对照。
5. **时间感知对评分有意义**：教师标准会变（Graphiti 双时模型）。若后续做长期运行，可记录「某条规则何时生效、何时失效」，避免旧标准污染新评分。
6. **检索式记忆 vs 全量注入**：你目前是**全量注入**（记忆块固定、无检索）。若题目/规则多到放不下，可参考 Mem0/HippoRAG 做检索式注入（按题目检索相关规则），但注意会引入检索误差。

---

## 5. 对本项目（AI-Marking r21）的适用性评估

结论先行：对批改实验，**记忆「装什么」比「怎么存/怎么检索」更重要**。r21 宏平均 memory−NM = +0.0175（记忆未跑赢无记忆），瓶颈不在机制而在记忆内容。据此分档：

### 高匹配（推荐优先尝试）
| 候选 | 依据 | 具体怎么做 |
|---|---|---|
| **纠错/案例记忆**（Reflexion 式） | 补上「模型−教师打分差异」这个最缺的维度 | 每次评完把「模型给 x / 教师给 y / 答案要点」写成情景记忆注入下一轮；NAE 口径一致、不改结构，是 CRM/ARM 之外最自然的第四变体「从反馈学习」 |
| **版本化评分标准**（Graphiti 式） | 标准会版本化，失效标注比删除安全 | 每条规则/量表带「生效时间窗」，标准更新时旧规则失效不删；附带来源追溯，便于向教师解释评分 |
| **只增不改的记忆**（Mem0 式） | 直接回答「记忆数据越多误差越低吗」 | 新教师评分持续追加进量表/规则，不覆写；延续 h=10 vs h=20 的容量探针 |
| **反思抽象作为 ARM 依据**（Generative Agents 式） | 支撑 ARM 设计动机 | 引用「反思=从实例抽象出高层规则」；但 r21 ARM 在 4.13 反而更差（0.5067 vs CRM 0.4667），需讨论「为什么高层抽象没赢」 |

### 中等（有条件才考虑）
- **图谱记忆**（HippoRAG）：仅当题目多到规则需跨题共享/复用；当前 2 题是过度设计。
- **遗忘曲线**（MemoryBank）：仅当有证据表明教师标准随时间漂移；批改标准追求稳定，遗忘反而有害。

### 不适合
- **MemGPT 自编辑**：模型自改评分规则→随机性、破坏可复现性，受控实验不想要；分页换入换出对「小且可全量注入」的记忆是过度设计。
- **检索式 top-k 注入**：批改漏掉一条规则风险高；记忆小、可全量注入，无需引入检索误差。
- **LangGraph checkpointer**：agent 基建（状态快照），本项目已有 DB 管 run，不需要。

---

## 6. 主要来源（URL）

- MemGPT arXiv: https://arxiv.org/abs/2310.08560 ; Letta: https://github.com/letta-ai/letta
- MemoryBank arXiv: https://arxiv.org/abs/2305.10250
- Generative Agents arXiv: https://arxiv.org/abs/2304.03442
- Reflexion arXiv: https://arxiv.org/abs/2303.11366 ; repo: https://github.com/noahshinn/reflexion
- HippoRAG arXiv: https://arxiv.org/abs/2405.14831
- CoALA arXiv: https://arxiv.org/abs/2309.02427
- Voyager arXiv: https://arxiv.org/abs/2305.16291 ; repo: https://github.com/MineDojo/Voyager
- Graphiti/Zep arXiv: https://arxiv.org/abs/2501.13956 ; repo: https://github.com/getzep/graphiti
- Mem0: https://github.com/mem0ai/mem0 ; LangGraph: https://github.com/langchain-ai/langgraph

> 说明：arXiv ID 中 Reflexion(2303.11366)、Voyager(2305.16291)、CoALA(2309.02427) 的 ID 与 1.4/1.6/1.7 的机制描述来自领域常识，未在本会话内逐条核验页面；MemGPT/MemoryBank/Generative Agents/HippoRAG/Graphiti 的机制要点经 2026-08 检索确认。
