"""Offline preparation only: no Docker, credentials, network, or model calls."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from experiments.math_harness.profiles import (
    HERE, ROOT, EXPERIMENTS, DEFAULT_EXPERIMENT, profile_dir, source_files,
)
CONDITIONS = ("single", "orchestrator", "delm")
TOOL_POLICIES = {
    "scratch-python": (
        "You may use local Python and local files to explore examples or check "
        "calculations. Internet access and external mathematical services are forbidden. "
        "The required final deliverable is a mathematical proof, not a program."
    ),
    "reasoning-only": (
        "Do not use Python, a CAS, or other computational tools for mathematical "
        "exploration. Local file I/O and the supplied DeLM board commands, when "
        "present, are allowed solely for writing and communicating proof text."
    ),
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def settings(model=None, tool_policy=None, *, experiment=DEFAULT_EXPERIMENT):
    config = json.loads((profile_dir(experiment) / "pilot.json").read_text())
    config["experiment"] = experiment
    if model is not None:
        if not model.strip():
            raise ValueError("Model must be nonempty")
        config["model"] = model
    if tool_policy is not None:
        config["tool_policy"] = tool_policy
    validate_config(config)
    return config


def validate_config(config):
    name = profile_dir(config).name
    if config["tool_policy"] not in TOOL_POLICIES:
        raise ValueError("Unknown tool policy")
    if config["conditions"] != list(CONDITIONS):
        raise ValueError("Expected the three registered conditions")
    if not re.fullmatch(r"[a-z0-9_]+", config["problem_id"]):
        raise ValueError("Invalid problem ID")
    peers = config.get("delm_worker_count", 2)
    allowed = (2,) if name == "math_pilot" else (2, 4)
    if type(peers) is not int or peers not in allowed:
        raise ValueError(f"DeLM worker count for {name} must be one of {allowed}")
    selected = config.get("selected_conditions", list(CONDITIONS))
    if not isinstance(selected, (list, tuple)) or not selected or len(set(selected)) != len(selected) or any(c not in CONDITIONS for c in selected):
        raise ValueError("Select distinct registered conditions")
    for key in ("deadline_seconds", "calibration_repeats", "measured_repeats"):
        if type(config[key]) is not int or config[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")


def schedule(config, phase):
    validate_config(config)
    rows = []
    repeats = 1 if phase == "smoke" else (config["calibration_repeats"] if phase == "calibration" else config["measured_repeats"])
    for repeat in range(1, repeats + 1):
        shift = (repeat - 1) % len(CONDITIONS)
        order = ("single",) if phase == "calibration" else CONDITIONS[shift:] + CONDITIONS[:shift]
        for condition in order:
            if phase != "calibration" and condition not in config.get("selected_conditions", CONDITIONS):
                continue
            rows.append({
                "run_id": f"{phase}-{config['problem_id']}-{condition}-r{repeat}",
                "problem_id": config["problem_id"], "phase": phase,
                "repeat": repeat, "condition": condition,
                "worker_count": config.get("delm_worker_count", 2) if condition == "delm" else 1,
                "board_enabled": condition == "delm",
            })
    return rows


def worker_prompt(config, condition, actor):
    problem = (profile_dir(config) / "problems" / f"{config['problem_id']}.txt").read_text()
    common = (profile_dir(config) / "prompts/common.txt").read_text().format(
        problem=problem, tool_policy=TOOL_POLICIES[config["tool_policy"]],
        agent_policy="" if condition == "orchestrator" else "Do not create additional agents.")
    # Reuse the paper's native-subagent instruction without a hand-written decomposition.
    if condition == "orchestrator":
        return common + "\nUse Codex’s native subagents to help solve this task.\n"
    if condition == "delm":
        return common + "\n" + (profile_dir(config) / "prompts/delm.txt").read_text().format(actor=actor, peer_count=config.get("delm_worker_count", 2),
            roster=", ".join(f"agent-{i}" for i in range(1, config.get("delm_worker_count", 2)+1)))
    if condition not in CONDITIONS:
        raise ValueError("Unknown condition")
    return common


def provenance(config):
    result = {"files": {str(path.relative_to(ROOT)): digest(path.read_bytes())
                        for path in source_files(config)}}
    try:
        result["repository_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        result["repository_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True))
    except (OSError, subprocess.CalledProcessError):
        result["repository_commit"] = None
        result["repository_dirty"] = None
    return result


def prepare(output, config, phase="calibration"):
    phases = ("smoke", "calibration", "measured")
    if profile_dir(config).name in ("math_pilot_matroid_v1", "math_pilot_matching_v1"):
        phases += ("exploratory",)
    if phase not in phases:
        raise ValueError("Unknown phase")
    config = dict(config)
    if phase == "smoke":
        config.update(problem_id="arithmetic_smoke", deadline_seconds=180, selection_status="infrastructure_smoke_only")
    output = Path(output).resolve()
    if output == HERE.parent or HERE.parent in output.parents:
        raise ValueError("Write bundles outside the source package, e.g. .local/math-pilot/")
    if output.exists():
        raise ValueError("Output already exists; use a new directory rather than overwrite a plan")
    rows = schedule(config, phase)
    # Resolve and validate all inputs before creating any output.
    problem = (profile_dir(config) / "problems" / f"{config['problem_id']}.txt").read_text()
    prompts = {condition: [worker_prompt(config, condition, f"agent-{i}")
                          for i in range(1, (config.get("delm_worker_count", 2) if condition == "delm" else 1) + 1)]
               for condition in CONDITIONS}
    manifest = {
        "schema_version": 1, "status": "prepared_not_executed",
        "configuration": config, "provenance": provenance(config), "runs": rows,
        "ready_for_live_execution": False,
        "live_requirements": [
            "Run the Linux adapter smoke tests before collecting benchmark data.",
            "Verify isolated workspaces, no solution lookup, and identical tool policies.",
            "Validate provider authentication and selected model without silent fallback.",
            "Capture first final-response events and usage on a shared monotonic clock.",
            ("This exploratory problem was selected by judgment without baseline calibration."
             if phase == "exploratory" else
             "Run baseline calibration before accepting this problem for measurement."),
            "Keep grading offline and reference material outside worker-accessible mounts."
        ],
        "worker_files": {},
    }
    output.mkdir(parents=True, exist_ok=False)
    for run in rows:
        for index, prompt in enumerate(prompts[run["condition"]], 1):
            folder = output / run["run_id"] / f"agent-{index}"
            folder.mkdir(parents=True)
            for name, body in (("PROBLEM.txt", problem), ("PROMPT.txt", prompt)):
                path = folder / name
                path.write_text(body)
                manifest["worker_files"][str(path.relative_to(output))] = digest(path.read_bytes())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=EXPERIMENTS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("smoke", "calibration", "measured", "exploratory"), default="calibration")
    parser.add_argument("--model", help="Explicit override; default is the research repo's Codex model")
    parser.add_argument("--tool-policy", choices=tuple(TOOL_POLICIES))
    parser.add_argument("--delm-only", action="store_true")
    parser.add_argument("--peers", type=int, choices=(2,4), help="Override the selected profile's peer count")
    args = parser.parse_args()
    try:
        config = settings(args.model, args.tool_policy, experiment=args.experiment)
        if args.peers is not None:
            config["delm_worker_count"] = args.peers
        if args.delm_only:
            config["selected_conditions"] = ["delm"]
        manifest = prepare(args.output, config, args.phase)
    except (OSError, ValueError) as error:
        parser.exit(1, f"prepare: {error}\n")
    print(json.dumps({"status": manifest["status"], "runs": len(manifest["runs"]),
                      "manifest": str(args.output.resolve() / "manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
