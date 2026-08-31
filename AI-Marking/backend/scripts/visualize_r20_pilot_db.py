"""Generate descriptive visualizations for the completed r20 pilot project.

This reads only aggregate-safe fields from the local experiment database and
does not export answer text, feedback, or model prompt/output payloads.  The
pilot's locked report remains technical-only; the NAE plots produced here are
an explicit post-run exploratory view requested for internal analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sqlalchemy import create_engine, text


CONDITIONS = ("nm", "crm", "arm")
CONDITION_LABELS = {"nm": "NM", "crm": "CRM", "arm": "ARM"}
CONDITION_COLORS = {"nm": "#8C97A8", "crm": "#2F80ED", "arm": "#F2994A"}
MODELS = ("deepseek-v4-flash-ga-260731", "doubao-seed-2.0-lite")
MODEL_LABELS = {
    "deepseek-v4-flash-ga-260731": "DeepSeek",
    "doubao-seed-2.0-lite": "Doubao",
}
QUESTIONS = ("4.13", "5.7")
HISTORIES = (10, 20)


def database_url() -> str:
    """Read the isolated experiment URL without importing application settings."""
    value = os.environ.get("DATABASE_URL")
    if value:
        return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    return "postgresql+psycopg2://postgres:postgres@localhost:5432/ai_marking_experiment"


def model_label(model: str) -> str:
    return MODEL_LABELS.get(model, model)


def pct(value: float) -> str:
    return f"{value:.1%}"


def percentile(values: list[float], p: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), p))


def find_project(connection, project_id: str | None) -> dict:
    if project_id:
        row = connection.execute(
            text("select id, name, kind, status, completed_at from r20_projects where id=:id"),
            {"id": project_id},
        ).mappings().one()
    else:
        row = connection.execute(
            text("""
                select id, name, kind, status, completed_at
                from r20_projects
                where kind='pilot_run' and status like 'completed%'
                order by completed_at desc nulls last
                limit 1
            """
            )
        ).mappings().one()
    return dict(row)


def load_aggregates(engine, project_id: str | None) -> tuple[dict, list[dict], dict]:
    with engine.connect() as connection:
        project = find_project(connection, project_id)
        raw = connection.execute(
            text("""
                select model_id, question_id, condition, history_count,
                       trajectory, answer_id, group_id, repeat,
                       model_score, teacher_score, max_score
                from r20_calls
                where project_id=:project_id
                  and kind='test_score'
                  and status='succeeded'
                  and question_id in ('4.13', '5.7')
                order by question_id, model_id, condition, history_count,
                         trajectory, answer_id, repeat
            """),
            {"project_id": project["id"]},
        ).mappings().all()

        # Average repeated scores inside each paired endpoint cell first.
        cells: dict[tuple, list[dict]] = defaultdict(list)
        for row in raw:
            key = (
                row["model_id"], row["question_id"], row["condition"],
                row["history_count"], row["trajectory"], row["answer_id"], row["group_id"],
            )
            cells[key].append(dict(row))

        endpoint_rows = []
        for key, rows in cells.items():
            model, question, condition, history, trajectory, answer_id, group_id = key
            score = float(np.mean([r["model_score"] for r in rows]))
            teacher_score = float(rows[0]["teacher_score"])
            max_score = float(rows[0]["max_score"])
            endpoint_rows.append({
                "model": model,
                "question": question,
                "condition": condition,
                "history": int(history),
                "trajectory": int(trajectory),
                "answer_id": answer_id,
                "group_id": group_id,
                "repeats": len(rows),
                "nae": abs(score - teacher_score) / max_score,
            })

        summary = []
        for model in MODELS:
            for question in QUESTIONS:
                for history in HISTORIES:
                    for condition in CONDITIONS:
                        values = [
                            r["nae"] for r in endpoint_rows
                            if r["model"] == model and r["question"] == question
                            and r["history"] == history and r["condition"] == condition
                        ]
                        summary.append({
                            "model": model,
                            "model_label": model_label(model),
                            "question": question,
                            "history": history,
                            "condition": condition,
                            "condition_label": CONDITION_LABELS[condition],
                            "mean_nae": float(np.mean(values)),
                            "n": len(values),
                        })

        report_row = connection.execute(
            text("select report_json from r20_reports where project_id=:project_id order by calculated_at desc limit 1"),
            {"project_id": project["id"]},
        ).mappings().one()
        report = report_row["report_json"]
        resources = report["supplement"]["resources"]
        checks = report["supplement"]["manipulation_checks"]
        latencies = [float(v) for v in resources["latencies_ms"]]
        operational = {
            "project_id": project["id"],
            "project_name": project["name"],
            "status": project["status"],
            "completed_at": project["completed_at"].isoformat() if project["completed_at"] else None,
            "test_score_rows": len(raw),
            "endpoint_cells": len(endpoint_rows),
            "attempts": resources["attempts"],
            "input_tokens": resources["input_tokens"],
            "output_tokens": resources["output_tokens"],
            "estimated_cost_fen": resources["estimated_cost_fen"],
            "failed_calls": resources["failed_calls"],
            "latency_p50_ms": percentile(latencies, 50),
            "latency_p95_ms": percentile(latencies, 95),
            "snapshot_count": checks["snapshot_count"],
            "max_items": checks["max_items"],
            "max_visible_tokens": checks["max_visible_tokens"],
            "constraint_violation_count": checks["constraint_violation_count"],
        }
    return project, summary, operational


def save_figure(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(path.with_suffix(".svg"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_operational_chart(operational: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.5))
    axes[0].bar(["Attempts", "Failed"], [operational["attempts"], operational["failed_calls"]], color=["#173F5F", "#27AE60"])
    axes[0].set_title("Execution stability", weight="bold")
    axes[0].set_ylim(0, operational["attempts"] * 1.12)
    axes[0].text(0, operational["attempts"] * 0.96, str(operational["attempts"]), ha="center", va="top", color="white", weight="bold")
    axes[0].text(1, operational["attempts"] * 0.03, str(operational["failed_calls"]), ha="center", va="bottom", weight="bold")

    axes[1].bar(["Input", "Output"], [operational["input_tokens"] / 1e6, operational["output_tokens"] / 1e6], color=["#2F80ED", "#F2994A"])
    axes[1].set_title("Token volume (million)", weight="bold")
    axes[1].set_ylabel("Million tokens")
    axes[1].grid(axis="y", alpha=0.25)

    axes[2].bar(["p50", "p95"], [operational["latency_p50_ms"] / 1000, operational["latency_p95_ms"] / 1000], color="#8C97A8")
    axes[2].set_title("Request latency", weight="bold")
    axes[2].set_ylabel("Seconds")
    axes[2].grid(axis="y", alpha=0.25)

    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_axisbelow(True)
    fig.suptitle("r20 pilot operational results · project 1", fontsize=15, weight="bold")
    fig.text(0.5, 0.01, f"Snapshots={operational['snapshot_count']} · constraint violations={operational['constraint_violation_count']} · estimated cost={operational['estimated_cost_fen'] / 100:.2f} CNY", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    save_figure(fig, out / "01_operational_dashboard.png")


def select(summary: list[dict], model: str, question: str, history: int, condition: str) -> dict:
    return next(r for r in summary if r["model"] == model and r["question"] == question and r["history"] == history and r["condition"] == condition)


def make_final_chart(summary: list[dict], out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.1), sharey=True)
    x = np.arange(len(CONDITIONS))
    width = 0.34
    for ax, question in zip(axes, QUESTIONS):
        for i, model in enumerate(MODELS):
            values = [select(summary, model, question, 20, condition)["mean_nae"] for condition in CONDITIONS]
            bars = ax.bar(x + (i - 0.5) * width, values, width, label=model_label(model), color=["#173F5F", "#C44536"][i])
            for bar, value in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2, value + 0.008, pct(value), ha="center", va="bottom", fontsize=8)
        ax.set_title(f"Question {question}", weight="bold")
        ax.set_xticks(x, [CONDITION_LABELS[c] for c in CONDITIONS])
        ax.set_ylim(0, 0.66)
        ax.grid(axis="y", alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Mean NAE at h=20\n(lower is better)")
    axes[-1].legend(frameon=False, loc="upper right")
    fig.suptitle("Final pilot comparison at h=20", fontsize=15, weight="bold")
    fig.text(0.5, 0.01, "n=15 endpoint×trajectory cells per bar; two h=20 repeats were averaged before NAE calculation. Exploratory only.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.05, 1, 0.93))
    save_figure(fig, out / "02_nae_h20_by_question.png")


def make_history_chart(summary: list[dict], out: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10, 7.5), sharey=False)
    for ax, (model, question) in zip(axes.flat, [(m, q) for m in MODELS for q in QUESTIONS]):
        for condition in CONDITIONS:
            values = [select(summary, model, question, history, condition)["mean_nae"] for history in HISTORIES]
            ax.plot(HISTORIES, values, marker="o", linewidth=2.2, label=CONDITION_LABELS[condition], color=CONDITION_COLORS[condition])
        ax.set_title(f"{model_label(model)} · Q{question}", weight="bold")
        ax.set_xticks(HISTORIES)
        ax.set_xlabel("History size h")
        ax.set_ylabel("Mean NAE")
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0, 1].legend(frameon=False, loc="upper right")
    fig.suptitle("Exploratory learning curve: h=10 → h=20", fontsize=15, weight="bold")
    fig.text(0.5, 0.01, "The pilot protocol defines h=20 as the endpoint; h=10 is a diagnostic probe.", ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    save_figure(fig, out / "03_learning_curve.png")


def make_effect_heatmap(summary: list[dict], out: Path) -> None:
    rows = [(model, question) for model in MODELS for question in QUESTIONS]
    matrix = []
    for model, question in rows:
        arm = select(summary, model, question, 20, "arm")["mean_nae"]
        crm = select(summary, model, question, 20, "crm")["mean_nae"]
        nm = select(summary, model, question, 20, "nm")["mean_nae"]
        matrix.append([arm - crm, ((arm + crm) / 2) - nm])
    matrix = np.asarray(matrix)
    limit = max(0.08, float(np.abs(matrix).max()) * 1.15)
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    image = ax.imshow(matrix, cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto")
    ax.set_xticks([0, 1], ["ARM − CRM", "Memory mean − NM"])
    ax.set_yticks(np.arange(len(rows)), [f"{model_label(m)} · Q{q}" for m, q in rows])
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, f"{matrix[i, j]:+.3f}", ha="center", va="center", weight="bold")
    ax.set_title("h=20 descriptive effect contrasts", fontsize=14, weight="bold")
    ax.set_xlabel("Negative values favor ARM or memory conditions")
    fig.colorbar(image, ax=ax, shrink=0.85, label="Difference in NAE")
    fig.tight_layout()
    save_figure(fig, out / "04_effect_heatmap.png")


def write_outputs(summary: list[dict], operational: dict, out: Path) -> None:
    with (out / "pilot_nae_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    with (out / "pilot_aggregate.json").open("w", encoding="utf-8") as handle:
        json.dump({"operational": operational, "summary": summary}, handle, ensure_ascii=False, indent=2)

    def s(model: str, question: str, history: int, condition: str) -> float:
        return select(summary, model, question, history, condition)["mean_nae"]

    lines = [
        "# 项目1技术试点可视化结果",
        "",
        f"- 题目：`4.13`、`5.7`；项目状态：`{operational['status']}`；完成时间：`{operational['completed_at']}`。",
        f"- 试点执行：{operational['attempts']} 次 attempts，失败调用 {operational['failed_calls']}；输入 {operational['input_tokens']:,} tokens，输出 {operational['output_tokens']:,} tokens。",
        f"- 记忆约束检查：{operational['snapshot_count']} 个 snapshot，最大可见 token 数 {operational['max_visible_tokens']}，违规 {operational['constraint_violation_count']} 次。",
        f"- 延迟：p50 {operational['latency_p50_ms'] / 1000:.2f}s，p95 {operational['latency_p95_ms'] / 1000:.2f}s；估算成本 {operational['estimated_cost_fen'] / 100:.2f} 元。",
        "",
        "## 图表",
        "",
        "![运行指标](01_operational_dashboard.png)",
        "![h=20 NAE](02_nae_h20_by_question.png)",
        "![学习曲线](03_learning_curve.png)",
        "![效果差值](04_effect_heatmap.png)",
        "",
        "## h=20 描述性读数",
        "",
        "NAE 越低越好；每个柱为 15 个 endpoint×trajectory 单元，重复评分先平均再计算误差。",
        "",
        "| 模型 | 题目 | NM | CRM | ARM | ARM−CRM | memory mean−NM |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        for question in QUESTIONS:
            nm, crm, arm = (s(model, question, 20, c) for c in CONDITIONS)
            lines.append(f"| {model_label(model)} | {question} | {nm:.4f} | {crm:.4f} | {arm:.4f} | {arm - crm:+.4f} | {((arm + crm) / 2) - nm:+.4f} |")
    lines += [
        "",
        "## 解释边界",
        "",
        "这些 NAE 图是根据用户要求对已完成技术试点做的事后探索性解封，不改变系统中 `pilot_technical_only` 的锁定报告状态，也不构成正式 RQ1/RQ2 结论。样本只有两道试点题，不能据此推广到正式题、其他模型或总体准确率。",
        "",
        "原始汇总只保留聚合字段；未导出学生答案、教师反馈、提示词或模型输出 payload。",
    ]
    (out / "pilot_results_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    output_dir = args.output_dir or repo_root / "outputs" / "r20_pilot_visualizations"
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url())
    _, summary, operational = load_aggregates(engine, args.project_id)
    make_operational_chart(operational, output_dir)
    make_final_chart(summary, output_dir)
    make_history_chart(summary, output_dir)
    make_effect_heatmap(summary, output_dir)
    write_outputs(summary, operational, output_dir)
    print(f"Wrote pilot visualizations to {output_dir}")


if __name__ == "__main__":
    main()
