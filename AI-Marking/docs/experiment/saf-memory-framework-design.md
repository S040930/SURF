# SAF 2.0 独立记忆框架研究设计（V3-r2）

当前新项目协议为 `saf-memory-framework-v3-r2`。原
`saf-memory-framework-v3` 仍作为历史基线读取和分析，但新项目默认不再使用它；两种协议的
数据、评分仪器和报告不得混合。两者共用 `ms_*` 表、迁移和执行基础设施。

## 研究问题

在同一批历史人工批改材料下，普通完整案例检索、Mem0、A-MEM 是否相对无记忆降低新回答评分的
归一化绝对误差（NAE）；去掉人工文字反馈后，误差与资源开销如何变化。研究只使用
`gpt-6-luna`，结论限定为 SAF 2.0 已选题目的新回答，不作跨模型总体推断。

## 冻结数据集

V3-r2 的正式六题集合固定为：

`12.2_PE`、`10.2_TC`、`8.2_MM`、`4.13`、`8.1_MM`、`4.3`。

`dataset.py` 直接读取带归档 SHA-256 的 SAF 2.0 数据，重新执行清洗、近重复和跨 split
审计；V3-r2 不再调用旧 V3 的题目选择结果。每题固定抽取 40 条 `training` 和 10 条
`unseen_answers`，训练/测试互斥。每题保存：

- 题目、参考答案和选择规则的 SHA-256；
- 两个 V4 历史顺序：数据集原始顺序 `original`，以及随机种子为 `42` 的逐题随机顺序 `shuffled`；
- 清洗前全历史分数的 `score_floor` / `score_ceiling` 与原始分数网格；
- 训练观察最高分、样本计数、排除组件、`protocol_fingerprint` 和总 manifest 哈希。

评分上下限是题目级冻结边界。模型可以返回连续小数；服务端拒绝低于 floor 或高于 ceiling 的
结果，不四舍五入、不投影到离散网格，也不自动裁剪。

## 实验条件与队列

四个主条件是 `no_memory`、`retrieval_full`、`mem0_full`、`amem_full`；
`retrieval_no_feedback`、`mem0_no_feedback`、`amem_no_feedback` 是可选的独立 wave。
检索剂量固定为 top-5。正式研究仍对训练答案评分，训练评分发生在当前答案写入记忆之前；
测试评分只在该题对应的 40 条训练记忆全部提交后开放。每个题目×模型×条件×历史顺序流严格
串行，四个固定 Luna 槽之间并行。

## V3-r2 评分仪器

评分请求只发送以下字段：

`question`、`reference_answer`、`answer`、`score_floor`、`score_ceiling`、`memory`。

`memory` 只投影历史学生答案、教师分数和可选教师反馈。`condition`、答案/题目 ID、相似度、
rank、原始框架 payload、`verification_feedback` 和测试答案的人工标签只保留在审计记录中，
不进入评分模型请求。参考答案只作为当前题目的评分标准，不写入记忆。

V3-r2 的系统指令、载荷字段、信封版本和 memory 投影写入
`config_json.scoring_context`，并生成独立的 `instrument_sha256`。冻结时校验该声明，评分
调用还记录协议 ID、请求哈希和 token 计数。只读校验命令为：

```bash
cd AI-Marking/backend
.venv/bin/python scripts/verify_scoring_instrument.py
```

旧 V3 的评分载荷和历史结果保留原样；V3 结果不能被当作 V3-r2 结果追加或合并。

## 记忆实现与审计

普通检索、Mem0 和 A-MEM 统一使用固定版本的 OpenAI-compatible embedding 服务；普通检索使用
余弦相似度 top-5。每个题目、条件和历史顺序有独立 memory store。训练写入成功后原子提交
数据库快照与文件快照，测试只读取最终快照。框架内部调用通过 Codex bridge 写入
`ms_framework_invocations`；失败、快照哈希异常、依赖不完整或协议漂移都会阻止继续执行。

`no_memory` 不创建 memory store。公开导出不包含学生正文、完整 prompt、密钥或验证标签；本地
审计可以读取受限 `ms_records` 和审计载荷。

## 分析口径

主指标只使用测试集。先在同一题、同一答案内平均两个历史顺序，再在题内平均，最后对六题
等权；主比较为记忆条件相对 `no_memory` 的 NAE，记忆增益定义为
`NAE(no_memory) - NAE(memory)`。反馈消融比较 no-feedback 与 full。训练轨迹仅作记忆形成
诊断，不进入测试主指标。

报告是描述性、探索性的：不做 bootstrap、其他重采样、显著性检验或总体置信区间；失败评分
保持缺失，绝不按零分计。评分上限、下限、题目方差和完整性审计必须从 V3-r2 manifest 重新
计算，不能沿用旧 V3 结论。

## 规模与资源

正式基础 wave 为 `2,400` 次评分和 `1,440` 次记忆写入；加入 no-feedback wave 后为
`4,200` 次评分和 `2,880` 次记忆写入。数据审计目标低于 2 分钟，平台 Python 进程目标低于
1 GB 内存；embedding 缓存有界，磁盘按已提交快照和最大 attempt 的两倍预留。若运行超过
5 分钟或 1 GB，需要补充性能分析。
