"""Summarize collected, externally graded records; never grade proofs or run models."""

import argparse
import json
import math
import statistics
from pathlib import Path

VERDICTS = ("correct", "incorrect", "uncertain", "ungraded")


def nonnegative(value, name, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
            or (integer and type(value) is not int)):
        raise ValueError(f"Invalid {name}")
    return value


def summarize_run(record, plan, deadline):
    if record.get("run_id") != plan["run_id"]:
        raise ValueError("Run identity mismatch")
    workers = record.get("workers", [])
    expected = {f"agent-{i}" for i in range(1, plan["worker_count"] + 1)}
    if len(workers) != len(expected) or {w.get("worker_id") for w in workers} != expected:
        raise ValueError("Record every expected worker, including failed workers")
    finals = []
    totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0}
    for worker in workers:
        status = worker.get("status")
        if status not in ("submitted", "timeout", "error"):
            raise ValueError("Invalid worker status")
        verdict = worker.get("verdict", "ungraded")
        if verdict not in VERDICTS:
            raise ValueError("Invalid verdict")
        elapsed = worker.get("final_elapsed_seconds")
        if status == "submitted":
            nonnegative(elapsed, "submission time")
            if elapsed <= deadline:
                finals.append((elapsed, worker["worker_id"], verdict))
        elif elapsed is not None or verdict != "ungraded":
            raise ValueError("A worker without a final submission cannot have a time or verdict")
        for key in totals:
            value = worker.get(key)
            if value is None:
                totals[key] = None
            else:
                nonnegative(value, key, integer=key.endswith("tokens"))
                if totals[key] is not None:
                    totals[key] += value
    # Equal timestamps are resolved by worker ID, never correctness.
    finals.sort()
    first = finals[0] if finals else None
    outcome = ("no_timely_submission" if first is None else first[2])
    success = {"correct": True, "incorrect": False, "no_timely_submission": False}.get(outcome)
    known_correct = [t for t, _, verdict in finals if verdict == "correct"]
    unresolved = [t for t, _, verdict in finals if verdict in ("uncertain", "ungraded")]
    oracle = min(known_correct) if known_correct else None
    oracle_resolved = not unresolved or (oracle is not None and min(unresolved) >= oracle)
    resolved = all(v not in ('uncertain', 'ungraded') for _, _, v in finals)
    submission_accuracy = sum(v == 'correct' for _, _, v in finals) / len(expected) if resolved else None
    return {
        "submission_accuracy": submission_accuracy,
        "submissions": [{"worker_id": w['worker_id'], "status": w['status'],
                         "verdict": w.get('verdict', 'ungraded'),
                         "elapsed_seconds": w.get('final_elapsed_seconds'),
                         "native_latency_seconds": w.get('native_latency_seconds')} for w in workers],
        "trial_completion_seconds": max(t for t, _, _ in finals) if len(finals) == len(expected) else None,
        "censored_at_deadline": any(w['status'] == 'timeout' for w in workers),
        "usage_per_submission": {k: v / len(expected) if v is not None else None for k, v in totals.items()},
        "run_id": plan["run_id"], "phase": plan["phase"], "condition": plan["condition"],
        "infrastructure_error": record.get("infrastructure_error"),
        "worker_errors": sum(w["status"] == "error" for w in workers),
        "primary_worker": first[1] if first else None,
        "primary_outcome": outcome, "primary_success": success,
        "primary_submission_seconds": first[0] if first else None,
        "time_to_first_correct_seconds_oracle": oracle if oracle_resolved else None,
        "oracle_resolved": oracle_resolved,
        "deadline_seconds": deadline,
        "total_usage_all_workers": totals,
    }


def summarize(manifest, records):
    rows = manifest["runs"]
    run_ids = {row["run_id"] for row in rows}
    if len(run_ids) != len(rows):
        raise ValueError("Duplicate run IDs in manifest")
    collected = {}
    for record in records:
        key = record.get("run_id")
        if key not in run_ids or key in collected:
            raise ValueError("Unknown or duplicate collected run ID")
        collected[key] = record
    deadline = nonnegative(manifest["configuration"]["deadline_seconds"], "deadline")
    summaries = [summarize_run(collected[row["run_id"]], row, deadline)
                 for row in rows if row["run_id"] in collected]
    groups = []
    for phase, condition in sorted({(r["phase"], r["condition"]) for r in rows}):
        planned = [r for r in rows if (r["phase"], r["condition"]) == (phase, condition)]
        available = [r for r in summaries if (r["phase"], r["condition"]) == (phase, condition)]
        correct = sum(r["primary_success"] is True for r in available)
        unresolved = sum(r["primary_success"] is None for r in available)
        missing = len(planned) - len(available)
        accuracies = [r['submission_accuracy'] for r in available]
        latencies = [r['trial_completion_seconds'] for r in available]
        def stats(values):
            if missing or any(v is None for v in values) or not values:
                return None
            return {'mean': statistics.mean(values), 'sample_std': statistics.stdev(values) if len(values) > 1 else None}
        groups.append({
            'submission_accuracy_across_runs': stats(accuracies),
            'trial_completion_seconds_across_runs': stats(latencies),
            "phase": phase, "condition": condition, "planned": len(planned),
            "collected": len(available), "missing": missing,
            "correct": correct, "incorrect_or_no_submission": sum(r["primary_success"] is False for r in available),
            "unresolved": unresolved,
            "primary_success_fraction": None if missing or unresolved else correct / len(planned),
            "infrastructure_error_runs": sum(bool(r["infrastructure_error"]) or r["worker_errors"] > 0 for r in available),
        })
    return {"schema_version": 1, "groups": groups, "runs": summaries,
            "missing_run_ids": sorted(run_ids - collected.keys()),
            "primary_metric": "per_submission_accuracy",
            "note": "Legacy primary_* fields describe secondary first-final selection. Trial latency is controller release to last root final, not exact API-to-token latency; native task durations are also preserved. Oracle timing uses retrospective grading, not a deployable answer selector. No proof is graded by this script."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True, help="JSON array of normalized run records")
    args = parser.parse_args()
    try:
        report = summarize(json.loads(args.manifest.read_text()), json.loads(args.records.read_text()))
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(1, f"summarize: {error}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
