"""一次性诊断脚本：汇总 r20 项目在 DB 中的真实 token 用量分布（只读）。

不修改任何数据，仅做聚合查询并打印文本报告。

用法:
    cd AI-Marking/backend
    PYTHONPATH=. python3 scripts/report_r20_tokens.py [project_id ...]

未传 project_id 时汇总全部项目。按 kind / condition / model /
attempt_number / attempt status / call status 分组，并依据冻结模型配置
估算费用（input/output cost fen per million）。
"""

from __future__ import annotations

import sys

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.r20 import R20Call, R20CallAttempt, R20Project


def _model_map(project: R20Project) -> dict[str, dict]:
    return {cfg["requested_model"]: cfg for cfg in project.model_configs_json}


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_cost(fen: int) -> str:
    yuan = fen / 100
    if yuan >= 10000:
        return f"¥{yuan / 10000:.2f}万"
    return f"¥{yuan:.2f}"


def _dt(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def _project_summary(db, project: R20Project) -> None:
    model_map = _model_map(project)

    calls = db.execute(
        select(func.count(R20Call.id)).where(R20Call.project_id == project.id)
    ).scalar_one()

    att_count, att_in, att_out, att_total = db.execute(
        select(
            func.count(R20CallAttempt.id),
            func.coalesce(func.sum(R20CallAttempt.input_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.output_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.total_tokens), 0),
        )
        .join(R20Call, R20CallAttempt.call_id == R20Call.id)
        .where(R20Call.project_id == project.id)
    ).one()

    rows = db.execute(
        select(
            R20Call.kind,
            R20Call.condition,
            R20CallAttempt.requested_model,
            func.count(R20CallAttempt.id),
            func.coalesce(func.sum(R20CallAttempt.input_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.output_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.total_tokens), 0),
        )
        .join(R20Call, R20CallAttempt.call_id == R20Call.id)
        .where(R20Call.project_id == project.id)
        .group_by(
            R20Call.kind, R20Call.condition, R20CallAttempt.requested_model
        )
        .order_by(R20Call.kind, R20Call.condition, R20CallAttempt.requested_model)
    ).all()

    # 每个 model 的输入/输出 token，用于估算费用
    model_tokens: dict[str, list[int]] = {}
    for _kind, _cond, model_id, _n, tin, tout, _ttot in rows:
        model_tokens.setdefault(model_id, [0, 0])
        model_tokens[model_id][0] += tin
        model_tokens[model_id][1] += tout

    total_fen = 0
    for model_id, (tin, tout) in model_tokens.items():
        cfg = model_map.get(model_id)
        if not cfg:
            continue
        total_fen += (
            tin * cfg["input_cost_fen_per_million"]
            + tout * cfg["output_cost_fen_per_million"]
        ) / 1_000_000
    total_fen = round(total_fen)

    sep = "=" * 74
    print(sep)
    print(
        f"Project: {project.name}   kind={project.kind}   status={project.status}"
    )
    print(
        f"  id={project.id}  created={_dt(project.created_at)}  "
        f"started={_dt(project.started_at)}  done={_dt(project.completed_at)}"
    )
    retry_ratio = att_count / calls if calls else 0
    print(
        f"  calls={calls}  attempts={att_count}  (attempts/calls={retry_ratio:.2f})"
    )
    print(
        f"  input={_fmt_tok(att_in)}  output={_fmt_tok(att_out)}  "
        f"total={_fmt_tok(att_total)}  est_cost={_fmt_cost(total_fen)}"
    )

    # 按 model
    print("  -- by model --")
    for model_id, (tin, tout) in sorted(model_tokens.items()):
        cfg = model_map.get(model_id)
        cost = 0
        if cfg:
            cost = round(
                (tin * cfg["input_cost_fen_per_million"]
                 + tout * cfg["output_cost_fen_per_million"])
                / 1_000_000
            )
        print(
            f"    {model_id:<32} in={_fmt_tok(tin):>6} "
            f"out={_fmt_tok(tout):>6} cost={_fmt_cost(cost)}"
        )

    # 按 kind
    print("  -- by kind --")
    kind_agg: dict[str, list[int]] = {}
    for _kind, _cond, _mid, n, tin, tout, ttot in rows:
        k = kind_agg.setdefault(_kind, [0, 0, 0, 0])
        k[0] += n
        k[1] += tin
        k[2] += tout
        k[3] += ttot
    for kind, (n, tin, tout, ttot) in sorted(kind_agg.items()):
        print(
            f"    {kind:<16} attempts={n:<5} in={_fmt_tok(tin):>6} "
            f"out={_fmt_tok(tout):>6} total={_fmt_tok(ttot):>6}"
        )

    # 按 condition
    print("  -- by condition --")
    cond_agg: dict[str, list[int]] = {}
    for _kind, _cond, _mid, n, tin, tout, ttot in rows:
        k = cond_agg.setdefault(_cond, [0, 0, 0, 0])
        k[0] += n
        k[1] += tin
        k[2] += tout
        k[3] += ttot
    for cond, (n, tin, tout, ttot) in sorted(cond_agg.items()):
        print(
            f"    {cond:<8} attempts={n:<5} in={_fmt_tok(tin):>6} "
            f"out={_fmt_tok(tout):>6} total={_fmt_tok(ttot):>6}"
        )

    # kind x condition
    print("  -- kind x condition --")
    for kind, cond, model_id, n, tin, tout, ttot in rows:
        print(
            f"    {kind:<16} {cond:<8} {model_id:<28} attempts={n:<5} "
            f"in={_fmt_tok(tin):>6} out={_fmt_tok(tout):>6} total={_fmt_tok(ttot):>6}"
        )

    # 按 attempt_number（重试放大）
    print("  -- attempts by attempt_number --")
    attempt_nums = db.execute(
        select(
            R20CallAttempt.attempt_number,
            func.count(R20CallAttempt.id),
            func.coalesce(func.sum(R20CallAttempt.input_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.output_tokens), 0),
        )
        .join(R20Call, R20CallAttempt.call_id == R20Call.id)
        .where(R20Call.project_id == project.id)
        .group_by(R20CallAttempt.attempt_number)
        .order_by(R20CallAttempt.attempt_number)
    ).all()
    for num, n, tin, tout in attempt_nums:
        label = " (retry)" if num >= 2 else ""
        print(
            f"    #{num}{label:<8} attempts={n:<5} in={_fmt_tok(tin):>6} "
            f"out={_fmt_tok(tout):>6}"
        )

    # 按 attempt status
    print("  -- attempts by status --")
    attempt_status = db.execute(
        select(
            R20CallAttempt.status,
            func.count(R20CallAttempt.id),
            func.coalesce(func.sum(R20CallAttempt.input_tokens), 0),
            func.coalesce(func.sum(R20CallAttempt.output_tokens), 0),
        )
        .join(R20Call, R20CallAttempt.call_id == R20Call.id)
        .where(R20Call.project_id == project.id)
        .group_by(R20CallAttempt.status)
        .order_by(func.count(R20CallAttempt.id).desc())
    ).all()
    for status, n, tin, tout in attempt_status:
        print(
            f"    {status:<20} attempts={n:<5} in={_fmt_tok(tin):>6} "
            f"out={_fmt_tok(tout):>6}"
        )

    # 按 call status
    print("  -- calls by status --")
    call_status = db.execute(
        select(R20Call.status, func.count(R20Call.id))
        .where(R20Call.project_id == project.id)
        .group_by(R20Call.status)
        .order_by(func.count(R20Call.id).desc())
    ).all()
    for status, n in call_status:
        print(f"    {status:<20} calls={n}")

    return calls, att_count, att_total, total_fen


def main() -> None:
    wanted = set(sys.argv[1:])
    db = SessionLocal()
    try:
        projects = db.execute(
            select(R20Project).order_by(R20Project.created_at)
        ).scalars().all()
        if not projects:
            print("DB 中没有 r20_projects 记录。")
            return

        if wanted:
            projects = [p for p in projects if p.id in wanted or p.name in wanted]
            if not projects:
                print(f"未找到匹配 project: {sorted(wanted)}")
                return

        grand_calls = 0
        grand_attempts = 0
        grand_tokens = 0
        grand_cost = 0
        for project in projects:
            calls, att_count, att_total, cost = _project_summary(db, project)
            grand_calls += calls
            grand_attempts += att_count
            grand_tokens += att_total
            grand_cost += cost

        print("=" * 74)
        print(
            f"GRAND TOTAL  projects={len(projects)}  calls={grand_calls}  "
            f"attempts={grand_attempts}  total_tokens={_fmt_tok(grand_tokens)}  "
            f"est_cost={_fmt_cost(grand_cost)}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
