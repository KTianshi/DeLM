"""Run one math pilot trial on Linux Docker Engine; grading remains offline."""

import argparse
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

from experiments.math_harness.profiles import HERE, ROOT, source_files

from board.protected_board import ProtectedBoard
from runtime.containers import handler
from experiments.math_harness.native_records import collect_tree
from runtime.machine_lock import machine_lock
from runtime.runtime_support import validate_key_file
from runtime.config import CLI_VERSIONS, DRIVER_REVISION
from experiments.math_harness.egress import proxy
from experiments.math_harness.prepare import provenance, validate_config


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60, **kwargs).stdout.strip()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def submission_status(release_ns, deadline_ns, observed, raw, forced_stop=False):
    if release_ns is not None and observed is not None:
        return "submitted" if release_ns <= observed <= deadline_ns else "timeout"
    return "timeout" if raw.get("timed_out") or forced_stop else "error"


def record_usage(row, usage, raw):
    # Native token counts may cover only completed responses when a turn is cut off.
    row["observed_usage"] = usage
    complete = raw.get("completed") and not raw.get("timed_out") and not raw.get("error")
    row["usage_coverage"] = "complete" if complete else "partial_or_unknown"
    if complete:
        row.update(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                   cached_input_tokens=usage.get("cached_input_tokens"))


def checked_bundle(bundle, run_id):
    bundle = bundle.resolve()
    manifest = json.loads((bundle / "manifest.json").read_text())
    config = manifest["configuration"]
    if "experiment" not in config:
        raise ValueError("Legacy bundle: use its archived adapter or prepare a new shared-harness bundle")
    validate_config(config)
    if manifest["provenance"]["files"] != provenance(config)["files"]:
        raise ValueError("Adapter source changed after preparation; prepare a new frozen bundle")
    if config["auth_mode"] != "coding-plan" or config["harness"] != "codex":
        raise ValueError("This pilot supports Codex coding-plan authentication only")
    if config["tool_policy"] != "scratch-python":
        raise ValueError("Live enforcement for reasoning-only tools is not implemented")
    if config["effort"] != "xhigh":
        raise ValueError("Keep the registered xhigh effort")
    rows = [row for row in manifest["runs"] if row["run_id"] == run_id]
    if len(rows) != 1:
        raise ValueError("Choose exactly one registered run ID")
    row = rows[0]
    if config.get("delm_worker_count", 2) not in (2, 4):
        raise ValueError("DeLM worker count must be 2 or 4")
    expected_count = {"single": 1, "independent": 2, "orchestrator": 1, "delm": config.get("delm_worker_count", 2)}[row["condition"]]
    if row["worker_count"] != expected_count or row["board_enabled"] != (row["condition"] == "delm"):
        raise ValueError("Inconsistent condition/worker/board settings")
    files = []
    for index in range(1, expected_count + 1):
        worker_files = {}
        for name in ("PROBLEM.txt", "PROMPT.txt"):
            relative = Path(row["run_id"]) / f"agent-{index}" / name
            path = bundle / relative
            if path.is_symlink() or not path.resolve().is_relative_to(bundle):
                raise ValueError("Worker input must be a regular file within the bundle")
            if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["worker_files"][relative.as_posix()]:
                raise ValueError(f"Worker input changed after preparation: {relative}")
            worker_files[name] = path
        files.append(worker_files)
    return manifest, row, files


def watch(process, log_path, events, context, actor):
    with log_path.open("x") as output:
        for line in process.stdout:
            observed = time.monotonic_ns()
            output.write(line)
            output.flush()
            try:
                event = json.loads(line).get("event")
            except (ValueError, AttributeError):
                continue
            if event in {"ready", "model_start", "result", "deadline", "error"}:
                events.setdefault(event, observed)
                if context is not None and event == "model_start":
                    context.session_state(actor, "running")
    process.wait()
    events["ended"] = time.monotonic_ns()
    if context is not None:
        context.session_state(actor, "ended")


def verify_network(container, gateway, proxy_port, board_port):
    # These are transport checks, not model calls or mathematical exploration.
    script = f'''
import http.client, socket
from pathlib import Path
assert not Path('/tests').exists() and not Path('/solution').exists()
assert not Path('/workspace/grader').exists()
connection = http.client.HTTPConnection({gateway!r}, {proxy_port}, timeout=5)
connection.request('CONNECT', 'example.org:443')
assert connection.getresponse().status == 403
connection.close()
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    pass
else:
    connection.close()
    raise AssertionError('Worker has direct internet access')
assert Path('/tools/context.sh').exists() == {board_port is not None!r}
print('isolated worker and restricted egress verified')
'''
    return command(["docker", "exec", container, "/usr/local/bin/python3", "-c", script])


@machine_lock()
def run(bundle, run_id, output, auth_file, image, *, allow_measured=False):
    manifest, plan, inputs = checked_bundle(bundle, run_id)
    config = manifest["configuration"]
    if plan["phase"] == "measured" and not allow_measured:
        raise ValueError("Measured runs require --calibration-approved after documented preliminary proof review")
    if not Path("/proc/meminfo").exists():
        raise ValueError("Use the Linux execution host")
    validate_key_file(auth_file)
    if command(["docker", "ps", "-q"]):
        raise ValueError("Use an idle Docker host; running containers were found")
    info = json.loads(command(["docker", "info", "--format", "{{json .}} "]))
    if info["NCPU"] < 2 * plan["worker_count"] or info["MemTotal"] < (4 * plan["worker_count"] + 4) * 1024**3:
        raise ValueError("Need two CPUs and 4 GiB per worker plus 4 GiB for the host")
    image_info = json.loads(command(["docker", "image", "inspect", image]))[0]
    if command(["docker", "run", "--rm", image_info["Id"], "/opt/agent-bin/codex", "--version"]) != f"codex-cli {CLI_VERSIONS['codex']}":
        raise ValueError("Image does not have the pinned Codex version")
    output = output.resolve()
    if output.exists():
        raise ValueError("Run output exists; never overwrite or implicitly retry")
    output.mkdir(parents=True, mode=0o700)
    source = output / "framework-source"
    # This snapshot is controller-only and never mounted into any worker.
    for path in source_files(config):
        target = source / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    metadata = {"schema_version": 1, "run_id": run_id, "plan": plan, "configuration": config,
                "status": "setting_up", "provenance": provenance(config), "image_id": image_info["Id"],
                "cli_version": CLI_VERSIONS["codex"], "driver_revision": DRIVER_REVISION,
                "docker_version": info["ServerVersion"], "cpus": info["NCPU"], "memory_bytes": info["MemTotal"],
                "per_worker_cpus": 2, "per_worker_memory_mib": 4096,
                "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "workers": [], "infrastructure_error": None}
    save = lambda: write_json(output / "run.json", metadata)
    save()
    containers, networks, servers, workers = [], [], [], []
    context = None
    release_ns = deadline_ns = None
    try:
        roster = [f"agent-{i}" for i in range(1, plan["worker_count"] + 1)]
        if plan["board_enabled"]:
            original_problem = inputs[0]["PROBLEM.txt"].read_bytes()
            context = ProtectedBoard(output / "context.sqlite", roster,
                                     lambda path: original_problem if path == "PROBLEM.txt" else None)
        for actor, worker_input in zip(roster, inputs):
            folder = output / actor
            folder.mkdir()
            shutil.copyfile(worker_input["PROMPT.txt"], folder / "prompt.txt")
            (folder / "context-command-errors.jsonl").touch()
            network = "delm-math-" + secrets.token_hex(6)
            command(["docker", "network", "create", "--internal", "--label", "delm.math=true", network])
            networks.append(network)
            network_info = json.loads(command(["docker", "network", "inspect", network]))[0]
            gateway = network_info["IPAM"]["Config"][0]["Gateway"]
            egress = proxy(gateway)
            servers.append(egress)
            threading.Thread(target=egress.serve_forever, daemon=True).start()
            board_port = None
            env = {"WORKER_NAME": actor, "TASK_WORKSPACE": "/workspace", "AGENT_MODEL": config["model"],
                   "AGENT_EFFORT": config["effort"], "MATH_CONDITION": plan["condition"], "AGENT_AUTHENTICATION": "chatgpt",
                   "HTTPS_PROXY": f"http://{gateway}:{egress.server_port}",
                   "HTTP_PROXY": f"http://{gateway}:{egress.server_port}", "NO_PROXY": f"{gateway},localhost,127.0.0.1",
                   "PATH": "/opt/agent-bin:/usr/local/bin:/usr/bin:/bin"}
            if context is not None:
                token = secrets.token_hex(16)
                board = ThreadingHTTPServer((gateway, 0), handler(context, {token: actor}))
                servers.append(board)
                threading.Thread(target=board.serve_forever, daemon=True).start()
                board_port = board.server_port
                env.update(CONTEXT_URL=f"http://{gateway}:{board_port}", CONTEXT_TOKEN=token,
                           CONTEXT_PYTHON="/opt/native/bin/python3.12", CONTEXT_LIBRARY_PATH="/opt/native/lib")
            mounts = [
                (ROOT / "runtime/__init__.py", "/tools/runtime/__init__.py"),
                (ROOT / "runtime/config.py", "/tools/runtime/config.py"),
                (ROOT / "runtime/native_worker.py", "/tools/runtime/native_worker.py"),
                (ROOT / "runtime/codex_api.py", "/tools/worker.py"),
                (HERE / "worker.py", "/tools/native_worker.py"),
                (auth_file, "/run/secrets/codex_auth.json"),
            ]
            if context is not None:
                mounts += [(ROOT / "board/context_client.py", "/tools/context_client.py"),
                           (ROOT / "board/context.sh", "/tools/context.sh")]
            name = "delm-math-worker-" + secrets.token_hex(6)
            containers.append(name)
            args = ["docker", "run", "-d", "--name", name, "--network", network,
                    "--label", "delm.math=true", "--cpus", "2", "--memory", "4096m", "--pids-limit", "512",
                    "--mount", f"type=bind,src={folder},dst=/records"]
            for path, target in mounts:
                args += ["--mount", f"type=bind,src={path.resolve()},dst={target},readonly"]
            for key, value in env.items():
                args += ["-e", f"{key}={value}"]
            command(args + [image_info["Id"], "sleep", "infinity"])
            command(["docker", "exec", name, "mkdir", "-p", "/workspace"])
            command(["docker", "cp", str(worker_input["PROBLEM.txt"]), f"{name}:/workspace/PROBLEM.txt"])
            network_check = verify_network(name, gateway, egress.server_port, board_port)
            worker = {"actor": actor, "name": name, "folder": folder, "events": {}, "process": None,
                      "network_check": network_check}
            workers.append(worker)
        metadata["status"] = "preparing_sessions"
        save()
        for worker in workers:
            process = subprocess.Popen(["docker", "exec", "-e", "LD_LIBRARY_PATH=/opt/native/lib", worker["name"],
                                        "/opt/native/bin/python3.12", "/tools/worker.py"],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            worker["process"] = process
            worker["watcher"] = threading.Thread(target=watch, args=(process, worker["folder"] / "process.log",
                                                worker["events"], context, worker["actor"]), daemon=True)
            worker["watcher"].start()
        ready_limit = time.monotonic() + 90
        while not all("ready" in worker["events"] for worker in workers):
            if any(worker["process"].poll() is not None for worker in workers) or time.monotonic() >= ready_limit:
                raise RuntimeError("Worker preparation failed; inspect per-worker result/process logs. No task released.")
            time.sleep(0.1)
        release_ns = time.monotonic_ns()
        deadline_ns = release_ns + config["deadline_seconds"] * 10**9
        for worker in workers:
            write_json(worker["folder"] / "release.json", {"release_ns": release_ns, "deadline_ns": deadline_ns})
        metadata.update(status="running", release_ns=release_ns, deadline_ns=deadline_ns)
        save()
        print(json.dumps({"status": "running", "run_id": run_id, "output": str(output)}), flush=True)
        while any(worker["process"].poll() is None for worker in workers):
            if time.monotonic_ns() > deadline_ns + 30 * 10**9:
                for worker in workers:
                    if worker["process"].poll() is None:
                        command(["docker", "stop", "-t", "2", worker["name"]])
                        worker["forced_stop"] = True
                break
            time.sleep(0.25)
        metadata["status"] = "collected_ungraded"
    except Exception as error:
        metadata.update(status="infrastructure_error", infrastructure_error=f"{type(error).__name__}: {error}")
    finally:
        for worker in workers:
            process = worker["process"]
            if process is not None and process.poll() is None:
                subprocess.run(["docker", "stop", "-t", "2", worker["name"]], capture_output=True, timeout=30)
            if "watcher" in worker:
                worker["watcher"].join(timeout=15)
            raw_path = worker["folder"] / "result.json"
            try:
                raw = json.loads(raw_path.read_text()) if raw_path.exists() else {}
            except (OSError, ValueError):
                raw = {}
            observed = worker["events"].get("result")
            status = submission_status(release_ns, deadline_ns, observed, raw, worker.get("forced_stop", False))
            timely = status == "submitted"
            row = {"worker_id": worker["actor"], "status": status, "verdict": "ungraded",
                   "final_elapsed_seconds": (observed - release_ns) / 1e9 if timely else None,
                   "input_tokens": None, "output_tokens": None, "cost_usd": None,
                   "events_monotonic_ns": worker["events"], "network_check": worker["network_check"],
                   "error": raw.get("error"), "shutdown_error": raw.get("shutdown_error")}
            if observed is not None and release_ns is not None:
                row["observed_final_seconds_including_late"] = (observed - release_ns) / 1e9
            try:
                tree = collect_tree(worker["name"], worker["folder"], config["model"],
                                    plan["condition"] == "orchestrator")
                row["native_tree"] = tree
                row["session_id"] = tree["root_session_id"]
                row["native_latency_seconds"] = tree["root_latency_seconds"]
                record_usage(row, tree["usage"], {**raw, "completed": raw.get("completed") and tree["usage_complete"]})
            except Exception as error:
                row["usage_collection_error"] = f"{type(error).__name__}: {error}"
            # Docker produces an archive; preserve it without extracting untrusted paths.
            with (worker["folder"] / "workspace.tar").open("wb") as archive:
                copied = subprocess.run(["docker", "cp", f"{worker['name']}:/workspace/.", "-"],
                                        stdout=archive, stderr=subprocess.PIPE, timeout=60)
                if copied.returncode:
                    row["workspace_collection_error"] = copied.stderr.decode(errors="replace")
            metadata["workers"].append(row)
        present = {row["worker_id"] for row in metadata["workers"]}
        for index in range(1, plan["worker_count"] + 1):
            if f"agent-{index}" not in present:
                metadata["workers"].append({"worker_id": f"agent-{index}", "status": "error", "verdict": "ungraded",
                                            "final_elapsed_seconds": None, "input_tokens": None,
                                            "output_tokens": None, "cost_usd": None})
        save()
        write_json(output / "normalized.json", {"run_id": run_id, "infrastructure_error": metadata["infrastructure_error"],
                                                "workers": metadata["workers"]})
        for server in servers:
            if hasattr(server, "stopping"):
                server.stopping.set()
            server.shutdown()
            server.server_close()
        for name in containers:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        for network in networks:
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    parser.add_argument("--image", default="delm-math-codex:setup")
    parser.add_argument("--calibration-approved", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.bundle, args.run_id, args.output, args.auth_file.resolve(), args.image,
                     allow_measured=args.calibration_approved)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"math-run: {error}\n")
    print(json.dumps({"status": result["status"], "run_id": result["run_id"],
                      "output": str(args.output.resolve()), "error": result["infrastructure_error"]}, indent=2))
    return int(result["status"] == "infrastructure_error")


if __name__ == "__main__":
    raise SystemExit(main())
