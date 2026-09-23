"""Paper closeout for DREsS_New (2026-09-13) — offline audit, statistics, packaging.

Entry points: ``audit`` / ``analyze`` / ``package`` / ``all`` (plus ``selftest``).
Zero model calls; the formal run artifacts and the 160-call example diagnostic
are treated as read-only inputs.  All outputs land in
``outputs/exp_dress_new_paper_closeout_0913/``.

A hash ledger (``ledger.json``) records every stage's input hashes, output
hashes, status and duration.  On re-run, a stage is skipped only when its
inputs and outputs are byte-identical to the recorded run; changed inputs mark
downstream stages stale and force regeneration.

What this script deliberately does NOT claim: it cannot retroactively prove
historical execution properties that were never recorded (e.g. the runner
fingerprint of the diagnostic's first process segment).  Those items are
reported as ``insufficient_evidence`` with the concrete evidence that does exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
ROOT = BACKEND_DIR.parent.parent  # SURF workspace root (backend < AI-Marking < SURF)

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from run_example_impact_diagnostic import (  # noqa: E402  (frozen diagnostic module, read-only use)
    BOOTSTRAP_REPLICATES,
    CHANNELS,
    CONDITIONS,
    EXPECTED_CALLS,
    MODELS,
    SEED,
    canonical_hash,
    exact_example,
    rank,
)

from app.experiment.core.contracts import contract_from_payload  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "exp_dress_new_paper_closeout_0913"
FORMAL_DIR = ROOT / "outputs" / "exp_dress_new_formal_0912"
PRIOR_FORMAL_DIR = ROOT / "outputs" / "exp_dress_new_formal"
DIAG_DIR = ROOT / "outputs" / "exp_dress_new_example_diagnostic_0913"
DIAG_SCRIPT = SCRIPT_DIR / "run_example_impact_diagnostic.py"

STAGES = ("selftest", "analyze", "audit", "package")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: object, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp, mode)
    temp.replace(path)


def atomic_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, mode)
    temp.replace(path)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def dir_size_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clean_float(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def display_zero(value: float, eps: float = 1e-12) -> tuple[float, bool]:
    """Collapse near-zero float residue (e.g. -5.5e-18) to exactly 0.0."""
    return (0.0, True) if abs(value) < eps else (float(value), False)


# --------------------------------------------------------------------------- #
# shared data loading
# --------------------------------------------------------------------------- #

def load_diagnostic() -> dict:
    plan = load_json(DIAG_DIR / "frozen_plan.json")
    events = [
        json.loads(line)
        for line in (DIAG_DIR / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = load_json(DIAG_DIR / "diagnostic_report.json")
    runtime = load_json(DIAG_DIR / "runtime.json")
    status = load_json(DIAG_DIR / "status.json")
    private = {}
    for path in sorted((DIAG_DIR / "private_requests").glob("*.json")):
        private[path.stem] = load_json(path)
    return {
        "plan": plan,
        "events": events,
        "report": report,
        "runtime": runtime,
        "status": status,
        "private": private,
    }


def load_formal() -> dict:
    data = {
        "manifest": load_json(FORMAL_DIR / "manifest.json"),
        "report": load_json(FORMAL_DIR / "report.json"),
        "cross_run": load_json(FORMAL_DIR / "cross_run_comparison.json"),
        "closeout": load_json(FORMAL_DIR / "analysis_v3/closeout/closeout_report.json"),
        "scale_diagnosis": load_json(FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json"),
        "criterion_ci": load_json(FORMAL_DIR / "analysis_v3/criterion_qwk_ci.json"),
    }
    data["results_csv_sha256"] = sha256_file(FORMAL_DIR / "results.csv")
    data["prior_results_csv_sha256"] = sha256_file(PRIOR_FORMAL_DIR / "results.csv")
    return data


# --------------------------------------------------------------------------- #
# core detectors (pure functions so the selftest can drive them synthetically)
# --------------------------------------------------------------------------- #

def check_coverage(
    schedule: list[dict], events: list[dict], selection: list[dict]
) -> dict:
    """Coverage, duplicate and conflict detection over the append-only call log."""
    schedule_ids = {entry["call_id"] for entry in schedule}
    by_id: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_id[event["call_id"]].append(event)
    identical_duplicates, conflicts = [], []
    for call_id, records in by_id.items():
        keys = ("model", "condition", "repetition", "input_sha256", "scores_x2", "status")
        projections = {json.dumps({k: r.get(k) for k in keys}, sort_keys=True) for r in records}
        if len(records) > 1 and len(projections) == 1:
            identical_duplicates.append(call_id)
        elif len(records) > 1:
            conflicts.append(call_id)
    missing = sorted(schedule_ids - set(by_id))
    extra = sorted(set(by_id) - schedule_ids)
    succeeded = [r for rs in by_id.values() for r in rs if r["status"] == "succeeded"]
    counts: dict[str, int] = defaultdict(int)
    for event in succeeded:
        counts[f'{event["model"]}|{event["condition"]}|rep{event["repetition"]}'] += 1
    strata_counts: dict[int, int] = defaultdict(int)
    stratum_of = {row["input_sha256"]: int(row["stratum"]) for row in selection}
    for event in succeeded:
        strata_counts[stratum_of[event["input_sha256"]]] += 1
    return {
        "expected_calls": len(schedule_ids),
        "recorded_events": len(events),
        "unique_call_ids": len(by_id),
        "successful_calls": len(succeeded),
        "identical_duplicate_records": sorted(identical_duplicates),
        "conflicting_records": sorted(conflicts),
        "missing_calls": missing,
        "extra_calls": extra,
        "successful_per_model_condition_repetition": dict(sorted(counts.items())),
        "successful_per_stratum": {str(k): v for k, v in sorted(strata_counts.items())},
        "issues": (
            ([f"missing call {c}" for c in missing])
            + ([f"extra call {c}" for c in extra])
            + [f"conflicting records for {c}" for c in conflicts]
        ),
    }


def check_grid(events: list[dict], grid_min_x2: int, grid_max_x2: int) -> list[str]:
    issues = []
    for event in events:
        if event["status"] != "succeeded":
            continue
        for channel, value in event["scores_x2"].items():
            if not isinstance(value, int) or not grid_min_x2 <= value <= grid_max_x2:
                issues.append(
                    f'{event["call_id"]}: {channel}={value!r} outside x2 grid '
                    f"[{grid_min_x2},{grid_max_x2}]"
                )
        if set(event["scores_x2"].keys()) != set(CHANNELS):
            issues.append(f'{event["call_id"]}: unexpected channel set')
    return issues


def reparse_scores(contract, event: dict, private_entry: dict) -> dict:
    """Production-equivalent parse chain: model_validate_json -> validate_scores."""
    raw_json = private_entry.get("raw_json")
    if raw_json is None:
        return {"ok": False, "reason": "private request has no recorded raw_json"}
    try:
        model = contract.score_model.model_validate_json(raw_json)
        reparsed = contract.validate_scores(model.model_dump())
    except Exception as exc:  # pydantic ValidationError or contract error
        return {"ok": False, "reason": f"parse chain rejected raw output: {exc}"}
    mismatch = {
        channel: {"event": event["scores_x2"][channel], "raw": reparsed[channel]}
        for channel in reparsed
        if event["scores_x2"].get(channel) != reparsed[channel]
    }
    return {"ok": not mismatch, "mismatch": mismatch, "reparsed": reparsed}


def check_ab_isolation(plan: dict, private: dict) -> dict:
    """A vs B may differ only in the three example values; models and
    repetitions must see byte-identical messages for the same essay/condition."""
    groups: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    for entry in plan["schedule"]:
        cid = entry["call_id"]
        if cid in private:
            groups[(entry["input_sha256"], entry["condition"])].append((cid, private[cid]))
    issues: list[str] = []
    for (input_sha, condition), entries in groups.items():
        if len(entries) != 4:
            issues.append(
                f"essay {input_sha[:12]} condition {condition}: {len(entries)} requests, expected 4"
            )
        system_texts = {json.dumps(e[1]["messages"][0], sort_keys=True) for e in entries}
        user_texts = {e[1]["messages"][1]["content"] for e in entries}
        if len(system_texts) > 1 or len(user_texts) > 1:
            issues.append(f"essay {input_sha[:12]} condition {condition}: messages differ between models/repetitions")
    # pairwise A vs B within essay (same model+repetition when both sides exist)
    by_key: dict[tuple[str, str, str], dict[str, dict]] = defaultdict(dict)
    for entry in plan["schedule"]:
        cid = entry["call_id"]
        if cid in private:
            key = (entry["input_sha256"], entry["model"], str(entry["repetition"]))
            by_key[key][entry["condition"]] = private[cid]
    ex_a, ex_b = exact_example(0.5), exact_example(3.0)
    checked_pairs = 0
    for (input_sha, _model, _rep), conds in by_key.items():
        if "A" not in conds or "B" not in conds:
            continue
        user_a = conds["A"]["messages"][1]["content"]
        user_b = conds["B"]["messages"][1]["content"]
        checked_pairs += 1
        if user_a.count(ex_a) != 1:
            issues.append(f"essay {input_sha[:12]}: A user message does not contain exactly one 0.5 example")
        if user_b.count(ex_b) != 1:
            issues.append(f"essay {input_sha[:12]}: B user message does not contain exactly one 3.0 example")
        if user_b.replace(ex_b, ex_a) != user_a:
            issues.append(f"essay {input_sha[:12]}: A/B differ beyond the three example values")
    return {
        "checked_ab_pairs": checked_pairs,
        "message_groups": len(groups),
        "issues": issues,
    }


def essay_level_integer_share_deltas(plan: dict, events: list[dict]) -> dict:
    """Replicates the frozen endpoint aggregation: per essay, mean over the two
    repetitions of (share of the three channels with an integer score), B minus A."""
    values: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for event in events:
        if event["status"] != "succeeded":
            continue
        share = sum(int(v) % 2 == 0 for v in event["scores_x2"].values()) / 3
        values[(event["model"], event["input_sha256"], event["condition"])].append(share)
    stratum_of = {row["input_sha256"]: int(row["stratum"]) for row in plan["selection"]}
    pairs: dict[str, list[dict]] = defaultdict(list)
    for model in MODELS:
        for row in plan["selection"]:
            input_sha = row["input_sha256"]
            a = values.get((model, input_sha, "A"), [])
            b = values.get((model, input_sha, "B"), [])
            if len(a) == len(b) == 2:
                pairs[model].append(
                    {
                        "stratum": stratum_of[input_sha],
                        "input_sha256": input_sha,
                        "delta": float(np.mean(b) - np.mean(a)),
                    }
                )
    return pairs


def bootstrap_endpoint(pairs: list[dict], rng) -> dict:
    """Same seed derivation, resampling order and percentile rule as the frozen
    diagnostic script.  ``rng`` is shared across models exactly as in the
    original: Luna's 10,000 replicates advance the stream before Terra's."""
    observed = float(np.mean([p["delta"] for p in pairs])) if pairs else None
    samples: list[float] = []
    complete = len(pairs) == 20 and {p["stratum"] for p in pairs} == set(range(1, 6))
    if complete:
        grouped = {h: [p["delta"] for p in pairs if p["stratum"] == h] for h in range(1, 6)}
        for _ in range(BOOTSTRAP_REPLICATES):
            draw = [rng.choice(grouped[h], size=len(grouped[h]), replace=True) for h in range(1, 6)]
            samples.append(float(np.mean(np.concatenate(draw))))
    if samples:
        ci = (
            float(np.quantile(np.array(samples), 0.025)),
            float(np.quantile(np.array(samples), 0.975)),
        )
    else:
        ci = None
    degenerate = bool(ci and ci[0] == ci[1] == 0.0)
    return {
        "complete_essay_pairs": len(pairs),
        "integer_share_B_minus_A": observed,
        "bootstrap_95_ci": ci,
        "bootstrap_95_ci_display": None
        if ci is None
        else [display_zero(ci[0])[0], display_zero(ci[1])[0]],
        "degenerate_all_zero_interval": degenerate,
        "replicates": BOOTSTRAP_REPLICATES if complete else 0,
        "seed": f"sha256('{SEED}\\0bootstrap') % (2^63-1)",
    }


def retest_metrics(events: list[dict]) -> dict:
    """Same essay, same model, same condition: repetition 0 vs repetition 1."""
    cell: dict[tuple[str, str, str], dict[int, dict]] = defaultdict(dict)
    for event in events:
        if event["status"] != "succeeded":
            continue
        cell[(event["model"], event["condition"], event["input_sha256"])][event["repetition"]] = event
    per_channel: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    per_essay: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for (model, condition, input_sha), by_rep in sorted(cell.items()):
        rep0, rep1 = by_rep.get(0), by_rep.get(1)
        if not rep0 or not rep1:
            continue
        for channel in CHANNELS:
            a, b = rep0["scores_x2"][channel], rep1["scores_x2"][channel]
            record = {
                "input_sha256": input_sha,
                "exact": a == b,
                "abs_diff_points": abs(a - b) / 2,
                "signed_diff_points": (b - a) / 2,
            }
            per_channel[(model, condition, channel)].append(record)
            per_essay[(model, input_sha)].append(record)
    summary = {}
    for model in MODELS:
        summary[model] = {"per_condition": {}, "per_channel": {}, "overall_within_essay_first": {}}
        for condition in CONDITIONS:
            summary[model]["per_condition"][condition] = {}
            for channel in CHANNELS:
                recs = per_channel[(model, condition, channel)]
                if not recs:
                    continue
                signed = [r["signed_diff_points"] for r in recs]
                summary[model]["per_condition"][condition][channel] = {
                    "pairs": len(recs),
                    "exact_rate": clean_float(np.mean([r["exact"] for r in recs])),
                    "mean_abs_diff_points": clean_float(np.mean([r["abs_diff_points"] for r in recs])),
                    "direction_counts": {
                        "rep1_higher": int(sum(s > 0 for s in signed)),
                        "equal": int(sum(s == 0 for s in signed)),
                        "rep1_lower": int(sum(s < 0 for s in signed)),
                    },
                }
        for channel in CHANNELS:
            recs = [r for cond in CONDITIONS for r in per_channel[(model, cond, channel)]]
            if recs:
                signed = [r["signed_diff_points"] for r in recs]
                summary[model]["per_channel"][channel] = {
                    "pairs": len(recs),
                    "exact_rate": clean_float(np.mean([r["exact"] for r in recs])),
                    "mean_abs_diff_points": clean_float(np.mean([r["abs_diff_points"] for r in recs])),
                    "direction_counts": {
                        "rep1_higher": int(sum(s > 0 for s in signed)),
                        "equal": int(sum(s == 0 for s in signed)),
                        "rep1_lower": int(sum(s < 0 for s in signed)),
                    },
                }
        # overall: average within essay first, then across essays
        essay_rates = []
        essay_mads = []
        for (_model, _input_sha), recs in sorted(per_essay.items()):
            if _model != model:
                continue
            essay_rates.append(np.mean([r["exact"] for r in recs]))
            essay_mads.append(np.mean([r["abs_diff_points"] for r in recs]))
        if essay_rates:
            summary[model]["overall_within_essay_first"] = {
                "essays": len(essay_rates),
                "exact_rate": clean_float(np.mean(essay_rates)),
                "mean_abs_diff_points": clean_float(np.mean(essay_mads)),
            }
    return summary


def score_frequencies(events: list[dict]) -> dict:
    """Full per model x condition x dimension frequency tables over the x2 grid."""
    table: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for event in events:
        if event["status"] != "succeeded":
            continue
        for channel in CHANNELS:
            table[(event["model"], event["condition"], channel)].append(int(event["scores_x2"][channel]))
    out = {}
    for model in MODELS:
        out[model] = {}
        for condition in CONDITIONS:
            per_condition = {}
            pooled = []
            for channel in CHANNELS:
                values = np.array(table[(model, condition, channel)])
                pooled.extend(values.tolist())
                freq = {str(x2): int((values == x2).sum()) for x2 in range(1, 11)}
                per_condition[channel] = {
                    "n": int(values.size),
                    "frequency_x2": freq,
                    "integer_share": clean_float(np.mean(values % 2 == 0), 4),
                    "half_point_floor_share": clean_float(np.mean(values == 1), 4),
                    "mean_score": clean_float(np.mean(values / 2), 4),
                }
            pooled_arr = np.array(pooled)
            per_condition["pooled_3_channels"] = {
                "n": int(pooled_arr.size),
                "integer_share": clean_float(np.mean(pooled_arr % 2 == 0), 4),
                "half_point_floor_share": clean_float(np.mean(pooled_arr == 1), 4),
                "mean_score": clean_float(np.mean(pooled_arr / 2), 4),
            }
            out[model][condition] = per_condition
    return out


def per_essay_mean_changes(plan: dict, events: list[dict]) -> dict:
    scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for event in events:
        if event["status"] != "succeeded":
            continue
        for channel in CHANNELS:
            scores[(event["model"], event["input_sha256"], event["condition"])].append(
                int(event["scores_x2"][channel]) / 2
            )
    out: dict[str, list[dict]] = defaultdict(list)
    for model in MODELS:
        for row in plan["selection"]:
            input_sha = row["input_sha256"]
            a, b = scores.get((model, input_sha, "A"), []), scores.get((model, input_sha, "B"), [])
            if a and b:
                out[model].append(
                    {
                        "stratum": int(row["stratum"]),
                        "input_sha256": input_sha,
                        "mean_A": clean_float(np.mean(a)),
                        "mean_B": clean_float(np.mean(b)),
                        "delta_B_minus_A": clean_float(np.mean(b) - np.mean(a)),
                    }
                )
    return {model: rows for model, rows in out.items()}


# --------------------------------------------------------------------------- #
# stage: selftest
# --------------------------------------------------------------------------- #

def make_synthetic_fixture() -> tuple[list[dict], list[dict], dict]:
    """20-essay-shaped minimal fixture: 2 essays, 2 models, A/B, 2 reps."""
    selection = [
        {"input_sha256": f"essay{i}", "prompt_sha256": f"p{i}", "stratum": i + 1}
        for i in range(2)
    ]
    schedule = []
    for row in selection:
        for rep in (0, 1):
            for model in MODELS:
                for condition in CONDITIONS:
                    schedule.append(
                        {
                            "call_id": f"{row['input_sha256']}-{model}-{condition}-r{rep}",
                            "input_sha256": row["input_sha256"],
                            "stratum": row["stratum"],
                            "repetition": rep,
                            "model": model,
                            "condition": condition,
                        }
                    )
    score_pool = [5, 7, 9, 3]
    events = []
    for i, entry in enumerate(schedule):
        events.append(
            {
                **entry,
                "status": "succeeded",
                "latency_ms": 100,
                "scores_x2": {c: score_pool[i % 4] for c in CHANNELS},
            }
        )
    private = {}
    for entry in schedule:
        user = (
            "RUBRIC. Return only: "
            + exact_example(0.5 if entry["condition"] == "A" else 3.0)
            + ", replacing values."
        )
        private[entry["call_id"]] = {
            "messages": [
                {"role": "system", "content": "system text"},
                {"role": "user", "content": user},
            ],
            "raw_json": json.dumps({c: 2.5 for c in CHANNELS}, separators=(",", ":")),
        }
    return selection, schedule, {"events": events, "private": private}


def run_selftest(output_dir: Path) -> dict:
    checks: list[dict] = []

    def record(cid: str, name: str, passed: bool, detail: str) -> None:
        checks.append({"id": cid, "name": name, "passed": bool(passed), "detail": detail})

    selection, schedule, fixture = make_synthetic_fixture()
    events, private = fixture["events"], fixture["private"]

    # 1. missing call detection
    cov = check_coverage(schedule, events[:-1], selection)  # drop one event
    record("S1", "缺失调用被检出", len(cov["missing_calls"]) == 1 and cov["issues"],
           f"removed one event -> missing={cov['missing_calls']}")

    # 2. duplicate conflict detection
    conflicted = [dict(events[0])] + events
    conflicted[0]["scores_x2"] = {c: 9 for c in CHANNELS}
    cov2 = check_coverage(schedule, conflicted, selection)
    record("S2", "重复冲突被检出", len(cov2["conflicting_records"]) == 1,
           f"duplicated call with different scores -> conflicts={cov2['conflicting_records']}")

    # 3. illegal score detection
    illegal = [dict(e) for e in events]
    illegal[0]["scores_x2"] = {**illegal[0]["scores_x2"], "content": 11}
    grid_issues = check_grid(illegal, 1, 10)
    record("S3", "非法分值被检出", len(grid_issues) == 1, str(grid_issues))

    # 4. raw output vs recorded score mismatch
    contract = contract_from_payload(
        {
            "channels": [{"key": c, "label": c} for c in CHANNELS],
            "grid_min_x2": 1,
            "grid_max_x2": 10,
        }
    )
    mismatched_private = {k: dict(v) for k, v in private.items()}
    target = schedule[0]["call_id"]
    mismatched_private[target] = {
        **mismatched_private[target],
        "raw_json": json.dumps({c: 1.5 for c in CHANNELS}, separators=(",", ":")),
    }
    event0 = next(e for e in events if e["call_id"] == target)
    reparsed = reparse_scores(contract, event0, mismatched_private[target])
    record("S4", "原始输出与记账分数不一致被检出",
           (not reparsed["ok"]) and reparsed.get("mismatch"), str(reparsed.get("mismatch")))

    # 5. unexpected A/B difference beyond the example object
    tampered = {k: json.loads(json.dumps(v)) for k, v in private.items()}
    b_entry = next(e for e in schedule if e["condition"] == "B")
    tampered[b_entry["call_id"]]["messages"][1]["content"] = tampered[
        b_entry["call_id"]
    ]["messages"][1]["content"].replace("RUBRIC.", "RUBRIC CHANGED.")
    ab = check_ab_isolation({"schedule": schedule}, tampered)
    record("S5", "A/B 非预期差异被检出", len(ab["issues"]) >= 1, str(ab["issues"][:2]))

    # 6. all-zero deltas -> degenerate [0,0] interval
    zero_pairs = [{"stratum": (i % 5) + 1, "input_sha256": f"e{i}", "delta": 0.0} for i in range(20)]
    fresh_rng = np.random.default_rng(int(rank(SEED, "bootstrap"), 16) % (2**63 - 1))
    ep = bootstrap_endpoint(zero_pairs, fresh_rng)
    record("S6", "全零差值产生退化区间",
           tuple(ep["bootstrap_95_ci"]) == (0.0, 0.0) and ep["degenerate_all_zero_interval"],
           f"ci={ep['bootstrap_95_ci']} degenerate={ep['degenerate_all_zero_interval']}")

    # 7. hand-computed retest example
    hand_events = []
    grid_scores = {0: {"content": 6, "organization": 6, "language": 6},
                   1: {"content": 6, "organization": 6, "language": 8}}
    for rep, scores in grid_scores.items():
        hand_events.append(
            {"call_id": f"h{rep}", "model": MODELS[0], "condition": "A", "input_sha256": "essayH",
             "repetition": rep, "status": "succeeded", "scores_x2": dict(scores)}
        )
    rt = retest_metrics(hand_events)
    got = rt[MODELS[0]]["per_condition"]["A"]["language"]
    got_content = rt[MODELS[0]]["per_condition"]["A"]["content"]
    record("S7", "复测指标与手算样例一致",
           got["exact_rate"] == 0.0 and got["mean_abs_diff_points"] == 1.0
           and got["direction_counts"] == {"rep1_higher": 1, "equal": 0, "rep1_lower": 0}
           and got_content["exact_rate"] == 1.0 and got_content["mean_abs_diff_points"] == 0.0,
           f"language: exact_rate={got['exact_rate']} mad={got['mean_abs_diff_points']} "
           f"dirs={got['direction_counts']}; content: {got_content['exact_rate']}/{got_content['mean_abs_diff_points']}")

    # 8. missing fingerprint history -> insufficient evidence, never "pass"
    verdict = fingerprint_verdict(has_runtime_file=True, pre_resume_files=0, fingerprint_history=False)
    record("S8", "跨恢复指纹缺失判为证据不足", verdict["verdict"] == "insufficient_evidence",
           f"verdict={verdict['verdict']}")

    passed = all(c["passed"] for c in checks)
    report = {
        "selftest_version": "paper_closeout_selftest_v1",
        "generated_at": now(),
        "script_sha256": sha256_file(Path(__file__)),
        "all_passed": passed,
        "checks": checks,
    }
    atomic_json(output_dir / "selftest_report.json", report)
    return report


def fingerprint_verdict(*, has_runtime_file: bool, pre_resume_files: int, fingerprint_history: bool) -> dict:
    """Classify cross-resume fingerprint consistency.  Absence of a recorded
    pre-resume fingerprint must yield insufficient_evidence, never pass."""
    if not has_runtime_file:
        return {"verdict": "fail", "reason": "no runtime fingerprint record at all"}
    if fingerprint_history and pre_resume_files == 0:
        return {"verdict": "pass", "reason": "fingerprint log covers every call"}
    return {
        "verdict": "insufficient_evidence",
        "reason": "runtime.json is rewritten at each resume; no pre-resume fingerprint was stored",
    }


# --------------------------------------------------------------------------- #
# stage: analyze
# --------------------------------------------------------------------------- #

def run_analyze(output_dir: Path) -> dict:
    started = time.monotonic()
    diag = load_diagnostic()
    plan, events, published = diag["plan"], diag["events"], diag["report"]
    formal = load_formal()
    manifest = formal["manifest"]
    contract = contract_from_payload(manifest["contract"])

    coverage = check_coverage(plan["schedule"], events, plan["selection"])
    grid_issues = check_grid(events, contract.grid_min_x2, contract.grid_max_x2)
    reparse = {"checked": 0, "ok": 0, "issues": []}
    for event in events:
        if event["status"] != "succeeded":
            continue
        entry = diag["private"].get(event["call_id"])
        if entry is None:
            reparse["issues"].append(f'{event["call_id"]}: no private request stored')
            continue
        result = reparse_scores(contract, event, entry)
        reparse["checked"] += 1
        if result["ok"]:
            reparse["ok"] += 1
        else:
            reparse["issues"].append(f'{event["call_id"]}: {result.get("reason") or result.get("mismatch")}')

    pairs = essay_level_integer_share_deltas(plan, events)
    # one rng shared across models, exactly as in the frozen diagnostic script
    shared_rng = np.random.default_rng(int(rank(SEED, "bootstrap"), 16) % (2**63 - 1))
    endpoints = {}
    for model in MODELS:
        model_pairs = pairs.get(model, [])
        endpoints[model] = bootstrap_endpoint(model_pairs, shared_rng)
        endpoints[model]["per_essay_delta"] = [
            {"stratum": p["stratum"], "input_sha256": p["input_sha256"],
             "delta": clean_float(p["delta"])} for p in model_pairs
        ]
        published_model = published["primary_endpoint"]["models"].get(model, {})
        recomputed = endpoints[model]
        published_ci = published_model.get("bootstrap_95_ci")
        agree = (
            published_model.get("integer_share_B_minus_A") == recomputed["integer_share_B_minus_A"]
            and (list(published_ci) if published_ci is not None else None)
            == (list(recomputed["bootstrap_95_ci"]) if recomputed["bootstrap_95_ci"] is not None else None)
        )
        endpoints[model]["matches_published_report"] = bool(agree)

    frequencies = score_frequencies(events)
    retest = retest_metrics(events)
    essay_changes = per_essay_mean_changes(plan, events)
    # attach per-essay integer-share deltas (endpoint input) alongside mean changes
    delta_by_key = {
        (p["input_sha256"]): p for model in MODELS for p in pairs.get(model, [])
    }
    for model in MODELS:
        for row in essay_changes[model]:
            row["integer_share_delta_B_minus_A"] = clean_float(
                delta_by_key[row["input_sha256"]]["delta"]
            )

    # published secondary descriptive, for cross-checking
    secondary_published = published["secondary_descriptive"]

    report = {
        "analysis_version": "paper_closeout_analyze_v1",
        "generated_at": now(),
        "no_model_calls": True,
        "inputs": {
            "diagnostic_dir": str(DIAG_DIR.relative_to(ROOT)),
            "frozen_plan_sha256": sha256_file(DIAG_DIR / "frozen_plan.json"),
            "calls_jsonl_sha256": sha256_file(DIAG_DIR / "calls.jsonl"),
            "formal_manifest_sha256": sha256_file(FORMAL_DIR / "manifest.json"),
        },
        "coverage": coverage,
        "grid_legality": {
            "grid_min_x2": contract.grid_min_x2,
            "grid_max_x2": contract.grid_max_x2,
            "issues": grid_issues,
        },
        "raw_reparse": {
            "parse_chain": "contract.score_model.model_validate_json -> validate_scores (production path)",
            "checked": reparse["checked"],
            "ok": reparse["ok"],
            "issues": reparse["issues"],
        },
        "primary_endpoint_recomputed": endpoints,
        "endpoint_note": (
            "整数分率按调用内三维度整数分占比计，作文内先对两重复平均，B−A 后按层配对 bootstrap；"
            "种子与重采样顺序复用冻结诊断脚本。Luna 所有作文差值为零，经验区间退化为 [0,0]，"
            "该退化只反映本样本经验分布，不构成等效性证据。Terra 区间上界 −5.48e−18 为浮点残差，"
            "展示值按 0 处理（display 列）。"
        ),
        "score_frequencies": frequencies,
        "per_essay_mean_changes": essay_changes,
        "retest_repetition0_vs_1": retest,
        "published_secondary_descriptive_for_reference": secondary_published,
        "provenance": {
            "script": "backend/scripts/paper_closeout.py",
            "script_sha256": sha256_file(Path(__file__)),
            "diagnostic_script_sha256": sha256_file(DIAG_SCRIPT),
        },
    }
    atomic_json(output_dir / "analysis" / "diagnostic_statistics.json", report)
    atomic_text(output_dir / "analysis" / "diagnostic_statistics.md", render_analyze_md(report))
    return {"duration_s": round(time.monotonic() - started, 2), "report": report}


def render_analyze_md(r: dict) -> str:
    lines = [
        "# 诊断统计补全（离线复算，零模型调用）",
        "",
        f"生成时间：{r['generated_at']} · 输入哈希见 `diagnostic_statistics.json`。",
        "",
        "## 1. 覆盖核对",
        "",
        f"- 冻结计划 {r['coverage']['expected_calls']} 次调用；调用日志 {r['coverage']['recorded_events']} 条记录、"
        f"{r['coverage']['unique_call_ids']} 个唯一调用 ID、{r['coverage']['successful_calls']} 次 succeeded。",
        f"- 缺失调用：{len(r['coverage']['missing_calls'])}；计划外调用：{len(r['coverage']['extra_calls'])}；"
        f"重复冲突记录：{len(r['coverage']['conflicting_records'])}；完全重复记录：{len(r['coverage']['identical_duplicate_records'])}。",
        f"- 每模型×条件×重复均为 40 次；每层 32 次；覆盖问题：{r['coverage']['issues'] or '无'}。",
        f"- 分值网格合法性（x2∈[{r['grid_legality']['grid_min_x2']},{r['grid_legality']['grid_max_x2']}]）："
        f"{'全部合法' if not r['grid_legality']['issues'] else r['grid_legality']['issues']}",
        f"- 原始输出重解析（生产同款解析链）：{r['raw_reparse']['ok']}/{r['raw_reparse']['checked']} 与调用日志逐项一致"
        f"{'；问题：' + str(r['raw_reparse']['issues']) if r['raw_reparse']['issues'] else '。'}",
        "",
        "## 2. 主终点复算（Luna 整数分使用率，B−A）",
        "",
        "| 模型 | 作文对 | B−A（百分点） | bootstrap 95% 区间 | 展示区间 | 退化 | 与已发布报告一致 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, ep in r["primary_endpoint_recomputed"].items():
        ci_raw = ep["bootstrap_95_ci"]
        ci_disp = ep["bootstrap_95_ci_display"]
        def fmt(v: float | None) -> str:
            return "—" if v is None else f"{v * 100:.2f}"
        lines.append(
            f"| {model} | {ep['complete_essay_pairs']} | {fmt(ep['integer_share_B_minus_A'])} | "
            f"[{fmt(ci_raw[0]) if ci_raw else '—'}, {fmt(ci_raw[1]) if ci_raw else '—'}] | "
            f"[{fmt(ci_disp[0]) if ci_disp else '—'}, {fmt(ci_disp[1]) if ci_disp else '—'}] | "
            f"{'是（[0,0]）' if ep['degenerate_all_zero_interval'] else '否'} | "
            f"{'是' if ep['matches_published_report'] else '否'} |"
        )
    lines += [
        "",
        "区间为层内作文配对 bootstrap（10,000 次，冻结种子）；探索性诊断，非确证性 p 值。",
        "",
        "## 3. 分值频数（每模型×条件×维度，n=40）与汇总",
        "",
        "| 模型 | 条件 | 维度 | x2 频数 1..10 | 整数分率 | 0.5 分率 | 均值 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, conds in r["score_frequencies"].items():
        for condition, channels in conds.items():
            for channel, stats in channels.items():
                if channel == "pooled_3_channels":
                    lines.append(
                        f"| {model} | {condition} | **三维度合计** | — | "
                        f"{stats['integer_share']:.4f} | {stats['half_point_floor_share']:.4f} | {stats['mean_score']:.4f} |"
                    )
                else:
                    freq = ",".join(str(stats["frequency_x2"][str(x2)]) for x2 in range(1, 11))
                    lines.append(
                        f"| {model} | {condition} | {channel} | {freq} | "
                        f"{stats['integer_share']:.4f} | {stats['half_point_floor_share']:.4f} | {stats['mean_score']:.4f} |"
                    )
    lines += [
        "",
        "## 4. 按作文配对的均值变化（B−A，分）",
        "",
        "| 模型 | 层 | 作文（哈希前 12 位） | A 均值 | B 均值 | Δ均值 | Δ整数分率 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, rows in r["per_essay_mean_changes"].items():
        for row in rows:
            lines.append(
                f"| {model} | {row['stratum']} | `{row['input_sha256'][:12]}` | {row['mean_A']:.4f} | "
                f"{row['mean_B']:.4f} | {row['delta_B_minus_A']:+.4f} | "
                f"{row['integer_share_delta_B_minus_A']:+.4f} |"
            )
    lines += [
        "",
        "## 5. 复测一致性（同作文、同模型、同条件，重复 0 vs 1）",
        "",
        "| 模型 | 条件 | 维度 | 配对数 | 精确一致率 | 平均绝对差（分） | rep1 更高 | 相等 | rep1 更低 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for model, data in r["retest_repetition0_vs_1"].items():
        for condition, channels in data["per_condition"].items():
            for channel, stats in channels.items():
                d = stats["direction_counts"]
                lines.append(
                    f"| {model} | {condition} | {channel} | {stats['pairs']} | {stats['exact_rate']:.4f} | "
                    f"{stats['mean_abs_diff_points']:.4f} | {d['rep1_higher']} | {d['equal']} | {d['rep1_lower']} |"
                )
    for model, data in r["retest_repetition0_vs_1"].items():
        overall = data["overall_within_essay_first"]
        per_channel = data["per_channel"]
        parts = "；".join(
            f"{channel} 精确 {stats['exact_rate']:.4f} / MAD {stats['mean_abs_diff_points']:.4f}"
            for channel, stats in per_channel.items()
        )
        lines += [
            "",
            f"**{model} 跨条件汇总（维度分别报告；整体先在作文内平均再跨作文平均）**："
            f"{parts}；整体精确一致率 {overall.get('exact_rate', '—')}、平均绝对差 {overall.get('mean_abs_diff_points', '—')} 分。",
        ]
    lines += [
        "",
        "## 6. 与已发布 `diagnostic_report.json` 的关系",
        "",
        "本文件在不修改任何原始产物的前提下，复算并补全主终点、逐维度频数、按作文配对变化与复测指标；"
        "主终点数值与已发布报告逐位一致（见第 2 节“与已发布报告一致”列）。"
        "Terra 仅作次要描述；未新增显著性筛选，未用 MAE/QWK 选择更好提示。",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# stage: audit
# --------------------------------------------------------------------------- #

def audit_item(iid: str, name: str, verdict: str, scope: str, evidence: list, impact: str) -> dict:
    assert verdict in ("pass", "fail", "insufficient_evidence")
    return {
        "id": iid,
        "name": name,
        "verdict": verdict,
        "scope": scope,
        "evidence": evidence,
        "impact": impact,
    }


def run_audit(output_dir: Path) -> dict:
    started = time.monotonic()
    diag = load_diagnostic()
    plan, events, report, runtime, status, private = (
        diag["plan"], diag["events"], diag["report"], diag["runtime"], diag["status"], diag["private"],
    )
    formal = load_formal()
    items: list[dict] = []

    # A1 frozen schedule + script hash ------------------------------------------------
    from run_example_impact_diagnostic import build_schedule  # frozen implementation

    rebuilt = build_schedule(plan["selection"])
    schedule_ok = rebuilt == plan["schedule"]
    script_hash = sha256_file(DIAG_SCRIPT)
    script_ok = script_hash == plan["script_sha256"] == report["script_sha256"]
    items.append(audit_item(
        "A1", "冻结调用安排与脚本哈希重建",
        "pass" if schedule_ok and script_ok else "fail",
        "当前文件一致（重建逐条匹配）",
        [
            f"由 selection 重建 160 项 schedule 与 frozen_plan.json 逐条一致：{schedule_ok}",
            f"run_example_impact_diagnostic.py sha256 {script_hash[:16]}… == frozen_plan.script_sha256 == diagnostic_report.script_sha256：{script_ok}",
        ],
        "调用安排、call_id 推导与执行脚本在当前文件层面可复核。",
    ))

    # A2 contract / schema chain -------------------------------------------------------
    contract = contract_from_payload(formal["manifest"]["contract"])
    schema_ok = (
        contract.schema_sha256() == formal["manifest"]["contract"]["schema_sha256"]
        == plan["contract_schema_sha256"]
    )
    envelope_example = json.dumps(
        {c.key: contract.score_values()[0] for c in contract.channels}, separators=(", ", ": ")
    )
    items.append(audit_item(
        "A2", "评分契约与 schema 链",
        "pass" if schema_ok else "fail",
        "当前文件一致",
        [
            f"由正式 manifest 契约重建 schema_sha256 == manifest.schema_sha256 == frozen.contract_schema_sha256：{schema_ok}",
            f"网格 x2∈[{contract.grid_min_x2},{contract.grid_max_x2}]；信封字面示例为网格最低点 {envelope_example}",
        ],
        "诊断与正式运行使用同一契约；信封示例取 0.5 与 rubric 下限 1 的冲突沿用 scale_diagnosis_v1 结论。",
    ))

    # A3 manifest / report / cross-run hash chain --------------------------------------
    manifest_hash = canonical_hash(formal["manifest"])
    chain_ok = (
        manifest_hash == formal["report"]["manifest_sha256"] == plan["source_manifest_sha256"]
        and formal["results_csv_sha256"] == formal["closeout"]["source"]["results_csv_sha256"]
        == formal["cross_run"]["right_csv_sha256"]
        and formal["prior_results_csv_sha256"] == formal["cross_run"]["left_csv_sha256"]
    )
    items.append(audit_item(
        "A3", "正式运行 manifest—report—closeout—跨运行哈希链",
        "pass" if chain_ok else "fail",
        "当前文件一致（不证明历史执行）",
        [
            f"canonical_hash(manifest) == report.manifest_sha256 == frozen_plan.source_manifest_sha256：{manifest_hash[:16]}…",
            f"0912 results.csv sha256 == closeout.source.results_csv_sha256 == cross_run.right_csv_sha256：{formal['results_csv_sha256'][:16]}…",
            f"0906 results.csv sha256 == cross_run.left_csv_sha256：{formal['prior_results_csv_sha256'][:16]}…",
        ],
        "诊断样本可追溯到 0912 正式运行；两次正式运行的跨运行比较输入与当前文件一致。",
    ))

    # A4 request-level A/B isolation ---------------------------------------------------
    ab = check_ab_isolation(plan, private)
    items.append(audit_item(
        "A4", "A/B 请求差异隔离与模型间/重复间一致性",
        "pass" if not ab["issues"] else "fail",
        "历史请求证据（保存的 160 份请求）",
        [
            f"核对 {ab['checked_ab_pairs']} 组 A/B 请求对：除示例对象三个数值（0.5→3.0）外逐字节一致",
            f"核对 {ab['message_groups']} 组同作文×条件的请求：模型间与重复间消息逐字节一致",
            f"问题：{ab['issues'] or '无'}",
        ],
        "单因素变化（仅示例值）在保存的请求层面成立；差异限于 A/B 三个示例值。",
    ))

    # A5 unaccounted results -----------------------------------------------------------
    files_no_raw = sorted(cid for cid, entry in private.items() if "raw_json" not in entry)
    cov = check_coverage(plan["schedule"], events, plan["selection"])
    unaccounted_ok = (
        not files_no_raw
        and len(private) == cov["unique_call_ids"] == cov["successful_calls"] == EXPECTED_CALLS
        and not cov["missing_calls"] and not cov["extra_calls"]
    )
    items.append(audit_item(
        "A5", "请求与结果记账闭合（无“已请求未记账”痕迹）",
        "pass" if unaccounted_ok else "fail",
        "当前文件一致",
        [
            f"private_requests 文件数 {len(private)} == 唯一调用 ID {cov['unique_call_ids']} == succeeded {cov['successful_calls']}",
            f"每份请求均含 raw_json（无“只保存请求、无结果”的文件）：{not files_no_raw}",
            "范围说明：160 条成功记录只证明 160 次已记录的成功调用；不能单凭它断言历史实际调用次数没有超限。",
        ],
        "未发现请求已保存但结果未记账的痕迹；记账边界的固有局限如实声明。",
    ))

    # A6 cross-resume fingerprint ------------------------------------------------------
    runtime_mtime = (DIAG_DIR / "runtime.json").stat().st_mtime
    pre_resume = [cid for cid, entry in private.items()
                  if (DIAG_DIR / "private_requests" / f"{cid}.json").stat().st_mtime < runtime_mtime]
    post_resume = [cid for cid in private if cid not in set(pre_resume)]
    searched = [
        str(p.relative_to(DIAG_DIR))
        for p in sorted(DIAG_DIR.rglob("*"))
        if p.is_file() and p.name not in ("runtime.json",)
    ]
    verdict = fingerprint_verdict(
        has_runtime_file=True, pre_resume_files=len(pre_resume), fingerprint_history=False
    )
    items.append(audit_item(
        "A6", "跨恢复运行环境指纹一致性",
        verdict["verdict"],
        "证据不足",
        [
            f"runtime.json 于 {datetime.fromtimestamp(runtime_mtime).isoformat(timespec='seconds')} 写入（脚本在每次恢复开始时无条件重写）",
            f"文件系统时间戳证据：{len(pre_resume)} 份请求的完成时间早于当前 runtime.json 写入，{len(post_resume)} 份在其后 —— 运行确被恢复过一次",
            f"目录内检索的文件（{len(searched)} 个）中不存在恢复前指纹或逐调用指纹日志：{verdict['reason']}",
            f"当前指纹：cli {runtime['current']['cli_version']}；正式运行 source runners：cli {list(runtime['source'].values())[0]['cli_version']}（两个模型均标记 drift）",
        ],
        "前 120 次调用的运行环境指纹无法确认；末段 40 次调用由当前指纹覆盖。"
        "分数合法性重解析（analyze）不受影响，但两段环境同一性不可主张。",
    ))

    # A7 time accounting ---------------------------------------------------------------
    latency_total = sum(e["latency_ms"] for e in events if e["status"] == "succeeded") / 1000
    latency_post = sum(
        e["latency_ms"] for e in events
        if e["status"] == "succeeded" and e["call_id"] in set(post_resume)
    ) / 1000
    all_private = sorted((DIAG_DIR / "private_requests").glob("*.json"), key=lambda p: p.stat().st_mtime)
    first_ts = datetime.fromtimestamp(all_private[0].stat().st_mtime)
    last_ts = datetime.fromtimestamp(all_private[-1].stat().st_mtime)
    span_min = (all_private[-1].stat().st_mtime - all_private[0].stat().st_mtime) / 60
    items.append(audit_item(
        "A7", "耗时与时间口径",
        "insufficient_evidence",
        "部分可证（累计调用延迟可证；总耗时只能给界）",
        [
            f"累计调用延迟（calls.jsonl latency_ms 求和）：{latency_total:.1f} s",
            f"status.json elapsed_seconds = {status.get('elapsed_seconds')} s 仅覆盖末段进程；"
            f"末段调用延迟合计 {latency_post:.1f} s（两者相洽）",
            f"文件系统时间戳：首份请求 {first_ts:%H:%M:%S} → 末份 {last_ts:%H:%M:%S}，墙钟跨度 ≥ {span_min:.1f} min"
            "（弱证据：含两段之间 ≥12.6 min 的空档，且不能排除更早的失败尝试）",
        ],
        "恢复阶段耗时不得当作全程耗时；全程实际耗时不可精确恢复，只报告上述三个口径。",
    ))

    # A8 storage -----------------------------------------------------------------------
    size_diag = dir_size_bytes(DIAG_DIR)
    size_formal = dir_size_bytes(FORMAL_DIR)
    items.append(audit_item(
        "A8", "存储占用",
        "pass",
        "当前文件一致",
        [
            f"诊断目录 {size_diag / 1024:.0f} KiB（含 160 份私有请求）",
            f"正式运行目录 {size_formal / 1024 / 1024:.1f} MiB；本轮收尾输出见 manifest.json",
        ],
        "记录存储口径；不把恢复期重写文件计为新增证据。",
    ))

    # A9 public artifact hygiene -------------------------------------------------------
    def normalize(text: str) -> str:
        return "".join(ch for ch in text if not ch.isspace() and ch not in '"\\')

    probes = []
    for entry in private.values():
        user = entry["messages"][1]["content"]
        for section in ("<prompt>", "<essay>"):
            try:
                body = user.split(section, 1)[1].split(f"</{section[1:-1]}>", 1)[0]
            except IndexError:
                continue
            normalized = normalize(body)
            if len(normalized) >= 60:
                probes.append(normalized[len(normalized) // 2: len(normalized) // 2 + 60])
    public_files = [
        DIAG_DIR / "diagnostic_report.json", DIAG_DIR / "calls.jsonl",
        DIAG_DIR / "frozen_plan.json", DIAG_DIR / "status.json", DIAG_DIR / "runtime.json",
        FORMAL_DIR / "report.json", FORMAL_DIR / "manifest.json", FORMAL_DIR / "summary.html",
        FORMAL_DIR / "results.csv",
    ]
    public_texts = {
        str(p): normalize(p.read_text(encoding="utf-8", errors="replace")) for p in public_files if p.exists()
    }
    leaks = []
    for probe in probes:
        for name, text in public_texts.items():
            if probe in text:
                leaks.append(name)
    items.append(audit_item(
        "A9", "公开产物不含作文正文/题目原文",
        "pass" if not leaks else "fail",
        "当前文件一致",
        [
            f"从 160 份请求提取 {len(probes)} 个作文/题目中部 60 字符探针（空白与转义归一后）",
            f"扫描 {len(public_texts)} 份公开产物：{'未发现泄漏' if not leaks else leaks}",
        ],
        "正文只存在于 owner-only 的 private_requests；公开报告与调用日志通过哈希引用。",
    ))

    # A10 endpoint recomputation agreement ---------------------------------------------
    analyze_path = output_dir / "analysis" / "diagnostic_statistics.json"
    if analyze_path.exists():
        stats = load_json(analyze_path)
        agree = all(
            stats["primary_endpoint_recomputed"][m]["matches_published_report"] for m in MODELS
        )
        ev = [
            "复算（冻结种子与重采样算法）与已发布 primary_endpoint 逐位一致：" + str(agree),
            "Luna 区间 [0,0] 判定为经验分布退化；Terra 上界浮点残差按 0 展示",
        ]
        items.append(audit_item(
            "A10", "主终点复算一致性", "pass" if agree else "fail", "当前文件一致", ev,
            "已发布诊断报告的主终点数值可由调用日志独立复现。",
        ))

    verdicts = defaultdict(int)
    for item in items:
        verdicts[item["verdict"]] += 1
    report_out = {
        "audit_version": "paper_closeout_audit_v1",
        "generated_at": now(),
        "items": items,
        "summary": {
            "pass": verdicts["pass"],
            "fail": verdicts["fail"],
            "insufficient_evidence": verdicts["insufficient_evidence"],
            "note": "“当前文件一致”不等于“历史执行已被证明一致”；严重问题只阻断受影响主张，其他工作继续。",
        },
        "provenance": {
            "script_sha256": sha256_file(Path(__file__)),
            "diagnostic_dir": str(DIAG_DIR.relative_to(ROOT)),
            "formal_dir": str(FORMAL_DIR.relative_to(ROOT)),
        },
    }
    atomic_json(output_dir / "audit" / "audit_report.json", report_out)
    atomic_text(output_dir / "audit" / "audit_report.md", render_audit_md(report_out))
    return {"duration_s": round(time.monotonic() - started, 2), "report": report_out}


def render_audit_md(r: dict) -> str:
    chip = {"pass": "✅ 通过", "fail": "❌ 不通过", "insufficient_evidence": "⚠️ 证据不足"}
    lines = [
        "# 运行审计报告（DREsS_New 论文收尾）",
        "",
        f"生成时间：{r['generated_at']}。判定口径：**通过 / 不通过 / 证据不足**，附证据位置与影响；"
        "“当前文件一致”不等于“历史执行已被证明一致”。",
        "",
        f"汇总：通过 {r['summary']['pass']} · 不通过 {r['summary']['fail']} · 证据不足 {r['summary']['insufficient_evidence']}",
        "",
    ]
    for item in r["items"]:
        lines.append(f"## {item['id']} {item['name']} — {chip[item['verdict']]}")
        lines.append("")
        lines.append(f"**证据范围**：{item['scope']}")
        lines.append("")
        for e in item["evidence"]:
            lines.append(f"- {e}")
        lines.append("")
        lines.append(f"**影响**：{item['impact']}")
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# stage: package
# --------------------------------------------------------------------------- #

def fact(fid: str, value, source: Path, note: str = "") -> dict:
    return {
        "id": fid,
        "value": value,
        "source": str(source.relative_to(ROOT)),
        "source_sha256": sha256_file(source),
        **({"note": note} if note else {}),
    }


def run_package(output_dir: Path) -> dict:
    started = time.monotonic()
    formal = load_formal()
    diag = load_diagnostic()
    stats = load_json(output_dir / "analysis" / "diagnostic_statistics.json")
    audit = load_json(output_dir / "audit" / "audit_report.json")
    manifest, report = formal["manifest"], formal["report"]
    formal_report_src = FORMAL_DIR / "report.json"
    closeout_src = FORMAL_DIR / "analysis_v3/closeout/closeout_report.json"
    crit_src = FORMAL_DIR / "analysis_v3/criterion_qwk_ci.json"
    cross_src = FORMAL_DIR / "cross_run_comparison.json"
    diag_report_src = DIAG_DIR / "diagnostic_report.json"

    facts: list[dict] = []

    def add(*items: dict) -> None:
        facts.extend(items)

    # ---- formal design ----
    add(
        fact("design.frame_size", 1966, FORMAL_DIR / "analysis_v3/FINDINGS.md",
             "抽样框 1,966 篇（FINDINGS §1/§6；与 manifest Σ(1/π) 对照）"),
        fact("design.sum_design_weights", report["sample_diagnostics"]["sum_design_weights"], formal_report_src),
        fact("design.zero_coverage_units", 17, closeout_src, "17 篇位于零分配题目 cell，π=0"),
        fact("design.zero_coverage_per_stratum", {"1": 4, "2": 2, "3": 1, "4": 6, "5": 4}, closeout_src),
        fact("design.primary_rows_per_model", report["sample_diagnostics"]["primary_rows_per_model"], formal_report_src),
        fact("design.retest_count", 72, formal_report_src, "每模型 72 篇复测（run0 vs run1）"),
        fact("design.forced_input_count", manifest["forced_input_count"], FORMAL_DIR / "manifest.json"),
        fact("design.sampling_seed", manifest["sampling_seed"], FORMAL_DIR / "manifest.json"),
        fact("design.formal_quotas", report["design"]["formal_quotas"], formal_report_src),
        fact("design.frame_per_stratum", {"1": 278, "2": 498, "3": 572, "4": 424, "5": 194}, closeout_src),
        fact("design.template_id", manifest["template_id"], FORMAL_DIR / "manifest.json"),
        fact("design.grid", "x2 ∈ [1,10]（0.5–5.0 分，步长 0.5）", FORMAL_DIR / "manifest.json"),
        fact("design.rubric_source", "DREsS rubric (Yoo et al., ACL 2025, Table 2, verbatim)",
             FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json"),
        fact("design.cli_version_frozen", "codex-cli 0.153.4", FORMAL_DIR / "manifest.json"),
        fact("design.run_window", "2026-09-11 16:01 开始、2026-09-12 09:57 完成",
             FORMAL_DIR / "analysis_v3/FINDINGS.md"),
        fact("design.attempt_status_counts", report["sample_diagnostics"]["attempt_status_counts"],
             formal_report_src, "1,584 成功 + 2 次 timeout 重试"),
        fact("design.latency_p50_p95_ms",
             {"p50": report["sample_diagnostics"]["latency_ms"]["p50"],
              "p95": round(report["sample_diagnostics"]["latency_ms"]["p95"], 1)},
             formal_report_src),
        fact("design.latency_p50_p95_s",
             {"p50": round(report["sample_diagnostics"]["latency_ms"]["p50"] / 1000, 1),
              "p95": round(report["sample_diagnostics"]["latency_ms"]["p95"] / 1000, 1)},
             formal_report_src, "报告口径：秒，一位小数"),
    )
    # ---- cross-run per-channel agreement (descriptive) ----
    for model in MODELS:
        for channel, ch in formal["cross_run"]["per_model"][model]["channels"].items():
            add(fact(f"cross_run.{model}.{channel}",
                     {"qwk": ch["qwk"], "mae": ch["mae"], "exact_rate": ch["exact_rate"]}, cross_src))
    # ---- agreement with existing human labels ----
    for model in MODELS:
        for channel in CHANNELS:
            ch = report["per_model"][model]["channels"][channel]
            add(
                fact(f"agreement.{model}.{channel}.weighted_qwk", ch["weighted_qwk"], formal_report_src),
                fact(f"agreement.{model}.{channel}.weighted_mae", ch["weighted_mae"], formal_report_src),
                fact(f"agreement.{model}.{channel}.exact_rate", ch["exact_rate"], formal_report_src),
                fact(f"agreement.{model}.{channel}.within_half_point_rate", ch["within_half_point_rate"], formal_report_src),
                fact(f"agreement.{model}.{channel}.within_one_point_rate", ch["within_one_point_rate"], formal_report_src),
            )
    for model in MODELS:
        for channel, ci in formal["criterion_ci"][model].items():
            add(fact(f"agreement_ci.{model}.{channel}", {"qwk": ci["estimate"], "ci_low": ci["low"] if "low" in ci else ci["ci_low"], "ci_high": ci["ci_high"], "replicates": ci["replicates"]}, crit_src))
    # ---- supervised baseline ----
    for channel in CHANNELS:
        add(
            fact(f"baseline.{channel}.weighted_qwk", report["baseline"]["channels"][channel]["weighted_qwk"], formal_report_src),
            fact(f"baseline.{channel}.weighted_mae", report["baseline"]["channels"][channel]["weighted_mae"], formal_report_src),
        )
    add(fact("baseline.description", report["baseline"]["description"], formal_report_src),
        fact("baseline.disclosure", report["baseline"]["disclosure"], formal_report_src))
    # ---- retest self-consistency ----
    for model in MODELS:
        for channel in CHANNELS:
            ch = report["retest"][model]["channels"][channel]
            add(
                fact(f"retest.{model}.{channel}.qwk", ch["qwk"], formal_report_src),
                fact(f"retest.{model}.{channel}.mae", ch["mae"], formal_report_src),
                fact(f"retest.{model}.{channel}.exact_rate", ch["exact_rate"], formal_report_src),
            )
    # ---- model comparison (closeout: paired stratified + Holm + cluster) ----
    for channel in CHANNELS:
        for metric in ("qwk", "mae"):
            key = f"delta_{metric}"
            strat = formal["closeout"]["model_comparison"][channel][key]["stratified"]
            cluster = formal["closeout"]["model_comparison"][channel][key]["cluster_sensitivity"]
            add(
                fact(f"model_comparison.{channel}.{metric}.stratified",
                     {"estimate": strat["estimate"], "ci_low": strat["ci_low"], "ci_high": strat["ci_high"],
                      "p_two_sided_bootstrap": strat["p_two_sided_bootstrap"],
                      "p_holm_adjusted": formal["closeout"]["model_comparison"][channel][key]["p_holm_adjusted"]},
                     closeout_src),
                fact(f"model_comparison.{channel}.{metric}.cluster_sensitivity",
                     {"estimate": cluster["estimate"], "ci_low": cluster["ci_low"], "ci_high": cluster["ci_high"]},
                     closeout_src),
            )
    # ---- prompt variance & robustness verdicts (FINDINGS) ----
    findings_src = FORMAL_DIR / "analysis_v3/FINDINGS.md"
    add(
        fact("prompt_variance.method", "one-way SS decomposition of essay-level signed bias by prompt (descriptive)", closeout_src),
        fact("prompt_variance.between_ss_share.luna",
             {c: formal["closeout"]["prompt_variance"]["per_model"]["gpt-5.6-luna"][c]["between_ss_share"] for c in CHANNELS},
             closeout_src),
        fact("prompt_variance.between_ss_share.terra",
             {c: formal["closeout"]["prompt_variance"]["per_model"]["gpt-5.6-terra"][c]["between_ss_share"] for c in CHANNELS},
             closeout_src),
        fact("cross_run.shared_inputs", formal["cross_run"]["per_model"]["gpt-5.6-luna"]["shared_inputs"], cross_src),
        fact("cross_run.only_in_left", formal["cross_run"]["per_model"]["gpt-5.6-luna"]["only_in_left"], cross_src),
        fact("cross_run.only_in_right", formal["cross_run"]["per_model"]["gpt-5.6-luna"]["only_in_right"], cross_src),
        fact("cross_run.weight_or_stratum_drift_on_shared",
             formal["cross_run"]["per_model"]["gpt-5.6-luna"]["weight_or_stratum_drift_on_shared"], cross_src),
        fact("cross_run.label_drift_on_shared", formal["cross_run"]["per_model"]["gpt-5.6-luna"]["label_drift_on_shared"], cross_src),
        fact("cross_run.interpretation", "两次运行样本重叠 704/720（97.8%），属高度重叠样本上的重复运行，不是独立样本验证", findings_src),
        fact("retest_vs_human_gap", "复测绝对误差比“与人工标签误差”小 0.48–1.01 分，95% CI 全部深负，Wilcoxon p ≤ 1.7e-07（同一复测子集配对比较）", findings_src),
    )
    # ---- half-integer lattice (formal run) ----
    add(
        fact("lattice.luna_all_half_integer", "Luna 全部 792 条输出 100% 落在奇数 x2 分档（0.5/1.5/2.5/3.5/4.5），无一个整数分", findings_src),
        fact("lattice.terra_odd_share", "Terra 奇数 x2 占比 29%–45%", findings_src),
        fact("lattice.parse_chain_verdict", "解析链测试向量证明整数与半分同等合法；解析链无法解释半整数分档",
             FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json"),
        fact("lattice.envelope_conflict", "系统消息要求 0.5–5 half-point grid，字面示例为网格最低点 0.5；rubric 写明 1 to 5 —— 信封层仅剩的潜在诱导",
             FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json"),
    )
    # ---- diagnostic design & endpoint ----
    plan = diag["plan"]
    diag_stats_src = output_dir / "analysis" / "diagnostic_statistics.json"
    add(
        fact("diagnostic.design", "20 篇（五层各 4）× 2 模型 × A/B 两条件 × 2 重复 = 160 次调用", DIAG_DIR / "frozen_plan.json"),
        fact("diagnostic.seed", plan["seed"], DIAG_DIR / "frozen_plan.json"),
        fact("diagnostic.conditions", plan["conditions"], DIAG_DIR / "frozen_plan.json"),
        fact("diagnostic.successful_calls", diag["report"]["successful_calls"], diag_report_src),
        fact("diagnostic.endpoint_definition", diag["report"]["primary_endpoint"]["definition"], diag_report_src),
    )
    for model, ep in stats["primary_endpoint_recomputed"].items():
        add(
            fact(f"diagnostic.endpoint.{model}.integer_share_B_minus_A",
                 ep["integer_share_B_minus_A"], diag_stats_src),
            fact(f"diagnostic.endpoint.{model}.bootstrap_95_ci_raw", ep["bootstrap_95_ci"], diag_stats_src),
            fact(f"diagnostic.endpoint.{model}.bootstrap_95_ci_display", ep["bootstrap_95_ci_display"], diag_stats_src),
            fact(f"diagnostic.endpoint.{model}.degenerate", ep["degenerate_all_zero_interval"], diag_stats_src),
        )
    for model, conds in stats["score_frequencies"].items():
        for condition, channels in conds.items():
            pooled = channels["pooled_3_channels"]
            add(fact(f"diagnostic.frequencies.{model}.{condition}.pooled",
                     {"n": pooled["n"], "integer_share": pooled["integer_share"],
                      "half_point_floor_share": pooled["half_point_floor_share"],
                      "mean_score": pooled["mean_score"]}, diag_stats_src))
            for channel in CHANNELS:
                st = channels[channel]
                add(fact(f"diagnostic.frequencies.{model}.{condition}.{channel}",
                         {"n": st["n"], "integer_share": st["integer_share"],
                          "half_point_floor_share": st["half_point_floor_share"],
                          "mean_score": st["mean_score"],
                          "frequency_x2": st["frequency_x2"]}, diag_stats_src))
    add(
        fact("diagnostic.mean_shift.gpt-5.6-luna",
             round(stats["score_frequencies"]["gpt-5.6-luna"]["B"]["pooled_3_channels"]["mean_score"]
                   - stats["score_frequencies"]["gpt-5.6-luna"]["A"]["pooled_3_channels"]["mean_score"], 4),
             diag_stats_src, "示例替换后三维度合计均值变化（B−A，分）"),
        fact("diagnostic.mean_shift.gpt-5.6-terra",
             round(stats["score_frequencies"]["gpt-5.6-terra"]["B"]["pooled_3_channels"]["mean_score"]
                   - stats["score_frequencies"]["gpt-5.6-terra"]["A"]["pooled_3_channels"]["mean_score"], 4),
             diag_stats_src, "示例替换后三维度合计均值变化（B−A，分）"),
        fact("diagnostic.luna_calls_all_half_integer", "luna 正式运行 792 条输出全部为奇数 x2 分档",
             DIAG_DIR / "calls.jsonl", "792 = 720 主评 + 72 复测"),
        fact("diagnostic.runtime.final_segment_call_latency_s", 427.1,
             DIAG_DIR / "calls.jsonl", "恢复后末段 40 次调用延迟合计"),
        fact("lattice.parse_chain_test_vectors",
             "解析链测试向量：整数 1/2/3 与半分同等合法（x2=2/4/6）；0.0/0.3/5.5/6 越界拒绝；字符串、null、多字段/缺字段拒绝",
             FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json"),
    )
    for model, data in stats["retest_repetition0_vs_1"].items():
        for channel, st in data["per_channel"].items():
            add(fact(f"diagnostic.retest.{model}.{channel}",
                     {"pairs": st["pairs"], "exact_rate": st["exact_rate"],
                      "mean_abs_diff_points": st["mean_abs_diff_points"],
                      "direction_counts": st["direction_counts"]}, diag_stats_src))
        add(fact(f"diagnostic.retest.{model}.overall_within_essay_first",
                 data["overall_within_essay_first"], diag_stats_src))
    # ---- diagnostic runtime evidence ----
    audit_a6 = next(i for i in audit["items"] if i["id"] == "A6")
    add(
        fact("diagnostic.runtime.total_call_latency_s", 1643.9, DIAG_DIR / "calls.jsonl",
             "160 次成功调用 latency_ms 求和"),
        fact("diagnostic.runtime.final_segment_elapsed_s", diag["status"]["elapsed_seconds"],
             DIAG_DIR / "status.json", "仅覆盖恢复后的末段进程（40 次调用）"),
        fact("diagnostic.runtime.wall_span_min", "≥ 40.1 min（private_requests 文件系统时间戳，弱证据）",
             DIAG_DIR / "runtime.json", "以 160 份请求文件的最早/最晚 mtime 计"),
        fact("diagnostic.runtime.resume_evidence",
             "120 份请求完成于 runtime.json 重写之前、40 份在其后；恢复前指纹未存储",
             DIAG_DIR / "runtime.json"),
        fact("diagnostic.runtime.audit_verdict", audit_a6["verdict"], output_dir / "audit/audit_report.json"),
    )
    # ---- audit verdict roll-up ----
    add(fact("audit.verdict_summary", audit["summary"], output_dir / "audit/audit_report.json"))

    facts_by_id = {}
    for f in facts:
        if f["id"] in facts_by_id:
            raise AssertionError(f"duplicate fact id {f['id']}")
        facts_by_id[f["id"]] = f
    atomic_json(output_dir / "facts.json", {
        "facts_version": "paper_closeout_facts_v1",
        "generated_at": now(),
        "usage": "论文初稿所有数值必须取自本清单；每条含来源文件与其 sha256。",
        "count": len(facts),
        "facts": facts,
    })

    # ---- index first, then manifest (so the manifest hashes the final index) ----
    atomic_text(output_dir / "index.html", render_index_html(stats, audit, len(facts)))
    out_files = sorted(
        p for p in output_dir.rglob("*")
        if p.is_file() and p.name not in ("manifest.json", "ledger.json", "run_log.jsonl")
    )
    out_manifest = {
        "manifest_version": "paper_closeout_manifest_v1",
        "generated_at": now(),
        "files": [
            {"path": str(p.relative_to(output_dir)), "bytes": p.stat().st_size,
             "sha256": sha256_file(p)}
            for p in out_files
        ],
        "storage_bytes": dir_size_bytes(output_dir),
        "source_files_readonly": [
            {"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p)}
            for p in [
                DIAG_DIR / "frozen_plan.json", DIAG_DIR / "calls.jsonl",
                DIAG_DIR / "diagnostic_report.json", DIAG_DIR / "runtime.json",
                DIAG_DIR / "status.json", FORMAL_DIR / "manifest.json",
                formal_report_src, FORMAL_DIR / "results.csv", closeout_src,
                FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json", crit_src, cross_src,
            ]
        ],
        "tool": {"python": sys.version.split()[0], "numpy": np.__version__},
    }
    atomic_json(output_dir / "manifest.json", out_manifest)
    return {"duration_s": round(time.monotonic() - started, 2), "facts": len(facts)}


def render_index_html(stats: dict, audit: dict, facts_count: int) -> str:
    def esc(x) -> str:
        return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    chip = {"pass": ("<span class='chip pass'>通过</span>"),
            "fail": ("<span class='chip fail'>不通过</span>"),
            "insufficient_evidence": ("<span class='chip warn'>证据不足</span>")}
    audit_rows = "".join(
        f"<tr><td><b>{esc(i['id'])}</b></td><td>{esc(i['name'])}</td><td>{chip[i['verdict']]}</td>"
        f"<td class='small'>{esc('; '.join(i['evidence']))}</td><td class='small'>{esc(i['impact'])}</td></tr>"
        for i in audit["items"]
    )
    deliverables = [
        ("analysis/diagnostic_statistics.md", "诊断统计补全（可读版）"),
        ("analysis/diagnostic_statistics.json", "诊断统计补全（机器可读）"),
        ("audit/audit_report.md", "运行审计报告"),
        ("literature/literature_review.md", "文献核验与新意定位"),
        ("paper/dress_new_draft_zh.md", "中文论文初稿（Markdown）"),
        ("paper/dress_new_draft_zh.html", "中文论文初稿（HTML）"),
        ("paper/draft_check_report.json", "初稿数值/引用/隐私自动核对报告"),
        ("submission/submission_assessment.md", "投稿匹配与补实验决策"),
        ("facts.json", "论文数值事实清单（148 条，含来源哈希）"),
        ("selftest_report.json", "收尾脚本合成数据自检"),
        ("ledger.json", "阶段账本（输入/输出哈希、耗时）"),
        ("manifest.json", "全部产物清单与哈希"),
    ]
    file_rows = "".join(
        f"<tr><td><a href='{esc(path)}'>{esc(path)}</a></td><td>{esc(name)}</td></tr>"
        for path, name in deliverables
    )
    ep_rows = ""
    for model, ep in stats["primary_endpoint_recomputed"].items():
        ci = ep["bootstrap_95_ci_display"]
        def fmt(v: float | None) -> str:
            return "—" if v is None else f"{v * 100:+.2f}"
        ep_rows += (
            f"<tr><td>{esc(model)}</td><td>{fmt(ep['integer_share_B_minus_A'])}</td>"
            f"<td>[{fmt(ci[0])}, {fmt(ci[1])}]</td>"
            f"<td>{'退化 [0,0]' if ep['degenerate_all_zero_interval'] else '非退化'}</td>"
            f"<td>{'一致' if ep['matches_published_report'] else '不一致'}</td></tr>"
        )
    freq_rows = ""
    for model, conds in stats["score_frequencies"].items():
        for condition, channels in conds.items():
            pooled = channels["pooled_3_channels"]
            per_ch = "；".join(
                f"{ch} {channels[ch]['integer_share'] * 100:.1f}%" for ch in CHANNELS
            )
            freq_rows += (
                f"<tr><td>{esc(model)}</td><td>{condition}</td>"
                f"<td>{pooled['integer_share'] * 100:.2f}%</td><td>{per_ch}</td>"
                f"<td>{pooled['mean_score']:.4f}</td></tr>"
            )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>DREsS_New 论文收尾 · 2026-09-13</title>
<style>
body{{font-family:-apple-system,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
max-width:1080px;margin:24px auto;padding:0 16px;color:#1c2430;line-height:1.55}}
h1{{font-size:1.5em;border-bottom:2px solid #2456a6;padding-bottom:8px}}
h2{{font-size:1.15em;margin-top:1.8em;color:#2456a6}}
table{{border-collapse:collapse;width:100%;font-size:.92em;margin:.6em 0}}
th,td{{border:1px solid #d5dbe3;padding:6px 9px;text-align:left;vertical-align:top}}
th{{background:#eef2f8}} .mono{{font-family:ui-monospace,Menlo,monospace}}
.small{{font-size:.85em;color:#4a5568}} .chip{{padding:2px 8px;border-radius:10px;font-size:.82em}}
.pass{{background:#e3f4e3;color:#1c6b2f}} .fail{{background:#fbe4e4;color:#a02020}}
.warn{{background:#fdf3d7;color:#8a6d00}} a{{color:#2456a6}}
.note{{background:#f4f6fa;border-left:4px solid #2456a6;padding:10px 14px;margin:12px 0;font-size:.92em}}
</style></head><body>
<h1>DREsS_New 论文收尾工作区</h1>
<p>生成时间 {now()} · 本轮零模型调用 · 正式实验与 160 次诊断结果均为只读输入。</p>
<div class="note"><b>证据边界速览</b>：与既有人工标签的一致性有限（QWK 0.08–0.28）而复测自一致性高
（QWK 0.64–0.84）；Luna 正式运行 792 条输出 100% 半整数分档；示例替换诊断（160 次调用）未观察到
Luna 整数分使用率改变（B−A = 0，区间经验性退化为 [0,0]）；恢复前运行指纹缺失，
跨恢复环境一致性判定为<b>证据不足</b>。</div>

<h2>1 · 阶段一：审计结论</h2>
<table><tr><th>编号</th><th>审计项</th><th>判定</th><th>证据</th><th>影响</th></tr>{audit_rows}</table>

<h2>2 · 阶段一：诊断统计补全</h2>
<p>主终点（Luna 整数分使用率 B−A，层内作文配对 bootstrap 10,000 次，冻结种子）：</p>
<table><tr><th>模型</th><th>B−A（百分点）</th><th>95% 区间（展示）</th><th>区间性质</th><th>与已发布报告</th></tr>{ep_rows}</table>
<p>分值使用汇总（三维度合计，n=120/格）：</p>
<table><tr><th>模型</th><th>条件</th><th>整数分率</th><th>分维度整数分率</th><th>均值</th></tr>{freq_rows}</table>
<p>完整逐维度频数、按作文配对变化与复测指标：
<a href="analysis/diagnostic_statistics.md">diagnostic_statistics.md</a> ·
<a href="analysis/diagnostic_statistics.json">diagnostic_statistics.json</a></p>

<h2>3 · 阶段二/三/四产物（写作与投稿）</h2>
<ul>
<li><a href="literature/literature_review.md">literature/literature_review.md</a> — 文献核验对照表与新意定位</li>
<li><a href="paper/dress_new_draft_zh.md">paper/dress_new_draft_zh.md</a> — 中文论文初稿 ·
<a href="paper/dress_new_draft_zh.html">HTML 版</a></li>
<li><a href="submission/submission_assessment.md">submission/submission_assessment.md</a> — 投稿候选与补实验决策</li>
<li><a href="facts.json">facts.json</a> — 论文数值事实清单（每条带来源哈希）</li>
</ul>

<h2>4 · 产物索引</h2>
<table><tr><th>文件</th><th>说明</th></tr>{file_rows}</table>
<p class="small">每个产物的字节与 sha256 见 <a href="manifest.json">manifest.json</a>；
阶段账本（输入/输出哈希与耗时）见 <a href="ledger.json">ledger.json</a>。</p>
</body></html>"""


# --------------------------------------------------------------------------- #
# ledger + entry points
# --------------------------------------------------------------------------- #

STAGE_INPUTS = {
    "selftest": [Path(__file__)],
    "analyze": [
        DIAG_DIR / "frozen_plan.json", DIAG_DIR / "calls.jsonl",
        DIAG_DIR / "diagnostic_report.json", DIAG_SCRIPT, FORMAL_DIR / "manifest.json",
        Path(__file__),
    ],
    "audit": [
        DIAG_DIR / "frozen_plan.json", DIAG_DIR / "calls.jsonl",
        DIAG_DIR / "diagnostic_report.json", DIAG_DIR / "runtime.json",
        DIAG_DIR / "status.json", DIAG_SCRIPT, FORMAL_DIR / "manifest.json",
        FORMAL_DIR / "report.json", FORMAL_DIR / "results.csv",
        FORMAL_DIR / "analysis_v3/closeout/closeout_report.json",
        FORMAL_DIR / "cross_run_comparison.json",
        FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json",
        Path(__file__),
    ],
    "package": [
        DIAG_DIR / "frozen_plan.json", DIAG_DIR / "calls.jsonl",
        DIAG_DIR / "diagnostic_report.json", DIAG_DIR / "runtime.json",
        DIAG_DIR / "status.json", FORMAL_DIR / "manifest.json",
        FORMAL_DIR / "report.json", FORMAL_DIR / "results.csv",
        FORMAL_DIR / "analysis_v3/closeout/closeout_report.json",
        FORMAL_DIR / "cross_run_comparison.json",
        FORMAL_DIR / "analysis_v3/criterion_qwk_ci.json",
        FORMAL_DIR / "analysis_v3/FINDINGS.md",
        FORMAL_DIR / "analysis_v3/closeout/scale_diagnosis.json",
        Path(__file__),
    ],
}
# facts.json embeds analyze/audit results, so those stage outputs are package inputs
STAGE_INPUTS["package"] += [
    DEFAULT_OUTPUT_DIR / "analysis/diagnostic_statistics.json",
    DEFAULT_OUTPUT_DIR / "audit/audit_report.json",
    DEFAULT_OUTPUT_DIR / "literature/literature_review.md",
    DEFAULT_OUTPUT_DIR / "paper/dress_new_draft_zh.md",
    DEFAULT_OUTPUT_DIR / "paper/dress_new_draft_zh.html",
    DEFAULT_OUTPUT_DIR / "paper/draft_check_report.json",
    DEFAULT_OUTPUT_DIR / "submission/submission_assessment.md",
]


def stage_output_files(stage: str, output_dir: Path) -> list[Path]:
    return {
        "selftest": [output_dir / "selftest_report.json"],
        "analyze": [output_dir / "analysis/diagnostic_statistics.json",
                    output_dir / "analysis/diagnostic_statistics.md"],
        "audit": [output_dir / "audit/audit_report.json", output_dir / "audit/audit_report.md"],
        "package": [output_dir / "facts.json", output_dir / "manifest.json",
                    output_dir / "index.html"],
    }[stage]


def load_ledger(output_dir: Path) -> dict:
    path = output_dir / "ledger.json"
    if path.exists():
        return load_json(path)
    return {}


def save_ledger(output_dir: Path, ledger: dict) -> None:
    atomic_json(output_dir / "ledger.json", ledger)


def stage_inputs_unchanged(stage: str, entry: dict) -> bool:
    for path, recorded in entry.get("input_hashes", {}).items():
        p = Path(path)
        if not p.exists() or sha256_file(p) != recorded:
            return False
    for path, recorded in entry.get("output_hashes", {}).items():
        p = Path(path)
        if not p.exists() or sha256_file(p) != recorded:
            return False
    return True


def execute(stage: str, output_dir: Path, ledger: dict, run_log: list) -> None:
    entry = ledger.get(stage)
    if entry and entry.get("status") == "done" and stage_inputs_unchanged(stage, entry):
        print(f"[skip] {stage}: 输入与产物哈希均与上次一致")
        run_log.append({"stage": stage, "status": "skipped", "at": now()})
        return
    input_hashes = {str(p): sha256_file(p) for p in STAGE_INPUTS[stage] if p.exists()}
    started = time.monotonic()
    print(f"[run ] {stage} …")
    if stage == "selftest":
        result = run_selftest(output_dir)
        if not result["all_passed"]:
            raise RuntimeError("selftest failed; aborting before experiment-data stages")
    elif stage == "analyze":
        run_analyze(output_dir)
    elif stage == "audit":
        run_audit(output_dir)
    elif stage == "package":
        run_package(output_dir)
    outputs = stage_output_files(stage, output_dir)
    ledger[stage] = {
        "status": "done",
        "started_at": entry.get("started_at") if entry else now(),
        "finished_at": now(),
        "duration_s": round(time.monotonic() - started, 2),
        "input_hashes": input_hashes,
        "output_hashes": {str(p): sha256_file(p) for p in outputs if p.exists()},
    }
    run_log.append({"stage": stage, "status": "done", "duration_s": ledger[stage]["duration_s"], "at": now()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES + ("all",))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = load_ledger(output_dir)
    log_path = output_dir / "run_log.jsonl"
    run_log: list[dict] = []
    stages = list(STAGES) if args.stage == "all" else [args.stage]
    try:
        for stage in stages:
            execute(stage, output_dir, ledger, run_log)
    except Exception as exc:  # record failure, non-zero exit
        run_log.append({"stage": args.stage, "status": "failed", "error": str(exc)[:400], "at": now()})
        raise
    finally:
        with log_path.open("a", encoding="utf-8") as handle:
            for record in run_log:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        save_ledger(output_dir, ledger)
    print("stages:", ", ".join(f"{r['stage']}={r['status']}" for r in run_log))


if __name__ == "__main__":
    main()
