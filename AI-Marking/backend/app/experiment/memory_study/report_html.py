"""Self-contained interim HTML rendering for one question shard slice."""

from __future__ import annotations

import html
import json
import re


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict):
        return html.escape(
            " ".join(f"{key}={_short(val)}" for key, val in value.items())
        )
    if isinstance(value, float):
        return f"{value:.4f}"
    return html.escape(str(value))


def _opt(value: object) -> str:
    """Short rendering that shows an em dash for None values."""
    return "—" if value is None else _short(value)


def _short(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_fmt(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_TRAIN_ORDER_VARIANTS = ("original", "shuffled")
_CHART_ORDER_COLORS = ("#2b7bb9", "#7a9e2e", "#c47f17")
_CHART_CANONICAL_CONDITIONS = (
    "no_memory",
    "retrieval_full",
    "mem0_full",
    "amem_full",
    "retrieval_no_feedback",
    "mem0_no_feedback",
    "amem_no_feedback",
)
_CHART_WIDTH, _CHART_HEIGHT = 900, 240


def _natural_key(value: str):
    """Sort ``4.13.1017`` before ``4.13.806`` (trailing numbers numerically)."""
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts) if parts else (value,)


def _axis_label(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{value:.0f}"
    return f"{value:.2f}"


def _chart_conditions(rows: list[dict]) -> list[str]:
    present = sorted({row["condition"] for row in rows})
    ordered = [c for c in _CHART_CANONICAL_CONDITIONS if c in present]
    return ordered + [c for c in present if c not in _CHART_CANONICAL_CONDITIONS]


def _train_chart_figure(
    condition: str, train: list[dict], order_variants: tuple[str, ...]
) -> str:
    """Cumulative training NAE by the frozen memory-formation step."""
    rows = [
        row for row in train
        if row["condition"] == condition and row.get("train_step") is not None
    ]
    cumulative_by_order: dict[str, list[tuple[int, float]]] = {}
    raw_values: list[float] = []
    for order in order_variants:
        ordered = sorted(
            (row for row in rows if row.get("order_variant") == order),
            key=lambda row: int(row["train_step"]),
        )
        running: list[float] = []
        points: list[tuple[int, float]] = []
        for row in ordered:
            nae = float(row["normalized_absolute_diff"])
            raw_values.append(nae)
            running.append(nae)
            points.append((int(row["train_step"]), sum(running) / len(running)))
        cumulative_by_order[order] = points
    if not raw_values:
        return ""
    max_step = max(step for points in cumulative_by_order.values() for step, _ in points)
    vmin, vmax = 0.0, max(raw_values)
    if vmax <= 0:
        vmax = 1.0

    margin_left, margin_right, margin_top, margin_bottom = 50, 16, 30, 34
    x0, x1 = margin_left, _CHART_WIDTH - margin_right
    y0, y1 = margin_top, _CHART_HEIGHT - margin_bottom

    def x_at(index: int) -> float:
        return x0 + (index - 1) * (x1 - x0) / max(max_step - 1, 1)

    def y_at(value: float) -> float:
        return y1 - (value - vmin) / (vmax - vmin) * (y1 - y0)

    grid: list[str] = []
    label_x = x0 - 8
    for step in range(5):
        value = vmin + (vmax - vmin) * step / 4
        grid.append(
            f'<line x1="{x0}" x2="{x1}" y1="{y_at(value):.1f}" '
            f'y2="{y_at(value):.1f}" stroke="#e8e8e8" stroke-width="1"/>'
            f'<text x="{label_x}" y="{y_at(value) + 3:.1f}" text-anchor="end" '
            f'font-size="10" fill="#888">{_axis_label(value)}</text>'
        )

    def polyline(points: list[tuple[float, float]], *, stroke: str, width: float, dash: str) -> str:
        coords = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        return (
            f'<polyline points="{coords}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}"{dash_attr}/>'
        )

    out: list[str] = []
    for order, color in zip(order_variants, _CHART_ORDER_COLORS):
        points = [
            (x_at(step), y_at(value))
            for step, value in cumulative_by_order[order]
        ]
        if points:
            out.append(polyline(points, stroke=color, width=1.8, dash=""))

    caption = (
        f'<text x="{x0}" y="{y0 - 8}" font-size="13" font-weight="600">'
        f"{html.escape(condition)}</text>"
        f'<text x="{x1}" y="{y0 - 8}" text-anchor="end" font-size="11" fill="#666">'
        f"逐步累计 NAE · 最长 {max_step} 步</text>"
    )

    return (
        f'<figure class="chart">'
        f'<svg viewBox="0 0 {_CHART_WIDTH} {_CHART_HEIGHT}" '
        f'role="img" aria-label="训练集 {html.escape(condition)} 记忆形成 NAE 轨迹">'
        f"{''.join(grid)}{''.join(out)}{caption}</svg>"
        f"<figcaption>纵轴为截至当前训练步骤的累计平均 NAE；"
        f'{"/".join(order_variants)} 对应冻结顺序颜色。横轴严格使用冻结历史顺序。</figcaption>'
        f"</figure>"
    )


def _train_gap_charts_html(
    detail_rows: list[dict], order_variants: tuple[str, ...]
) -> str:
    """Per-condition line charts of the training-set record gaps. Empty (e.g.
    no train rows) renders nothing so the interim report stays valid on slices
    that only carry test data."""
    train = [
        row
        for row in detail_rows
        if row.get("split") == "train"
        and row.get("teacher_score") is not None
        and row.get("model_score") is not None
    ]
    if not train:
        return ""
    figures = "".join(
        _train_chart_figure(condition, train, order_variants)
        for condition in _chart_conditions(train)
    )
    return f'<div class="chart-grid">{figures}</div>'


def render_question_report_html(
    report: dict,
    detail_rows: list[dict],
    *,
    study_name: str,
    question_id: str,
    generated_at: str,
) -> str:
    """Render one question slice as a standalone interim HTML document."""
    primary = report.get("primary", {})
    condition_nae = primary.get("condition_nae") or {}
    memory_gain = primary.get("memory_gain_vs_no_memory") or {}
    split_metrics = report.get("split_metrics") or {}
    secondary = report.get("secondary") or {}
    resources = secondary.get("resource") or {}
    failures = report.get("failures") or []
    statements = report.get("statements") or []
    grid_context = report.get("grid_context") or {}
    diagnostics = report.get("diagnostics") or {}
    order_sensitivity = secondary.get("history_order_sensitivity") or {}
    no_memory_variation = secondary.get("no_memory_run_variation") or {}
    configured_orders = (report.get("analysis_method") or {}).get("order_variants")
    if isinstance(configured_orders, list) and configured_orders:
        order_variants = tuple(str(order) for order in configured_orders)
    else:
        order_variants = tuple(
            sorted(
                {
                    str(row.get("order_variant"))
                    for row in detail_rows
                    if row.get("order_variant")
                }
            )
        ) or _TRAIN_ORDER_VARIANTS

    summary_rows = [
        [model, condition, values.get("estimate"), values.get("n_answers"), values.get("excluded_answers")]
        for model, conditions in condition_nae.items()
        for condition, values in conditions.items()
    ]
    summary_table = (
        _table(
            ["模型", "条件", "测试集 NAE", "有效答案", "排除答案"],
            summary_rows,
        )
        if summary_rows
        else "<p>暂无成功评分。</p>"
    )

    gain_rows = [
        [
            model, condition, values.get("estimate"), values.get("n_answers"),
            values.get("improved"), values.get("tied"), values.get("worse"),
        ]
        for model, conditions in memory_gain.items()
        for condition, values in conditions.items()
    ]
    gain_table = _table(
        ["模型", "记忆条件", "记忆增益", "有效答案", "改善", "持平", "变差"],
        gain_rows,
    ) if gain_rows else ""

    order_rows = []
    for model, conditions in order_sensitivity.items():
        for condition, values in conditions.items():
            for question, detail in (values.get("by_question") or {}).items():
                order_rows.append([
                    model, condition, question,
                    *[
                        (detail.get("order_nae") or {}).get(order)
                        for order in order_variants
                    ],
                    detail.get("range"), detail.get("n_answers"),
                ])
    for model, values in no_memory_variation.items():
        for question, detail in (values.get("by_question") or {}).items():
            order_rows.append([
                model, "no_memory（重复运行波动）", question,
                *[
                    (detail.get("order_nae") or {}).get(order)
                    for order in order_variants
                ],
                detail.get("range"), detail.get("n_answers"),
            ])
    order_table = _table(
        ["模型", "条件", "题目", *order_variants, "顺序差", "有效答案"],
        order_rows,
    ) if order_rows else ""

    split_rows = [
        [
            split,
            model,
            condition,
            stats.get("n"),
            stats.get("signed_bias"),
            stats.get("mae"),
            stats.get("normalized_mae"),
            stats.get("median_absolute_error"),
            stats.get("within_one_score"),
        ]
        for split, models in split_metrics.items()
        for model, conditions in models.items()
        for condition, stats in conditions.items()
    ]
    split_table = (
        _table(
            [
                "数据片",
                "模型",
                "条件",
                "n",
                "signed_bias",
                "mae",
                "normalized_mae",
                "中位绝对误差",
                "within_one_score",
            ],
            split_rows,
        )
        if split_rows
        else ""
    )

    model_comparison = report.get("model_comparison") or {}
    comparison_rows = [
        [model, key, value]
        for model, entries in model_comparison.items()
        for key, value in entries.items()
        if not isinstance(value, dict)
    ]
    comparison_table = (
        _table(["模型", "对比项", "数值"], comparison_rows) if comparison_rows else ""
    )

    detail_cells: list[list[object]] = []
    for model, entries in model_comparison.items():
        for key, value in entries.items():
            if not isinstance(value, dict) or not key.endswith("_detail"):
                continue
            for cell_key, cell_value in (value.get("cells") or {}).items():
                detail_cells.append([model, key, cell_key, cell_value])
            detail_cells.append(
                [
                    model,
                    f"{key} · 汇总",
                    "",
                    (
                        f"n_paired={_opt(value.get('n_paired'))} "
                        f"n_cells={_opt(value.get('n_cells'))} "
                        f"mean={_opt(value.get('mean'))} min={_opt(value.get('min'))} "
                        f"max={_opt(value.get('max'))} median={_opt(value.get('median'))}"
                    ),
                ]
            )
    comparison_detail_table = (
        _table(
            ["模型", "对比", "题目 / 顺序", "配对差值单元均值"],
            detail_cells,
        )
        if detail_cells
        else ""
    )

    grid_rows = [
        [
            question,
            values.get("denominator"),
            values.get("score_floor"),
            values.get("observed_min"),
            values.get("observed_max"),
            values.get("training_observed_max"),
            values.get("n_answers"),
        ]
        for question, values in grid_context.items()
    ]
    grid_table = (
        _table(
            [
                "题目",
                "denominator(评分上限)",
                "floor",
                "observed_min",
                "observed_max",
                "training_observed_max",
                "n_answers",
            ],
            grid_rows,
        )
        if grid_rows
        else ""
    )

    secondary_rows = [
        ["测试集有符号偏差", diagnostics.get("signed_bias")],
        ["去反馈 − 完整反馈 NAE", secondary.get("feedback_ablation")],
        ["输入 token", resources.get("input_tokens")],
        ["输出 token", resources.get("output_tokens")],
        ["总 token", resources.get("total_tokens")],
    ]
    secondary_table = _table(["指标", "数值"], secondary_rows)

    failure_rows = [
        [
            item.get("call_id"),
            item.get("model"),
            item.get("condition"),
            item.get("answer_id"),
            item.get("failure_code"),
            item.get("failure_summary"),
        ]
        for item in failures
    ]
    failure_table = (
        _table(
            ["call_id", "模型", "条件", "answer_id", "失败码", "摘要"],
            failure_rows,
        )
        if failure_rows
        else "<p>该题无失败调用。</p>"
    )

    detail_rows = sorted(detail_rows, key=lambda row: row.get("call_id") or 0)
    detail_table = _table(
        [
            "call_id",
            "模型",
            "条件",
            "反馈",
            "顺序",
            "repeat",
            "answer_id",
            "数据片",
            "训练步骤",
            "评分前记忆数",
            "教师分",
            "模型分",
            "signed",
            "absolute",
            "normalized",
            "tokens",
            "延迟ms",
        ],
        [
            [
                row.get("call_id"),
                row.get("model"),
                row.get("condition"),
                row.get("feedback_mode"),
                row.get("order_variant"),
                row.get("repeat"),
                row.get("answer_id"),
                row.get("split"),
                row.get("train_step"),
                row.get("memory_items_before"),
                row.get("teacher_score"),
                row.get("model_score"),
                row.get("signed_diff"),
                row.get("absolute_diff"),
                row.get("normalized_absolute_diff"),
                row.get("total_tokens"),
                row.get("latency_ms"),
            ]
            for row in detail_rows
        ],
    )

    statement_items = "".join(
        f"<li>{html.escape(str(statement))}</li>" for statement in statements
    )
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>{html.escape(study_name)} · {html.escape(question_id)} 中期草稿</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 2rem auto; max-width: 1200px; color: #1a1a1a; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 2rem; }}
.banner {{ background: #fff3cd; border: 1px solid #e0c068; border-radius: 6px;
           padding: 0.75rem 1rem; font-weight: 600; }}
.meta {{ color: #555; font-size: 0.85rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.82rem; margin-top: 0.5rem; }}
th, td {{ border: 1px solid #d0d0d0; padding: 4px 8px; text-align: left; }}
th {{ background: #f5f5f5; }}
code {{ font-size: 0.78rem; word-break: break-all; }}
.chart-grid {{ display: flex; flex-wrap: wrap; gap: 1.2rem; }}
.chart {{ margin: 0; flex: 1 1 44rem; min-width: 26rem; }}
.chart svg {{ width: 100%; height: auto; display: block;
              border: 1px solid #e3e3e3; border-radius: 6px; background: #fff; }}
.chart figcaption {{ font-size: 0.74rem; color: #666; margin-top: 0.3rem; }}
</style>
</head>
<body>
<div class="banner">中期草稿（非正式结论）——正式结果以完整性审计通过后的全量报告为准</div>
<h1>{html.escape(study_name)} · 题目 {html.escape(question_id)}</h1>
<p class="meta">生成时间 {html.escape(generated_at)} ·
report_sha256 <code>{html.escape(str(report.get("report_sha256", "")))}</code></p>
<h2>声明</h2>
<ul>{statement_items}</ul>
<h2>NEW 测试集：条件 NAE</h2>
{summary_table}
<h2>NEW 测试集：记忆增益（正值表示改善）</h2>
{gain_table}
<h2>最终记忆的历史顺序敏感性</h2>
<p class="meta">记忆条件比较三条独立训练轨迹；no_memory 仅作为重复运行波动基线。</p>
{order_table}
<h2>评分网格上下文（grid_context）</h2>
{grid_table}
<h2>训练 / 测试分离诊断统计</h2>
{split_table}
<h2>训练阶段记忆形成 NAE 轨迹</h2>
<p class="meta">第 t 步评分发生在当前答案写入前，反映前 t−1 个答案形成的记忆；仅作过程诊断，不进入测试主指标。</p>
{_train_gap_charts_html(detail_rows, order_variants)}
<h2>补充框架对比</h2>
{comparison_table}
<h2>配对差值明细（每题 × 历史顺序）</h2>
{comparison_detail_table}
<h2>次要指标与资源</h2>
{secondary_table}
<h2>失败调用</h2>
{failure_table}
<h2>逐条评分明细（{len(detail_rows)} 条）</h2>
{detail_table}
</body>
</html>
"""


def render_question_report_json(report: dict, detail_rows: list[dict]) -> str:
    """Render one question slice as machine-readable JSON."""
    return json.dumps(
        {"report": report, "detail_rows": detail_rows},
        ensure_ascii=False,
        indent=2,
    )
