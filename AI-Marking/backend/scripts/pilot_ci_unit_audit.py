"""CI 口径审计：bootstrap 该在哪个单位上做？（只读）

用法：
    cd AI-Marking/backend && .venv/bin/python scripts/pilot_ci_unit_audit.py

产出：
    outputs/saf_memory_study/_verify/pilot_ci_unit_audit.json

背景：上一版复核把 report 的"每个 condition 各自的 CI"换成了"配对 Δ 的
bootstrap CI"，但仍在**答案级（n=10）**重采样。本脚本审计这一选择是否成立。

对每一对 (model, condition) 同时给出四种口径：

  A. 答案级 bootstrap（n=10）——即上一版报告用的
  B. 题目级 bootstrap（n=2）——单位正确，但重采样分布只有 3 个取值点
  C. 题目级 t 区间（df=1）——直接问"换一道新题的效应"，这是论文真正要的量
  D. 精确符号翻转检验的可达最小 two-sided p（答案级 vs 题目级）

只读 ms_* 表产物 JSON，不写数据库、不调用模型。
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations_with_replacement
from math import factorial
from pathlib import Path
from statistics import mean, stdev

try:
    from scipy import stats as _scipy_stats
except Exception:  # pragma: no cover
    _scipy_stats = None

REPO = Path(__file__).resolve().parents[3]
OUT_DIR = REPO / "outputs" / "saf_memory_study" / "_verify"
SRC = OUT_DIR / "pilot_paired_analysis.json"
OUT = OUT_DIR / "pilot_ci_unit_audit.json"

CONDITION_ORDER = [
    "retrieval_full",
    "retrieval_no_feedback",
    "mem0_full",
    "mem0_no_feedback",
    "amem_full",
    "amem_no_feedback",
]
CONDITION_LABEL = {
    "retrieval_full": "检索·含反馈",
    "retrieval_no_feedback": "检索·无反馈",
    "mem0_full": "Mem0·含反馈",
    "mem0_no_feedback": "Mem0·无反馈",
    "amem_full": "A-MEM·含反馈",
    "amem_no_feedback": "A-MEM·无反馈",
}

# t_{0.975, df=1}，用于题目级两单位区间
T975_DF1 = 12.706204736432095 if _scipy_stats is None else float(_scipy_stats.t.ppf(0.975, 1))


def bootstrap_exact(values: list[float]) -> dict:
    """穷举全部重采样多重集并按多项系数加权（无种子噪声）。"""
    k = len(values)
    w_by_mean: dict[float, float] = defaultdict(float)
    for combo in combinations_with_replacement(range(k), k):
        counts = [0] * k
        for i in combo:
            counts[i] += 1
        w = factorial(k)
        for c in counts:
            if c > 1:
                w //= factorial(c)
        w_by_mean[round(sum(counts[i] * values[i] for i in range(k)) / k, 12)] += w
    keys = sorted(w_by_mean)
    total = k**k
    assert abs(sum(w_by_mean[x] for x in keys) - total) < 1e-6 * total

    def q(p: float) -> float:
        acc = 0.0
        for x in keys:
            acc += w_by_mean[x]
            if acc / total >= p:
                return x
        return keys[-1]

    return {
        "n_units": k,
        "distinct_resample_means": len(keys),
        "mean": mean(values),
        "lo": q(0.025),
        "hi": q(0.975),
        "half_width": (q(0.975) - q(0.025)) / 2,
    }


def t_interval(values: list[float]) -> dict:
    """题目级 t 区间（小样本、单位=题）。n=1 时不可估。"""
    k = len(values)
    if k < 2:
        return {"n_units": k, "mean": values[0] if k else None,
                "lo": None, "hi": None, "half_width": None}
    m = mean(values)
    se = stdev(values) / (k**0.5)          # ddof=1
    half = T975_DF1 * se
    return {"n_units": k, "mean": m, "sd": stdev(values), "se": se,
            "lo": m - half, "hi": m + half, "half_width": half}


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))

    # model -> question -> answer -> condition -> abs_nd
    grid: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for key, rows in raw["detail"].items():
        model, question = key.split("|")
        for r in rows:
            grid[model][question][r["answer_id"]][r["condition"]] = float(r["abs_nd"])

    models = sorted(grid)
    report: dict = {
        "source": str(SRC.relative_to(REPO)),
        "t975_df1": T975_DF1,
        "note": (
            "A = 答案级 bootstrap（n=10，捕获题内噪声）；"
            "B = 题目级 bootstrap（n=2，分布退化）；"
            "C = 题目级 t 区间（df=1，换新题的效应）；"
            "D = 精确符号翻转检验可达最小 p"
        ),
        "results": {},
        "power_floors": {},
    }

    for model in models:
        qs = sorted(grid[model])
        report["results"][model] = {}
        for cond in CONDITION_ORDER:
            d_answer: list[float] = []
            d_by_q: dict[str, list[float]] = {q: [] for q in qs}
            for q in qs:
                for ans, by_cond in grid[model][q].items():
                    if "no_memory" not in by_cond or cond not in by_cond:
                        continue
                    d = by_cond[cond] - by_cond["no_memory"]
                    d_answer.append(d)
                    d_by_q[q].append(d)
            d_q = [mean(d_by_q[q]) for q in qs if d_by_q[q]]

            a = bootstrap_exact(d_answer)
            b = bootstrap_exact(d_q) if len(d_q) >= 2 else None
            c = t_interval(d_q)

            report["results"][model][cond] = {
                "n_answers": len(d_answer),
                "n_questions": len(d_q),
                "question_deltas": {q: mean(v) for q, v in d_by_q.items() if v},
                "pooled_delta": mean(d_answer),
                "A_answer_bootstrap": a,
                "B_question_bootstrap": b,
                "C_question_t": c,
                "halfwidth_ratio_C_over_A": (
                    c["half_width"] / a["half_width"]
                    if c["half_width"] and a["half_width"] else None
                ),
            }

    for model in models:
        nq = len(grid[model])
        na = sum(len(v) for v in grid[model].values())
        report["power_floors"][model] = {
            "n_questions": nq,
            "n_answers": na,
            "min_two_sided_p_answer_unit": 2 / (2 ** na),
            "min_two_sided_p_question_unit": 2 / (2 ** nq),
            "significant_possible_question_unit": (2 / (2 ** nq)) < 0.05,
        }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---------- 控制台 ----------
    W = 96
    print("=" * W)
    print("§1  同一份数据、同一个点估计，换个重采样单位，区间宽度差多少？")
    print("=" * W)
    print(f"{'模型':<14}{'条件':<16}{'Δ':>8}{'A 答案级±':>11}{'B 题目级±':>11}"
          f"{'C 题目级t ±':>13}{'C/A':>8}")
    for model in models:
        for cond in CONDITION_ORDER:
            r = report["results"][model][cond]
            a, b, c = r["A_answer_bootstrap"], r["B_question_bootstrap"], r["C_question_t"]
            bhw = b["half_width"] if b else float("nan")
            ratio = r["halfwidth_ratio_C_over_A"]
            ratio_s = f"{ratio:7.1f}x" if ratio else "   n/a "
            print(f"{model.replace('gpt-6-',''):<14}{CONDITION_LABEL[cond]:<16}"
                  f"{r['pooled_delta']*100:+7.1f}pp{a['half_width']*100:10.1f}pp"
                  f"{bhw*100:10.1f}pp{c['half_width']*100:12.1f}pp"
                  f"{ratio_s}")

    print("\n" + "=" * W)
    print("§2  题目级 bootstrap（n=2）到底退化成什么")
    print("=" * W)
    for model in models:
        r = report["results"][model]["retrieval_full"]
        b = r["B_question_bootstrap"]
        dq = list(r["question_deltas"].values())
        print(f"  {model}: 两道题的 Δ = {[f'{x*100:+.1f}pp' for x in dq]}")
        print(f"    重采样只有 {b['distinct_resample_means']} 个不同取值，"
              f"权重 1/4 : 1/2 : 1/4 → 区间端点就是"
              f"[{b['lo']*100:+.1f}, {b['hi']*100:+.1f}]pp，"
              f"即 min/max(两题) 本身，无分布形态可言")

    print("\n" + "=" * W)
    print("§3  可达最小 two-sided p：单位决定天花板")
    print("=" * W)
    for model in models:
        p = report["power_floors"][model]
        print(f"  {model}: 题数={p['n_questions']} 答案数={p['n_answers']}  "
              f"答案级下限={p['min_two_sided_p_answer_unit']:.4f}  "
              f"题目级下限={p['min_two_sided_p_question_unit']:.4f}  "
              f"{'可达' if p['significant_possible_question_unit'] else '✗ 数学上不可能 < 0.05'}")

    print(f"\n结果已写入 {OUT}")


if __name__ == "__main__":
    main()
