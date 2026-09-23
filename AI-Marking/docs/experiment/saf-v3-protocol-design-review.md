# SAF 2.0 V3-r2 研究设计复核

## 复核结论

V3-r2 保留 V3 的实验队列：Luna-only、40/10 训练测试划分、V4 的两种历史顺序、四个主
条件、可选 no-feedback wave、top-5 检索和 NAE 主指标。新协议只复用已经冻结的六题选择
结果，不复用任何旧的题目集合、评分结果或评分仪器。

正式题为：`12.2_PE`、`10.2_TC`、`8.2_MM`、`4.13`、`8.1_MM`、`4.3`。

## 关键控制

1. **数据冻结**：manifest 同时保存题目哈希、训练/测试答案 ID、原始与 seed=42 随机两个顺序、每题 score floor
   / ceiling、分数网格、数据完整性审计和 `protocol_fingerprint`。
2. **仪器冻结**：V3-r2 的评分请求只含 question、reference_answer、answer、score_floor、
   score_ceiling、memory；`scoring_context` 与 `instrument_sha256` 在创建/冻结时保存并校验。
3. **记忆盲化**：评分模型看到的历史 memory 只含学生答案、教师分数和可选教师反馈。条件、
   ID、相似度、rank、原始框架 payload、验证反馈和测试人工标签不进入评分请求。
4. **边界校验**：连续小数分数合法；低于题目 floor 或高于 ceiling 的评分失败，不强制离散
   网格，不自动裁剪。
5. **协议隔离**：旧 V3 项目仍可读取、分析和审计；新项目默认 V3-r2，数据库查询、报告和
   工件必须按 protocol ID 隔离。

## 风险与审计要求

- 六题的方差、预算和结论范围必须重新审计，不能直接引用旧 V3 的数据质量结论。
- 训练阶段继续评分，且评分发生在当前答案写入 memory 之前；测试仅在 40 条训练写入全部
  提交后开放。
- 失败调用保持缺失，不按零分计；分析关闭 bootstrap、重采样、显著性检验和总体推断。
- `scripts/verify_scoring_instrument.py` 必须在冻结前、恢复运行前和代码变更后执行。
