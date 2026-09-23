"""把训练集逐记录评分差距折线图注入正式实验3两题报告。

只读数据库；对 formal3_two_questions_report.html 原地增量写入
（幂等：已存在附录节则跳过）。图表 SVG 复用
app.experiment.memory_study.report_html._train_gap_charts_html。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path("/Users/mac/Desktop/SURF/AI-Marking/backend")
sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.experiment.memory_study.report_html import _train_gap_charts_html  # noqa: E402

OUT_DIR = Path("/Users/mac/Desktop/SURF/outputs/saf2_analysis/formal3_both_questions")
REPORT = OUT_DIR / "formal3_two_questions_report.html"
STUDY_ID = "f3460bca-1a20-4760-99ef-661c27591418"
QUESTIONS = ["4.2_LM_v1.0", "4.13"]

url = "postgresql+psycopg2://mac@localhost:5511/ai_marking_experiment"
engine = create_engine(url)

sql = text(
    """
    SELECT c.question_id, c.condition, c.order_variant, c.model_score,
           r.teacher_score, r.source_split, c.answer_id
    FROM ms_calls c
    JOIN ms_records r ON r.study_id = c.study_id AND r.answer_id = c.answer_id
    WHERE c.study_id = :study_id AND c.question_id IN :questions
      AND c.kind = 'score' AND c.status = 'succeeded'
    """
)
with engine.connect() as conn:
    df = pd.read_sql(
        sql, conn, params={"study_id": STUDY_ID, "questions": tuple(QUESTIONS)}
    )

rows_by_question: dict[str, list[dict]] = {}
for q in QUESTIONS:
    rows = []
    for _, r in df[df["question_id"] == q].iterrows():
        rows.append(
            {
                "condition": r["condition"],
                "order_variant": r["order_variant"],
                "answer_id": r["answer_id"],
                "teacher_score": float(r["teacher_score"]),
                "model_score": float(r["model_score"]),
                "split": "train" if r["source_split"] == "training" else "test",
            }
        )
    rows_by_question[q] = rows

charts = {}
for q in QUESTIONS:
    charts[q] = _train_gap_charts_html(rows_by_question[q])
    n_train = sum(1 for row in rows_by_question[q] if row["split"] == "train")
    print(f"{q}: train rows={n_train} charts_ok={bool(charts[q])}")

appendix = f"""
  <!-- ============ 五、附录：训练集逐记录评分差距 ============ -->
  <h2>五、附录：训练集逐记录评分差距（折线图）</h2>
  <p class="muted">补充诊断，供核对模型逐条打分与人工分的差距；正式结论以正文三、四节为准。每个条件一张折线图：横轴为该题训练答案（按答案 ID 数字排序），实线为教师分，虚线为三个历史顺序（order_1/2/3）下的模型分。</p>
  <h3>题 4.2_LM_v1.0（训练集）</h3>
  {charts['4.2_LM_v1.0']}
  <h3>题 4.13（训练集）</h3>
  {charts['4.13']}
"""

css = """
  .chart-grid { display: flex; flex-wrap: wrap; gap: 1rem; }
  .chart { margin: 0; flex: 1 1 40%; min-width: 300px; }
  .chart svg { width: 100%; height: auto; display: block; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
  .chart figcaption { font-size: 13px; color: var(--text-muted); margin-top: 6px; }
"""

src = REPORT.read_text(encoding="utf-8")
if "五、附录：训练集逐记录评分差距" in src:
    print("已存在附录节，跳过注入。")
    raise SystemExit(0)

updated = src.replace("</style>", css.rstrip("\n") + "\n</style>", 1)
anchor = "<footer>"
footer_idx = updated.find(anchor)
if footer_idx < 0:
    raise RuntimeError("找不到 <footer> 锚点")
updated = updated[:footer_idx] + appendix.lstrip("\n") + "\n" + updated[footer_idx:]

REPORT.write_text(updated, encoding="utf-8")
print(f"已注入并写回: {REPORT}")