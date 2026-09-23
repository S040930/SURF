"""正式实验 3 已完成两题的描述性记忆效果分析（无显著性检验）。"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd


OUT_DIR = Path(__file__).resolve().parent
MODEL = "gpt-5.6-luna"
PRIMARY = ["no_memory", "retrieval_full", "mem0_full", "amem_full"]
MEMORY = PRIMARY[1:]
ORDERS = ["order_1", "order_2", "order_3"]
LABELS = {
    "no_memory": "无记忆",
    "retrieval_full": "确定性检索·含反馈",
    "mem0_full": "Mem0·含反馈",
    "amem_full": "A-MEM·含反馈",
}


calls = pd.read_csv(OUT_DIR / "analysis_data_calls.csv")
training_path = OUT_DIR / "analysis_data_training.csv"
training = pd.read_csv(training_path) if training_path.exists() else pd.DataFrame()

# 同一答案先平均三条历史顺序。
answers = (
    calls.groupby(["question_id", "condition", "answer_id"], observed=True)
    .agg(nae=("normalized_absolute_diff", "mean"), signed=("normalized_signed_diff", "mean"))
    .reset_index()
)

question_nae = (
    answers[answers["condition"].isin(PRIMARY)]
    .groupby(["question_id", "condition"], observed=True)["nae"]
    .mean()
)
condition_nae = {}
for condition in PRIMARY:
    by_question = {
        question: float(value)
        for (question, current), value in question_nae.items()
        if current == condition
    }
    condition_nae[condition] = {
        "estimate": float(pd.Series(by_question).mean()),
        "by_question": by_question,
        "n_answers": int((answers["condition"] == condition).sum()),
    }

memory_gain = {}
baseline = answers[answers["condition"] == "no_memory"].set_index(["question_id", "answer_id"])["nae"]
for condition in MEMORY:
    target = answers[answers["condition"] == condition].set_index(["question_id", "answer_id"])["nae"]
    common = baseline.index.intersection(target.index)
    gain = baseline.loc[common] - target.loc[common]
    by_question = {question: float(values.mean()) for question, values in gain.groupby(level=0)}
    memory_gain[condition] = {
        "estimate": float(pd.Series(by_question).mean()),
        "by_question": by_question,
        "n_answers": int(len(gain)),
        "improved": int((gain > 1e-12).sum()),
        "tied": int((gain.abs() <= 1e-12).sum()),
        "worse": int((gain < -1e-12).sum()),
    }

order_means = (
    calls[calls["condition"].isin(PRIMARY)]
    .groupby(["question_id", "condition", "order_variant"], observed=True)["normalized_absolute_diff"]
    .mean()
)
order_sensitivity = {}
for condition in PRIMARY:
    by_question = {}
    for question in sorted(calls["question_id"].unique()):
        values = {
            order: float(order_means.loc[(question, condition, order)])
            for order in ORDERS
        }
        by_question[question] = {
            "order_nae": values,
            "range": max(values.values()) - min(values.values()),
        }
    order_sensitivity[condition] = {
        "mean_range": float(pd.Series([value["range"] for value in by_question.values()]).mean()),
        "by_question": by_question,
    }

feedback_ablation = {}
for framework in ("retrieval", "mem0", "amem"):
    full = answers[answers["condition"] == f"{framework}_full"].set_index(["question_id", "answer_id"])["nae"]
    no_feedback = answers[answers["condition"] == f"{framework}_no_feedback"].set_index(["question_id", "answer_id"])["nae"]
    common = full.index.intersection(no_feedback.index)
    delta = no_feedback.loc[common] - full.loc[common]
    by_question = {question: float(values.mean()) for question, values in delta.groupby(level=0)}
    feedback_ablation[framework] = {
        "estimate": float(pd.Series(by_question).mean()),
        "by_question": by_question,
        "n_answers": int(len(delta)),
        "direction": "positive_means_text_feedback_reduced_error",
    }

training_trajectory = {}
if not training.empty:
    for keys, group in training.groupby(["question_id", "condition", "order_variant"], observed=True):
        question, condition, order = keys
        group = group.sort_values("train_step")
        records = []
        running = []
        for row in group.itertuples():
            running.append(float(row.normalized_absolute_diff))
            records.append({
                "train_step": int(row.train_step),
                "answer_id": str(row.answer_id),
                "memory_items_before": int(row.memory_items_before),
                "teacher_score": float(row.teacher_score),
                "model_score": float(row.model_score),
                "nae": float(row.normalized_absolute_diff),
                "cumulative_mean_nae": float(sum(running) / len(running)),
            })
        training_trajectory[f"{condition} / {question} / {order}"] = records

result = {
    "report_type": "two_question_interim_descriptive",
    "analysis_amendment": "adopted_after_reviewing_first_two_formal_questions",
    "primary": {
        "condition_nae": {MODEL: condition_nae},
        "memory_gain_vs_no_memory": {MODEL: memory_gain},
    },
    "secondary": {
        "history_order_sensitivity": {MODEL: {key: value for key, value in order_sensitivity.items() if key != "no_memory"}},
        "no_memory_run_variation": {MODEL: order_sensitivity["no_memory"]},
        "feedback_ablation": {MODEL: feedback_ablation},
    },
    "diagnostics": {"training_memory_trajectory": {MODEL: training_trajectory}},
    "inference": {"significance_tests": False, "confidence_intervals": False},
}
(OUT_DIR / "stats_results_both.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt(value):
    return "—" if value is None else f"{float(value):.4f}"


nae_rows = "".join(
    f"<tr><td>{html.escape(LABELS[c])}</td><td>{fmt(v['estimate'])}</td><td>{v['n_answers']}</td></tr>"
    for c, v in condition_nae.items()
)
gain_rows = "".join(
    f"<tr><td>{html.escape(LABELS[c])}</td><td>{fmt(v['estimate'])}</td>"
    f"<td>{v['improved']} / {v['tied']} / {v['worse']}</td><td>{v['n_answers']}</td></tr>"
    for c, v in memory_gain.items()
)
order_rows = "".join(
    f"<tr><td>{html.escape(LABELS[c])}</td><td>{html.escape(q)}</td>"
    f"<td>{' / '.join(fmt(detail['order_nae'][order]) for order in ORDERS)}</td>"
    f"<td>{fmt(detail['range'])}</td></tr>"
    for c, values in order_sensitivity.items()
    for q, detail in values["by_question"].items()
)
feedback_rows = "".join(
    f"<tr><td>{html.escape(framework)}</td><td>{fmt(values['estimate'])}</td><td>{values['n_answers']}</td></tr>"
    for framework, values in feedback_ablation.items()
)
trajectory_blocks = "".join(
    "<details><summary>" + html.escape(key) + f"（{len(records)} 步）</summary><table>"
    "<tr><th>步骤</th><th>答案</th><th>评分前记忆数</th><th>人工</th><th>模型</th><th>NAE</th><th>累计 NAE</th></tr>" +
    "".join(
        f"<tr><td>{r['train_step']}</td><td>{html.escape(r['answer_id'])}</td>"
        f"<td>{r['memory_items_before']}</td><td>{fmt(r['teacher_score'])}</td>"
        f"<td>{fmt(r['model_score'])}</td><td>{fmt(r['nae'])}</td>"
        f"<td>{fmt(r['cumulative_mean_nae'])}</td></tr>" for r in records
    ) + "</table></details>"
    for key, records in training_trajectory.items()
)

document = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>正式实验3 · 两题记忆效果报告</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;max-width:1100px;margin:32px auto;color:#202124}}
table{{border-collapse:collapse;width:100%;margin:10px 0 24px}}th,td{{border:1px solid #ddd;padding:7px;text-align:left}}th{{background:#f5f5f5}}
.note{{background:#fff7df;border-left:4px solid #d89b18;padding:12px}}details{{margin:8px 0}}summary{{cursor:pointer;font-weight:600}}
</style></head><body><h1>记忆功能对大模型评分的影响：两题中期结果</h1>
<p class="note">这是查看前两题后登记的描述性分析修订。无 bootstrap、置信区间或显著性检验；正式结论等待六题完成。</p>
<h2>NEW 测试集 NAE</h2><table><tr><th>条件</th><th>NAE</th><th>答案数</th></tr>{nae_rows}</table>
<h2>记忆增益</h2><p>定义为无记忆 NAE − 记忆 NAE，正值表示改善。</p><table><tr><th>条件</th><th>增益</th><th>改善 / 持平 / 变差</th><th>答案数</th></tr>{gain_rows}</table>
<h2>历史顺序敏感性</h2><p>无记忆行仅表示重复运行波动。</p><table><tr><th>条件</th><th>题目</th><th>order 1 / 2 / 3 NAE</th><th>极差</th></tr>{order_rows}</table>
<h2>补充：教师文字反馈效应</h2><p>数值为无反馈 NAE − 完整反馈 NAE，正值表示文字反馈降低误差。</p><table><tr><th>框架</th><th>NAE 差值</th><th>答案数</th></tr>{feedback_rows}</table>
<h2>训练阶段记忆形成轨迹</h2><p>第 t 步评分发生在当前答案写入前；逐答案 NAE 仅作过程诊断。</p>{trajectory_blocks or '<p>尚未导出训练评分；请先运行 extract_both.py。</p>'}
</body></html>"""
(OUT_DIR / "formal3_two_questions_report.html").write_text(document, encoding="utf-8")

print(json.dumps(result["primary"], ensure_ascii=False, indent=2))
