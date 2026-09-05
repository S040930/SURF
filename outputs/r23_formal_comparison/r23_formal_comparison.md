# r23 DREsS_CASE Rubric Sensitivity — 两个正式实验结果对比

> 生成时间：2026-09-03　·　协议：`r23-dress-case-rubric-sensitivity-2026-08-v1`　·　固定样本 seed `20260830`（两模型同一 2307 槽 + 225 复测槽，去重后 2293 个唯一输入一一对应）

## 1. 实验条件

| 项目 | 模型 | reasoning | speed | service tier | timeout | CLI | 状态 | 报告哈希 |
|---|---|---|---|---|---|---|---|---|
| 正式实验1 | gpt-5.6-luna | medium | fast | fast | 60s | codex-cli 0.147.0 | completed / locked | `855ad01f…` |
| terra正式 | gpt-5.6-terra | medium | standard | default | 120s | codex-cli 0.151.0 | completed / locked | `bf5c01b5…` |

运行可靠性：luna 4 次失败（3 中断 + 1 超时）、terra 24 次失败（19 超时 + 4 中断 + 1 非法输出），均人工重试成功，两项目最终全部调用成功。

## 2. 逐维度统计


### gpt-5.6-luna

| 维度 | 单调性 H1 (MPA) | 区分度 H2 (QWK) | 其他 |
|---|---|---|---|
| Content (n=540) | MPA **0.771** [0.749, 0.795]  Holm p=0.0006  ✅ | QWK **0.512** [0.429, 0.589]  Holm p=0.0006  ✅ | MAE 0.930  ρ 0.662  Δ(5−1) 1.833 |
| Organization (n=1020) | MPA **0.699** [0.684, 0.714]  Holm p=0.0006  ✅ | QWK **0.346** [0.290, 0.401]  Holm p=0.0006  ✅ | MAE 1.079  ρ 0.433  Δ(5−1) 1.475 |
| Language (n=747) | MPA **0.757** [0.714, 0.801]  Holm p=0.0006  ✅ | QWK **0.239** [0.200, 0.342]  Holm p=0.0006  ✅ | MAE 1.437  ρ 0.486  Δ(5−1) 1.512 |


### gpt-5.6-terra

| 维度 | 单调性 H1 (MPA) | 区分度 H2 (QWK) | 其他 |
|---|---|---|---|
| Content (n=540) | MPA **0.779** [0.756, 0.801]  Holm p=0.0006  ✅ | QWK **0.534** [0.459, 0.608]  Holm p=0.0006  ✅ | MAE 0.831  ρ 0.608  Δ(5−1) 1.658 |
| Organization (n=1020) | MPA **0.713** [0.699, 0.727]  Holm p=0.0006  ✅ | QWK **0.431** [0.391, 0.471]  Holm p=0.0006  ✅ | MAE 0.875  ρ 0.472  Δ(5−1) 1.363 |
| Language (n=747) | MPA **0.783** [0.743, 0.826]  Holm p=0.0006  ✅ | QWK **0.435** [0.404, 0.514]  Holm p=0.0006  ✅ | MAE 1.054  ρ 0.656  Δ(5−1) 1.675 |


## 3. Organization 选择性 (H3)

| 模型 | SI | 95% CI | Holm p | 斜率 Content | 斜率 Organization | 斜率 Language |
|---|---|---|---|---|---|---|
| gpt-5.6-luna | **0.164** | [0.131, 0.196] | 0.0002 | 0.191 | 0.315 | 0.112 |
| gpt-5.6-terra | **0.174** | [0.148, 0.199] | 0.0002 | 0.171 | 0.294 | 0.069 |

## 4. 敏感性分析与负对照

- **置换负对照**：两模型全部单元的 MPA 置换均值落在 0.500 附近（luna 0.4995–0.5018，terra 0.5002–0.5005），QWK 置换均值落在 0 附近（−0.0001 至 0.0108）；逐作文均值的 SI 负对照为 0.0000。
- **Language 长度/prompt 基线**：R²=0.496，MAE=0.729（两模型相同，因为基线只依赖输入标签与篇幅）。
- **Language 共同 prompt 子集**（5 个 prompt，n=586）：luna MPA=0.694、QWK=0.230；terra MPA=0.749、QWK=0.427。方向与全样本一致。
- **复测一致性**（各 225 对）：luna 三通道完全一致率 39.6%、平均通道绝对差 0.236；terra 33.3%、0.281。Codex CLI 不暴露 temperature，此随机性未受控，但远小于效应量。

## 5. 结论（按预注册判定模板）

两个模型在三个维度均通过 H1（MPA>0.5）与 H2（QWK>0），Holm 校正 p<0.001；Organization 两模型均通过 H3（SI>0）。
合并两份单模型报告判断跨模型稳定性：Content / Organization / Language 的 H1、H2 在两模型上同时通过，Organization 的 H3 也在两模型上通过，
因此可表述为**跨两个预注册模型对 CASE 预设等级表现出稳定单调敏感性，且 Organization 维度为 rubric-selective**。

**限定**：QWK 是 CASE intended-label recovery，不代表与人工评分一致；结果不推广到真实作文评分准确性、公平性或教学有效性；Language 因篇幅/prompt 混杂按探索性处理。

## 6. 复现与数据

- 报告 JSON：`report_gpt-5.6-luna.json`、`report_gpt-5.6-terra.json`（自实时实验库导出，报告哈希与项目记录一致）。
- 图表：`01_mpa_by_dimension.{png,svg}`、`02_qwk_by_dimension.{png,svg}`、`03_level_trends.{png,svg}`、`04_selectivity.{png,svg}`。
- 说明：`outputs/r23_experiment_results.dump`（2026-08-31 导出）仅含 luna 正式实验；terra 正式数据在端口 5511 的实时实验库 `ai_marking_experiment` 中，如需长期归档建议补充导出。
