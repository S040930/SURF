"""正式实验3 两题合并分析：提取测试结果与训练记忆形成轨迹。

口径（与单题分析保持一致）：
- study = f3460bca-1a20-4760-99ef-661c27591418（正式实验3）
- 测试结果与训练评分分开导出，禁止混算
- 指标：normalized_absolute_diff（NAE，max_score=1 归一化）
- 聚合层：call 级用于描述，answer 级（10 answers × 3 orders 取均值）用于配对检验
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

BACKEND = Path("/Users/mac/Desktop/SURF/AI-Marking/backend")
OUT_DIR = Path("/Users/mac/Desktop/SURF/outputs/saf2_analysis/formal3_both_questions")
OUT_DIR.mkdir(parents=True, exist_ok=True)

STUDY_ID = "f3460bca-1a20-4760-99ef-661c27591418"
QUESTIONS = ["4.2_LM_v1.0", "4.13"]

# 后面的分析延用与单题分析一致的条件顺序
CONDITION_ORDER = [
    "no_memory",
    "retrieval_full",
    "retrieval_no_feedback",
    "mem0_full",
    "mem0_no_feedback",
    "amem_full",
    "amem_no_feedback",
]

url = "postgresql+psycopg2://mac@localhost:5511/ai_marking_experiment"
engine = create_engine(url)

sql = text(
    """
    SELECT
        c.id AS call_id, c.question_id, c.condition, c.framework, c.feedback_mode,
        c.order_variant, c.kind, c.status, c.model_score, c.max_score,
        (c.model_score - r.teacher_score) / NULLIF(c.max_score, 0) AS normalized_signed_diff,
        ABS(c.model_score - r.teacher_score) / NULLIF(c.max_score, 0) AS normalized_absolute_diff,
        c.input_tokens, c.total_tokens, c.latency_ms, c.attempt_count,
        c.failure_code, c.failure_summary,
        r.teacher_score, r.teacher_feedback, r.source_split, r.selection_kind,
        c.answer_id
    FROM ms_calls c
    JOIN ms_records r
        ON r.study_id = c.study_id AND r.answer_id = c.answer_id
    WHERE c.study_id = :study_id
      AND c.question_id IN :questions
      AND c.kind = 'score'
      AND c.status = 'succeeded'
    ORDER BY c.question_id, c.condition, c.answer_id, c.order_variant
    """
)

with engine.connect() as conn:
    df = pd.read_sql(sql, conn, params={"study_id": STUDY_ID, "questions": tuple(QUESTIONS)})
    manifest = conn.execute(
        text("SELECT data_manifest_json FROM ms_studies WHERE id = :study_id"),
        {"study_id": STUDY_ID},
    ).scalar_one()
if isinstance(manifest, str):
    manifest = json.loads(manifest)

df["condition"] = pd.Categorical(df["condition"], categories=CONDITION_ORDER, ordered=True)
test_df = df[df["selection_kind"] == "test"].copy()
train_df = df[df["selection_kind"] == "training"].copy()

step_map = {
    (question, order, answer_id): step
    for question, variants in manifest["orders"].items()
    for order, answer_ids in variants.items()
    if order.startswith("order_")
    for step, answer_id in enumerate(answer_ids, start=1)
}
train_df["train_step"] = [
    step_map[(row.question_id, row.order_variant, row.answer_id)]
    for row in train_df.itertuples()
]
train_df["memory_items_before"] = train_df.apply(
    lambda row: 0 if row["condition"] == "no_memory" else int(row["train_step"]) - 1,
    axis=1,
)

# 检查每条件样本量
n_check = test_df.groupby(["question_id", "condition"], observed=True).size().unstack("condition")
print("=== 测试集 score 调用样本量（call 级）===")
print(n_check)
assert (n_check == 30).all().all(), "call 级样本量必须为 30（10 answers × 3 orders）"

# answer 级聚合：每 answer 取 3 个 order 的 NAE 均值
answer_df = (
    test_df.groupby(["question_id", "condition", "answer_id"], observed=True)[
        ["normalized_absolute_diff", "normalized_signed_diff", "total_tokens"]
    ]
    .mean()
    .reset_index()
)

n_answer = answer_df.groupby(["question_id", "condition"], observed=True).size().unstack("condition")
print("\n=== answer 级样本量 ===")
print(n_answer)
assert (n_answer == 10).all().all(), "answer 级样本量必须为 10"

test_df.to_csv(OUT_DIR / "analysis_data_calls.csv", index=False)
answer_df.to_csv(OUT_DIR / "analysis_data_answers.csv", index=False)
train_df.sort_values(
    ["question_id", "condition", "order_variant", "train_step"]
).to_csv(OUT_DIR / "analysis_data_training.csv", index=False)
print(f"\n已保存: {OUT_DIR / 'analysis_data_calls.csv'}")
print(f"已保存: {OUT_DIR / 'analysis_data_answers.csv'}")
print(f"已保存: {OUT_DIR / 'analysis_data_training.csv'}")
