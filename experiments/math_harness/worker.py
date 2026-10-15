"""Math worker: upstream Humanize/Codex driver with synchronized release/deadline."""

import dataclasses
import json
import os
from pathlib import Path
import signal
import time

from runtime.native_worker import AgentDeadline, check_skills, model_settings, require_account


def emit(event):
    print(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns()}), flush=True)


def main():
    from hmz.agents import CodexAgent, CodexAgentConfig
    os.environ.pop("LD_LIBRARY_PATH", None)
    config = CodexAgentConfig(**model_settings(os.environ), web_search=False)
    native_agents = os.environ.get("MATH_CONDITION") == "orchestrator"
    config_path = Path("/root/.codex/config.toml")
    with config_path.open("a") as output:
        output.write("\n[agents]\nenabled = " + str(native_agents).lower() + "\n")
        output.write("default_subagent_model = " + json.dumps(config.model) + "\n")
        output.write("default_subagent_reasoning_effort = " + json.dumps(config.effort) + "\n")
    result = {"model": config.model, "effort": config.effort, "completed": False,
              "native_agents_enabled": native_agents}
    agent = None
    try:
        agent = CodexAgent(config, name=os.environ["WORKER_NAME"])
        effective = agent.server.call("config/read", {"includeLayers": False})["config"]["agents"]
        Path("/records/agents-config.json").write_text(json.dumps(effective, indent=2))
        if (effective["enabled"] != native_agents
                or effective["default_subagent_model"] != config.model
                or effective["default_subagent_reasoning_effort"] != config.effort):
            raise RuntimeError("Native subagent configuration mismatch")
        result["authentication"] = require_account(agent.server, "chatgpt")
        inventory = agent.server.call("skills/list", {"cwds": ["/workspace"], "forceReload": True})
        check_skills(inventory, "/workspace")
        Path("/records/skills.json").write_text(json.dumps(inventory, indent=2))
        models = agent.server.call("model/list", {})
        Path("/records/models.json").write_text(json.dumps(models, indent=2))
        available = {row.get("model") for row in models.get("data", [])} | {row.get("id") for row in models.get("data", [])}
        if config.model not in available:
            raise RuntimeError(f"Configured model {config.model} not in available model list; no fallback")
        session = agent.new(cwd="/workspace")
        prompt = Path("/records/prompt.txt").read_text()
        emit("ready")
        release = Path("/records/release.json")
        preparation_deadline = time.monotonic() + 120
        while not release.exists():
            if time.monotonic() > preparation_deadline:
                raise RuntimeError("Controller did not release the prepared worker")
            time.sleep(0.02)
        control = json.loads(release.read_text())
        result["release_ns"] = control["release_ns"]
        remaining = (control["deadline_ns"] - time.monotonic_ns()) / 1e9
        if remaining <= 0:
            raise AgentDeadline("Common deadline already elapsed")
        def expired(*_):
            raise AgentDeadline("Common experiment deadline reached")
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        emit("model_start")
        with Path("/records/trajectory.jsonl").open("x") as output:
            for sequence, event in enumerate(session.stream(prompt), 1):
                received_ns = time.monotonic_ns()
                output.write(json.dumps({"sequence": sequence, "monotonic_ns": received_ns,
                                         "event": dataclasses.asdict(event)}, default=str) + "\n")
                output.flush()
                if event.kind == "result" and not result["completed"]:
                    result.update(completed=True, answer=event.text, tokens=dict(event.tokens),
                                  final_worker_monotonic_ns=received_ns)
                    Path("/records/final.txt").write_text(event.text or "")
                    emit("result")
    except AgentDeadline as error:
        result.update(timed_out=True, error=str(error))
        emit("deadline")
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        emit("error")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if agent is not None:
            try:
                agent.stop()
            except Exception as error:
                result["shutdown_error"] = f"{type(error).__name__}: {error}"
        Path("/records/result.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
