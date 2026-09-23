"""试点报告数字复核与配对统计（只读）。

用法：
    cd AI-Marking/backend && .venv/bin/python scripts/pilot_paired_analysis.py

产出：
    outputs/saf_memory_study/_verify/pilot_paired_analysis.json

本脚本只读 ms_* 表，不写数据库、不调用模型。目的是把试点报告的
"每个 condition 各自的 bootstrap CI" 换成 "相对基线的配对 Δ bootstrap CI"，
并额外给出按题目分层（question-level）的效果，以及 token 口径的逐层核对。

统计口径说明：
    n=10 时模拟 bootstrap 有约 ±1.0pp 的种子抖动，因此这里**不做模拟**，
    改为穷举全部 C(k+k-1, k) 个重采样多重集并按多项系数加权，得到精确的
    bootstrap 分布（n=10 → 92,378 个多重集，可秒级枚举）。
    独立复核（含 BCa、精确符号翻转检验）见 scripts/pilot_paired_check.py。
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations_with_replacement
from math import factorial
from pathlib import Path
from statistics import mean

from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
OUT_DIR = REPO / "outputs" / "saf_memory_study" / "_verify"

CONDITION_ORDER = [
    "no_memory",
    "retrieval_full",
    "retrieval_no_feedback",
    "mem0_full",
    "mem0_no_feedback",
    "amem_full",
    "amem_no_feedback",
]
CONDITION_LABEL = {
    "no_memory": "无记忆基线",
    "retrieval_full": "确定性检索 · 含反馈",
    "retrieval_no_feedback": "确定性检索 · 无反馈",
    "mem0_full": "Mem0 · 含反馈",
    "mem0_no_feedback": "Mem0 · 无反馈",
    "amem_full": "A-MEM · 含反馈",
    "amem_no_feedback": "A-MEM · 无反馈",
}

BOOTSTRAP_NOTE = "exact enumeration of all resampled multisets"


def db_url() -> str:
    env = (BACKEND / ".env").read_text().splitlines()
    return next(line.split("=", 1)[1].strip() for line in env if line.startswith("DATABASE_URL"))


def bootstrap_ci(values: list[float]) -> tuple[float, float, float]:
    """对均值做**精确** percentile bootstrap；返回 (mean, lo, hi)。

    穷举全部长度为 k 的重采样多重集（共 C(2k-1, k) 个），每个的权重为
    多项系数 k!/∏m_i!；权重和恰好等于 k^k。n=10 时 92,378 个多重集。
    """
    if not values:
        return (float("nan"),) * 3
    k = len(values)
    weight_by_mean: dict[float, float] = defaultdict(float)
    for combo in combinations_with_replacement(range(k), k):
        counts = [0] * k
        for i in combo:
            counts[i] += 1
        w = factorial(k)
        for c in counts:
            if c > 1:
                w //= factorial(c)
        weight_by_mean[round(sum(counts[i] * values[i] for i in range(k)) / k, 12)] += w

    keys = sorted(weight_by_mean)
    total = k**k
    assert abs(sum(weight_by_mean[x] for x in keys) - total) < 1e-6 * total, "权重和 != k^k"

    def quantile(p: float) -> float:
        acc = 0.0
        for x in keys:
            acc += weight_by_mean[x]
            if acc / total >= p:
                return x
        return keys[-1]

    return mean(values), quantile(0.025), quantile(0.975)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url())
    result: dict = {"bootstrap_method": BOOTSTRAP_NOTE}

    with engine.connect() as conn:
        study_id = conn.execute(
            text("select id from ms_studies order by created_at desc limit 1")
        ).scalar()
        result["study_id"] = study_id

        # ---------- 1. 调用账本与 token 口径 ----------
        ledger = conn.execute(
            text(
                """
                select model, kind, count(*) n, coalesce(sum(total_tokens),0) tok,
                       coalesce(sum(input_tokens),0) tin, coalesce(sum(output_tokens),0) tout
                from ms_calls where study_id = :s
                group by model, kind order by kind, model
                """
            ),
            {"s": study_id},
        ).fetchall()
        result["ledger"] = [
            {"model": r.model, "kind": r.kind, "calls": r.n, "total_tokens": r.tok,
             "input_tokens": r.tin, "output_tokens": r.tout}
            for r in ledger
        ]

        score_rows = conn.execute(
            text(
                """
                select model, condition, count(*) n, coalesce(sum(total_tokens),0) tok,
                       round(coalesce(avg(total_tokens),0)) avg_call
                from ms_calls where study_id = :s and kind = 'score'
                group by model, condition order by model, condition
                """
            ),
            {"s": study_id},
        ).fetchall()
        result["score_token_by_condition"] = [
            {"model": r.model, "condition": r.condition, "score_calls": r.n,
             "block_tokens": r.tok, "per_call_tokens": float(r.avg_call)}
            for r in score_rows
        ]

        # 框架内部调用（记忆写入/提取/演化）单独记账
        fw = conn.execute(
            text(
                """
                select framework, phase, count(*) n, coalesce(sum(total_tokens),0) tok,
                       round(coalesce(avg(total_tokens),0)) avg_call,
                       round(coalesce(avg(latency_ms),0)) avg_ms
                from ms_framework_invocations where status = 'succeeded'
                group by framework, phase order by framework, phase
                """
            )
        ).fetchall()
        result["framework_invocations"] = [
            {"framework": r.framework, "phase": r.phase, "calls": r.n,
             "total_tokens": int(r.tok), "avg_tokens": float(r.avg_call),
             "avg_latency_ms": float(r.avg_ms)}
            for r in fw
        ]

        # ---------- 2. 失败尝试 ----------
        att = conn.execute(
            text(
                """
                select a.status, count(*) n
                from ms_call_attempts a join ms_calls c on c.id = a.call_id
                where c.study_id = :s group by a.status order by a.status
                """
            ),
            {"s": study_id},
        ).fetchall()
        result["attempts_by_status"] = [{"status": r.status, "n": r.n} for r in att]

        call_status = conn.execute(
            text("select status, count(*) n from ms_calls where study_id = :s group by status"),
            {"s": study_id},
        ).fetchall()
        result["calls_by_status"] = [{"status": r.status, "n": r.n} for r in call_status]

        failed_calls = conn.execute(
            text(
                """
                select id, model, kind, question_id, answer_id, attempt_count, status,
                       failure_code, started_at, completed_at
                from ms_calls where study_id = :s and attempt_count > 1
                order by id
                """
            ),
            {"s": study_id},
        ).fetchall()
        result["calls_with_retries"] = [
            {"call_id": r.id, "model": r.model, "kind": r.kind, "question": r.question_id,
             "answer_id": r.answer_id, "attempts": r.attempt_count, "status": r.status,
             "failure_code": r.failure_code,
             "started_at": str(r.started_at), "completed_at": str(r.completed_at)}
            for r in failed_calls
        ]

        # ---------- 3. 逐答案评分 ----------
        score_calls = conn.execute(
            text(
                """
                select model, question_id, answer_id, condition,
                       normalized_absolute_diff nad, normalized_signed_diff nsd,
                       manual_score, model_score, total_tokens
                from ms_calls
                where study_id = :s and kind = 'score' and status = 'succeeded'
                order by model, question_id, answer_id, condition
                """
            ),
            {"s": study_id},
        ).fetchall()

        # ---------- 4. 训练/测试记录归属（answer_id vs question_id）----------
        records = conn.execute(
            text(
                """
                select question_id, answer_id, source_split, selection_kind, count(*) n
                from ms_records where study_id = :s
                group by 1,2,3,4 order by question_id, answer_id
                """
            ),
            {"s": study_id},
        ).fetchall()
        result["records"] = [
            {"question_id": r.question_id, "answer_id": r.answer_id, "split": r.source_split,
             "selection": r.selection_kind, "n": r.n}
            for r in records
        ]

    # 索引：model -> question -> answer -> condition -> nad
    grid: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for r in score_calls:
        grid[r.model][r.question_id][r.answer_id][r.condition] = float(r.nad)

    result["models"] = sorted(grid)
    result["questions"] = {m: sorted(grid[m]) for m in grid}

    # ---------- 5. 配对统计：每个 condition 相对基线的 Δabs ND ----------
    def paired(model: str, question: str | None, cond: str) -> dict:
        base_vals, cond_vals, diffs, betters, worses, ties = [], [], [], 0, 0, 0
        qs = [question] if question else list(grid[model])
        for q in qs:
            for ans, by_cond in grid[model][q].items():
                if "no_memory" not in by_cond or cond not in by_cond:
                    continue
                b, c = by_cond["no_memory"], by_cond[cond]
                base_vals.append(b)
                cond_vals.append(c)
                d = c - b
                diffs.append(d)
                if c < b:
                    betters += 1
                elif c > b:
                    worses += 1
                else:
                    ties += 1
        m, lo, hi = bootstrap_ci(diffs)
        _, blo, bhi = bootstrap_ci(base_vals)
        _, clo, chi = bootstrap_ci(cond_vals)
        return {
            "n": len(diffs),
            "baseline_abs_nd": mean(base_vals) if base_vals else None,
            "baseline_ci": [blo, bhi],
            "condition_abs_nd": mean(cond_vals) if cond_vals else None,
            "condition_ci": [clo, chi],
            "delta": m,
            "delta_ci": [lo, hi],
            "betters": betters, "worses": worses, "ties": ties,
        }

    pooled = {}
    per_question = {}
    for model in result["models"]:
        pooled[model] = {}
        for cond in CONDITION_ORDER:
            if cond == "no_memory":
                continue
            pooled[model][cond] = paired(model, None, cond)
        per_question[model] = {}
        for q in sorted(grid[model]):
            per_question[model][q] = {}
            for cond in CONDITION_ORDER:
                if cond == "no_memory":
                    continue
                per_question[model][q][cond] = paired(model, q, cond)
    result["pooled_paired"] = pooled
    result["per_question_paired"] = per_question

    # ---------- 6. 逐答案明细（供热力图/复算）----------
    detail = defaultdict(list)
    for r in score_calls:
        detail[f"{r.model}|{r.question_id}"].append(
            {"answer_id": r.answer_id, "condition": r.condition,
             "abs_nd": float(r.nad), "signed_nd": float(r.nsd),
             "manual_score": r.manual_score, "model_score": r.model_score,
             "total_tokens": r.total_tokens}
        )
    result["detail"] = dict(detail)

    (OUT_DIR / "pilot_paired_analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # ---------- 控制台摘要 ----------
    print("=" * 78)
    print("token 口径核对（ms_calls.kind='score'）")
    print("=" * 78)
    for row in result["score_token_by_condition"]:
        print(f"  {row['model']:14s} {row['condition']:20s} calls={row['score_calls']:3d} "
              f"block_sum={row['block_tokens']:8,d} per_call={row['per_call_tokens']:8,.0f}")
    total = sum(r["block_tokens"] for r in result["score_token_by_condition"])
    print(f"  -> 140 次评分 token 总量 = {total:,d}")

    print("\n" + "=" * 78)
    print("配对统计：Δabs ND（负 = 优于基线）")
    print("=" * 78)
    for model in result["models"]:
        print(f"\n[{model}]")
        print(f"  {'condition':22s} {'n':>3s} {'baseline':>9s} {'cond':>7s} {'Δ':>8s} "
              f"{'Δ 95% CI':>20s}  更好/更差/持平")
        for cond, s in pooled[model].items():
            print(f"  {CONDITION_LABEL[cond]:22s} {s['n']:3d} "
                  f"{s['baseline_abs_nd']*100:8.1f}% {s['condition_abs_nd']*100:6.1f}% "
                  f"{s['delta']*100:+7.1f}pp "
                  f"[{s['delta_ci'][0]*100:+6.1f}, {s['delta_ci'][1]*100:+6.1f}] "
                  f"  {s['betters']}/{s['worses']}/{s['ties']}")

    print("\n" + "=" * 78)
    print("按题目分层：Δabs ND")
    print("=" * 78)
    for model in result["models"]:
        print(f"\n[{model}]")
        for q, conds in per_question[model].items():
            print(f"  {q}")
            for cond, s in conds.items():
                print(f"    {CONDITION_LABEL[cond]:22s} n={s['n']:2d} "
                      f"{s['baseline_abs_nd']*100:6.1f}% -> {s['condition_abs_nd']*100:6.1f}%  "
                      f"Δ={s['delta']*100:+6.1f}pp")

    print(f"\n结果已写入 {OUT_DIR / 'pilot_paired_analysis.json'}")


if __name__ == "__main__":
    main()
