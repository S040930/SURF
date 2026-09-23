#!/usr/bin/env python3
"""Build a local, self-contained visual audit of GradeAgentOps data."""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import math
import statistics
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import cohen_kappa_score, mean_absolute_error, mean_squared_error


QUESTION_ORDER = [f"Q{i}" for i in range(1, 11)]
BLUE = "#2563eb"
ORANGE = "#ea580c"
INK = "#172033"
MUTED = "#5b6578"
GRID = "#dbe3ef"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--grader1", type=Path, required=True)
    parser.add_argument("--grader2", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def qwk(left: pd.Series, right: pd.Series) -> float:
    return float(
        cohen_kappa_score(
            left.astype(int), right.astype(int), labels=list(range(0, 11)), weights="quadratic"
        )
    )


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def short(value: str, length: int = 230) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= length else value[: length - 1] + "…"


def read_data(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    answers = pd.DataFrame(json.loads(args.answers.read_text(encoding="utf-8")))
    g1 = pd.read_csv(args.grader1, encoding="utf-8-sig")
    g2 = pd.read_csv(args.grader2, encoding="utf-8-sig")
    key = ["question_id", "student_id"]
    g1 = g1.rename(columns={"human_final_score": "score_g1"})
    g2 = g2.rename(columns={"human_final_score": "score_g2"})
    g1["score_g1"] = pd.to_numeric(g1["score_g1"])
    g2["score_g2"] = pd.to_numeric(g2["score_g2"])
    return answers, g1, g2


def build_merged(answers: pd.DataFrame, g1: pd.DataFrame, g2: pd.DataFrame) -> pd.DataFrame:
    rubric = answers[
        [
            "answer_id",
            "student_id",
            "question_id",
            "Question_text",
            "answer",
            "rubric",
            "Gold_type",
            "Gold_points",
            "Banned_misconceptions",
        ]
    ].copy()
    rubric["question_type"] = rubric["rubric"].map(
        {"technical": "技术题", "argumentative": "论述题"}
    )
    merged = rubric.merge(g1[["question_id", "student_id", "score_g1"]], on=["question_id", "student_id"], how="outer", validate="one_to_one")
    merged = merged.merge(g2[["question_id", "student_id", "score_g2"]], on=["question_id", "student_id"], how="outer", validate="one_to_one")
    merged["abs_diff"] = (merged["score_g1"] - merged["score_g2"]).abs()
    merged["signed_diff"] = merged["score_g1"] - merged["score_g2"]
    return merged


def question_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for q in QUESTION_ORDER:
        part = df[df["question_id"] == q].sort_values("student_id")
        if part.empty:
            continue
        rows.append(
            {
                "question_id": q,
                "type": part["question_type"].iloc[0],
                "mean_g1": part["score_g1"].mean(),
                "mean_g2": part["score_g2"].mean(),
                "mae": mean_absolute_error(part["score_g1"], part["score_g2"]),
                "rmse": math.sqrt(mean_squared_error(part["score_g1"], part["score_g2"])),
                "exact": (part["abs_diff"] == 0).mean(),
                "within1": (part["abs_diff"] <= 1).mean(),
                "pearson": pearsonr(part["score_g1"], part["score_g2"])[0],
                "spearman": spearmanr(part["score_g1"], part["score_g2"]).statistic,
                "qwk": qwk(part["score_g1"], part["score_g2"]),
                "n": len(part),
            }
        )
    return pd.DataFrame(rows)


def rubric_summary(answers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for q in QUESTION_ORDER:
        part = answers[answers["question_id"] == q]
        if part.empty:
            continue
        first = part.iloc[0]
        points = first["Gold_points"]
        rows.append(
            {
                "question_id": q,
                "type": "技术题" if first["rubric"] == "technical" else "论述题",
                "gold_points": len(points),
                "weight_sum": sum(float(item["weight"]) for item in points),
                "banned": len(first["Banned_misconceptions"]),
                "gold_type": first["Gold_type"],
                "question": first["Question_text"],
            }
        )
    return pd.DataFrame(rows)


def style_axes(ax: plt.Axes) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED)


def make_charts(df: pd.DataFrame, qs: pd.DataFrame, rs: pd.DataFrame, out: Path) -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["PingFang MO", "Arial", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.titleweight": "bold",
            "figure.dpi": 150,
        }
    )

    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5), constrained_layout=True)
    for ax, col, label, color in [
        (axes[0, 0], "score_g1", "评分者 E1", BLUE),
        (axes[0, 1], "score_g2", "评分者 E2", ORANGE),
    ]:
        ax.hist(df[col], bins=np.arange(-0.5, 11.5, 1), color=color, alpha=0.82, edgecolor="white")
        ax.set_title(f"{label}：总分分布")
        ax.set_xlabel("总分（0–10）")
        ax.set_ylabel("回答数")
        ax.set_xticks(range(0, 11, 2))
        style_axes(ax)
    for ax, col, label, color in [
        (axes[1, 0], "score_g1", "E1", BLUE),
        (axes[1, 1], "score_g2", "E2", ORANGE),
    ]:
        by_q = [df.loc[df["question_id"] == q, col].to_numpy() for q in QUESTION_ORDER]
        bp = ax.boxplot(by_q, patch_artist=True, widths=0.62, showfliers=False)
        for patch in bp["boxes"]:
            patch.set_facecolor(color)
            patch.set_alpha(0.52)
            patch.set_edgecolor(color)
        for median in bp["medians"]:
            median.set_color(INK)
            median.set_linewidth(1.5)
        ax.set_title(f"{label}：按题目分布")
        ax.set_xlabel("题目")
        ax.set_ylabel("总分（0–10）")
        ax.set_xticks(range(1, 11), QUESTION_ORDER)
        ax.set_ylim(-0.5, 10.5)
        style_axes(ax)
    fig.suptitle("GradeAgentOps：人工评分概览", fontsize=17, color=INK)
    fig.savefig(out / "01_score_distributions.png", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.4), constrained_layout=True)
    x = np.arange(len(qs))
    width = 0.36
    axes[0].bar(x - width / 2, qs["mean_g1"], width, label="E1", color=BLUE)
    axes[0].bar(x + width / 2, qs["mean_g2"], width, label="E2", color=ORANGE)
    axes[0].set_title("各题平均分")
    axes[0].set_ylabel("平均总分")
    axes[0].set_xticks(x, qs["question_id"])
    axes[0].set_ylim(0, 10)
    axes[0].legend(frameon=False)
    style_axes(axes[0])
    axes[1].bar(x, qs["mae"], color=[BLUE if t == "技术题" else ORANGE for t in qs["type"]])
    axes[1].set_title("各题人工评分 MAE")
    axes[1].set_ylabel("|E1 − E2| 平均值")
    axes[1].set_xticks(x, qs["question_id"])
    axes[1].set_ylim(0, max(2.0, qs["mae"].max() * 1.25))
    style_axes(axes[1])
    fig.suptitle("GradeAgentOps：题目级评分差异", fontsize=17, color=INK)
    fig.savefig(out / "02_question_agreement.png", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.4), constrained_layout=True)
    axes[0].scatter(df["score_g1"], df["score_g2"], s=14, alpha=0.28, color=BLUE, edgecolors="none")
    axes[0].plot([0, 10], [0, 10], linestyle="--", color=MUTED, linewidth=1)
    axes[0].set_title("E1 与 E2 的逐回答评分")
    axes[0].set_xlabel("E1 分数")
    axes[0].set_ylabel("E2 分数")
    axes[0].set_xlim(-0.3, 10.3)
    axes[0].set_ylim(-0.3, 10.3)
    style_axes(axes[0])
    axes[1].bar(qs["question_id"], qs["qwk"], color=[BLUE if t == "技术题" else ORANGE for t in qs["type"]])
    axes[1].axhline(0.678, linestyle="--", color=MUTED, linewidth=1, label="论文报告的总体 QWK 0.678")
    axes[1].set_title("各题 Quadratic Weighted Kappa")
    axes[1].set_ylabel("QWK")
    axes[1].set_ylim(0, 1.02)
    axes[1].legend(frameon=False, fontsize=8)
    style_axes(axes[1])
    fig.suptitle("GradeAgentOps：人工评分一致性", fontsize=17, color=INK)
    fig.savefig(out / "03_human_agreement.png", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.4), constrained_layout=True)
    axes[0].bar(rs["question_id"], rs["gold_points"], color=BLUE)
    axes[0].set_title("每题 Gold points 数量")
    axes[0].set_ylabel("原子评分点数量")
    axes[0].set_ylim(0, max(rs["gold_points"].max() + 1, 6))
    style_axes(axes[0])
    axes[1].bar(rs["question_id"], rs["banned"], color=ORANGE)
    axes[1].set_title("每题 Banned misconceptions 数量")
    axes[1].set_ylabel("显式错误模式数量")
    axes[1].set_ylim(0, max(rs["banned"].max() + 1, 4))
    style_axes(axes[1])
    fig.suptitle("GradeAgentOps：Rubric 结构", fontsize=17, color=INK)
    fig.savefig(out / "04_rubric_structure.png", bbox_inches="tight")
    plt.close(fig)


def image_tag(path: Path, alt: str) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img alt="{esc(alt)}" src="data:image/png;base64,{data}" />'


def make_report(args: argparse.Namespace, answers: pd.DataFrame, df: pd.DataFrame, qs: pd.DataFrame, rs: pd.DataFrame, out: Path) -> None:
    total = len(df)
    mae = mean_absolute_error(df["score_g1"], df["score_g2"])
    rmse = math.sqrt(mean_squared_error(df["score_g1"], df["score_g2"]))
    pearson = pearsonr(df["score_g1"], df["score_g2"])[0]
    spearman = spearmanr(df["score_g1"], df["score_g2"]).statistic
    exact = (df["abs_diff"] == 0).mean()
    within1 = (df["abs_diff"] <= 1).mean()
    overall_qwk = qwk(df["score_g1"], df["score_g2"])
    duplicate_keys = df.duplicated(["question_id", "student_id"]).sum()
    response_missing = int(df["answer"].isna().sum())
    score_missing = int(df[["score_g1", "score_g2"]].isna().any(axis=1).sum())
    top = df.sort_values(["abs_diff", "question_id", "student_id"], ascending=[False, True, True]).head(12)

    qrows = []
    for _, row in qs.iterrows():
        qrows.append(
            "<tr>"
            f"<td>{esc(row.question_id)}</td><td>{esc(row.type)}</td><td>{row.n:.0f}</td>"
            f"<td>{row.mean_g1:.2f}</td><td>{row.mean_g2:.2f}</td><td>{row.mae:.2f}</td>"
            f"<td>{pct(row.within1)}</td><td>{row.qwk:.3f}</td>"
            "</tr>"
        )
    rrows = []
    for _, row in rs.iterrows():
        rrows.append(
            "<tr>"
            f"<td>{esc(row.question_id)}</td><td>{esc(row.type)}</td><td>{esc(row.gold_type)}</td>"
            f"<td>{row.gold_points:.0f}</td><td>{row.weight_sum:.0f}</td><td>{row.banned:.0f}</td>"
            f"<td>{esc(short(row.question, 100))}</td></tr>"
        )
    erows = []
    for _, row in top.iterrows():
        erows.append(
            "<tr>"
            f"<td>{esc(row.question_id)}</td><td>{esc(row.student_id)}</td>"
            f"<td>{row.score_g1:.0f}</td><td>{row.score_g2:.0f}</td><td>{row.signed_diff:+.0f}</td>"
            f"<td>{esc(short(row.answer))}</td></tr>"
        )

    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>GradeAgentOps 可视化分析</title>
<style>
body{{margin:0;background:#f5f7fb;color:{INK};font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55}}
main{{max-width:1240px;margin:0 auto;padding:36px 28px 64px}} h1{{margin:0 0 8px;font-size:32px}} h2{{margin-top:38px;border-bottom:1px solid #dbe3ef;padding-bottom:8px}}
.lead{{color:{MUTED};max-width:920px}} .cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:26px 0}}
.card{{background:white;border:1px solid #e3e9f2;border-radius:12px;padding:14px;box-shadow:0 2px 8px #1720330b}} .label{{font-size:12px;color:{MUTED}}}.value{{font-size:23px;font-weight:700;margin-top:4px}}
.chart{{background:white;border:1px solid #e3e9f2;border-radius:14px;padding:12px;margin:18px 0;box-shadow:0 2px 8px #1720330b}} .chart img{{display:block;width:100%;height:auto}}
table{{border-collapse:collapse;width:100%;background:white;border:1px solid #e3e9f2;border-radius:12px;overflow:hidden;font-size:13px}} th,td{{border-bottom:1px solid #edf1f6;padding:9px 10px;text-align:left;vertical-align:top}} th{{background:#eef3fb;color:#33415a;white-space:nowrap}} tr:last-child td{{border-bottom:0}}
.note{{background:#eef6ff;border-left:4px solid {BLUE};padding:12px 16px;border-radius:8px;color:#29405e}} .warn{{background:#fff7ed;border-left:4px solid {ORANGE};padding:12px 16px;border-radius:8px;color:#713b13}}
code{{background:#eef1f6;border-radius:4px;padding:2px 5px}} small{{color:{MUTED}}}
@media(max-width:900px){{.cards{{grid-template-columns:repeat(3,1fr)}}main{{padding:24px 14px}}table{{font-size:12px}}}}
</style></head><body><main>
<h1>GradeAgentOps 可视化分析</h1>
<p class="lead">本报告基于公开仓库中的 JSON 回答文件和两份人工评分 CSV 生成。它先展示数据内容，再展示两位人工评分者的分数分布、一致性和 rubric 结构。回答文本仅截取少量匿名样本，不代表完整数据导出。</p>
<div class="cards">
<div class="card"><div class="label">回答数</div><div class="value">{total:,}</div></div>
<div class="card"><div class="label">学生数</div><div class="value">{df.student_id.nunique():,}</div></div>
<div class="card"><div class="label">题目数</div><div class="value">{df.question_id.nunique()}</div></div>
<div class="card"><div class="label">E1–E2 MAE</div><div class="value">{mae:.2f}</div></div>
<div class="card"><div class="label">±1 分覆盖</div><div class="value">{pct(within1)}</div></div>
<div class="card"><div class="label">总体 QWK</div><div class="value">{overall_qwk:.3f}</div></div>
</div>
<div class="note"><strong>快速读法：</strong>每道题都有 100 条回答，Q1–Q5 是技术题，Q6–Q10 是论述题。E1 的总体均分为 {df.score_g1.mean():.2f}，E2 为 {df.score_g2.mean():.2f}；E2 整体比 E1 高 {df.score_g2.mean()-df.score_g1.mean():.2f} 分。两位评分者完全相同的比例为 {pct(exact)}，相差不超过 1 分的比例为 {pct(within1)}。</div>
<h2>1. 分数和题目内容</h2>
<div class="chart">{image_tag(out/'01_score_distributions.png','分数分布')}</div>
<p>技术题和论述题均使用 0–10 的最终分数，但题目级分布不同。箱线图能看到哪些题目分数更集中，直方图则显示评分是否集中在低分、中间分或高分区间。</p>
<h2>2. 人工评分一致性</h2>
<div class="chart">{image_tag(out/'02_question_agreement.png','题目级平均分与MAE')}</div>
<div class="chart">{image_tag(out/'03_human_agreement.png','人工评分一致性')}</div>
<p>散点图偏离对角线的点就是两位专家对同一份回答给出不同分数的地方。QWK 比简单相关更关注分数偏差的严重程度；它不应被解释为“模型质量”，而是该数据集人工标签一致性的上限背景。</p>
<table><thead><tr><th>题目</th><th>类型</th><th>n</th><th>E1 均分</th><th>E2 均分</th><th>MAE</th><th>±1 分</th><th>QWK</th></tr></thead><tbody>{''.join(qrows)}</tbody></table>
<h2>3. Rubric 结构</h2>
<div class="chart">{image_tag(out/'04_rubric_structure.png','rubric结构')}</div>
<table><thead><tr><th>题目</th><th>类型</th><th>Gold 类型</th><th>Gold points</th><th>权重和</th><th>Banned misconceptions</th><th>题目</th></tr></thead><tbody>{''.join(rrows)}</tbody></table>
<div class="note"><strong>关键观察：</strong>每题 Gold points 的权重和都是 10，技术题有显式 banned misconceptions，而论述题没有。技术题的 rubric 更偏“检查关键知识点与错误模式”；论述题更依赖清晰度、连贯性、原创性和论证性等人工维度。</div>
<h2>4. 最大人工分歧样本</h2>
<div class="warn">以下样本按 |E1−E2| 排序，用来直观看评分标准在哪里可能含糊。它们不是“谁对谁错”的自动判定，需要结合对应题目 rubric 复核。</div>
<table><thead><tr><th>题目</th><th>学生</th><th>E1</th><th>E2</th><th>E1−E2</th><th>回答摘录</th></tr></thead><tbody>{''.join(erows)}</tbody></table>
<h2>5. 数据质量检查</h2>
<table><thead><tr><th>检查项</th><th>结果</th><th>解释</th></tr></thead><tbody>
<tr><td>JSON 回答数</td><td>{len(answers):,}</td><td>应为 1000 条</td></tr>
<tr><td>评分 CSV 对齐后</td><td>{len(df):,}</td><td>以 question_id + student_id 一对一合并</td></tr>
<tr><td>重复键</td><td>{duplicate_keys}</td><td>题目和学生组合是否重复</td></tr>
<tr><td>回答缺失</td><td>{response_missing}</td><td>JSON 中 answer 字段</td></tr>
<tr><td>任一人工分数缺失</td><td>{score_missing}</td><td>E1/E2 最终分数</td></tr>
<tr><td>题目类型</td><td>技术 5 题 / 论述 5 题</td><td>每题 100 条回答</td></tr>
</tbody></table>
<h2>6. 对 SURF 的使用建议</h2>
<p>这份数据适合先做独立的评分一致性与 rubric 验证。若接入现有记忆实验，建议按学生而不是按回答切分训练/测试集，避免同一学生的不同回答跨 split 泄漏；同时需要单独确认是否存在逐答案教师文字反馈，不能默认复用当前 SAF 的 <code>full/no_feedback</code> 消融。</p>
<p><small>生成脚本：analyze_gradeagentops.py；来源：anghelcata/GradeAgentOps/data。图表为本地重新计算结果，论文中的总体 QWK 线仅作为参考背景。</small></p>
</main></body></html>"""
    (out / "gradeagentops_visual_report.html").write_text(html_doc, encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    answers, g1, g2 = read_data(args)
    merged = build_merged(answers, g1, g2)
    qs = question_summary(merged)
    rs = rubric_summary(answers)
    make_charts(merged, qs, rs, args.out)
    make_report(args, answers, merged, qs, rs, args.out)
    print(json.dumps({
        "rows": len(merged),
        "students": int(merged["student_id"].nunique()),
        "questions": int(merged["question_id"].nunique()),
        "mae": round(float(mean_absolute_error(merged["score_g1"], merged["score_g2"])), 4),
        "within_1": round(float((merged["abs_diff"] <= 1).mean()), 4),
        "qwk": round(qwk(merged["score_g1"], merged["score_g2"]), 4),
        "report": str(args.out / "gradeagentops_visual_report.html"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
