# DeLM math pilot

This historical profile, executed by the [shared harness](../README.md), runs one math problem under three conditions using
the original DeLM protected board, queue, native Codex runtime, and pinned image.
Upstream runtime and board code are unchanged.

| Condition | Execution | Graded submissions per run |
| --- | --- | --- |
| single | One Codex agent, native agents disabled | 1 |
| orchestrator | One Codex main agent with native subagents enabled | 1 main proof |
| delm | Two equal Codex peers, native agents disabled, original shared board | 2 |

The orchestrator prompt ends with the paper's instruction:
“Use Codex’s native subagents to help solve this task.” Its decomposition and number
of children are chosen by Codex. There is no custom planner, fixed role assignment,
or artificial two-agent cap. Actual tree size and each child's model/effort are
recorded and checked. This is a native-subagent comparison, not matched model compute.
The former independent-pair control is removed from new schedules; historical data
and prepared bundles are preserved.

## Frozen pilot settings

- Problem: IMO 2024 Shortlist C3, paired partners and adjacent swaps around a circle.
- Model/effort: `gpt-6-astra`, `xhigh`, including all native subagents.
- Codex 0.153.4 and the original repository's pinned Humanize driver.
- Authentication: existing ChatGPT coding-plan login; no paid API fallback.
- Deadline: 1,200 seconds from a shared controller release, including native children.
- Three fresh repetitions per condition: nine trials, one at a time on GCP.
- Balanced order: single/orchestrator/delm; orchestrator/delm/single;
  delm/single/orchestrator. Calibration is excluded from measured repeats.
- Common prompt version: `math-native-subagents-v3`. All write `proof.md` and
  submit a complete proof in their final response. Scratch Python is allowed.

Two earlier calibration attempts submitted after 540.29 and 963.13 seconds. Both
passed preliminary non-blind Codex inspection plus finite falsification checks;
independent blinded proof review remains pending. This supports provisional
retention of C3 for the pilot, not a claim of verified correctness. The selection
rule was to reject a problem if both correct attempts took under two minutes, or
if neither succeeded within twenty minutes. No second problem is selected.
Earlier calibration prompts did not require `proof.md`; all new comparison trials
use the same updated common prompt. Public benchmark exposure remains possible.

## Proof collaboration

DeLM peers divide useful mathematical work and publish partial proof files/lemmas
as attachments to notes and task completions. They read, expand, apply, and reconcile
peer contributions using the original board APIs. Each assembles a complete local
proof and publishes it before submission. Both proofs may use the same argument
and text; they need not be separate discoveries.

The board stores immutable published versions. It is not a live jointly editable
document. Applying a compatible version copies it locally; conflicts are resolved
locally and republished. There is no permanent proof integrator or coordinator.
The orchestrator uses Codex's own delegation mechanism and shared workspace.

## Isolation and resources

Each root worker has its own internal Docker network and workspace. Only problem
and prompt are exported; grader material and reference links stay on the controller.
A CONNECT proxy allows model/authentication hosts only. Direct internet and other
hosts are blocked, and the web-search tool is disabled. Each container is limited
to two CPUs, 4 GiB RAM, and 512 processes. Native children share their main agent's
container limits; the DeLM pair has two such containers. These are container limits,
not equal model-token budgets. The otherwise idle host has ample spare capacity.

Native subagent model/effort defaults are explicitly set and the effective config
is saved. Completed native traces are checked for actual model, effort, provider,
and CLI version; any mismatch stops the batch. No silent model fallback is allowed.
Worker shutdown stops the app-server group; controller deadline enforcement and
container removal stop the entire native group, including shell processes.

## Collection and grading

Primary accuracy is per submission, matching the paper: independently grade both
DeLM finals and the single final from each baseline. Native child answers are
intermediate work, not extra submissions. Produce a blinded review packet and keep
its identity mapping private. Use the official reference and rubric in `grader/`.
Uncertain proofs remain uncertain; formula matching and finite checks do not verify
an unrestricted mathematical proof. No proof is automatically marked correct.

Preserve controller release-to-final times, native task durations, every session's
raw JSONL, final proofs, workspace archives, board records, and source/image hashes.
Controller trial completion is the last root final; incomplete trials are censored
at the deadline and never silently dropped. Native task duration includes harness
handling and is a proxy for the paper's first-API-call-to-last-token metric, not an
exact reproduction. The raw events permit further timing analysis.

Whole-tree usage includes the main agent and all children, including unfinished
work. Inherited fork-history counters are excluded and repeated cumulative counters
are not counted twice. Missing traces or model mismatches stop collection. Interrupted
counts are preserved as observed usage, but complete totals stay null. Cost in USD
stays null because coding-plan tokens are not an attributable API invoice. Report
whole-trial tokens and tokens per graded submission.

`summarize.py` reports per-submission accuracy and trial latency as mean/sample
standard deviation across the three runs when resolved. Legacy `primary_*` fields
refer only to the secondary first-final selector; a fast wrong proof remains a
failure under that selector. Oracle earliest-correct timing is retrospective and
excludes reviewer time. Do not use success-only latency averages to claim speedup.
One problem and three repeats are a mechanism pilot, not broad evidence of speedup.

## Execution

On the GCP host, from the repository root:

```sh
python3.13 -m unittest discover -s experiments/math_harness -t . -v
python3.13 -m experiments.math_harness.prepare --experiment math_pilot --phase smoke --output .local/math-pilot/smoke-NEW
python3.13 -m experiments.math_harness.prepare --experiment math_pilot --phase measured --output .local/math-pilot/measured-NEW
```

Smoke trials use an elementary arithmetic identity and are excluded from results.
Run a prepared schedule with `python3.13 -m experiments.math_harness.batch --bundle PATH --output NEW_PATH`; measured
trials also require `--calibration-approved`, acknowledging the documented
preliminary review and provisional problem selection. Independent grading is still
required before interpreting accuracy or speedup. Every output path is new; there
are no hidden retries or overwrites. Infrastructure/collection errors stop the batch;
legitimate timeouts remain observations and the schedule continues.

Launch with a system-level transient service so SSH/laptop disconnection is harmless:

```sh
sudo systemd-run --unit=delm-math-UNIQUE --uid=ktianshi --gid=docker \
  --working-directory=/home/ktianshi/DeLM \
  /home/ktianshi/.local/bin/python3.13 -m experiments.math_harness.batch \
  --bundle .local/math-pilot/measured-NEW \
  --output .local/math-pilot/runs/measured-NEW --calibration-approved
```

The batch also writes `records.json`, `summary.json`, and an anonymized `review-packet/`
with a separate private mapping upon completion. Grading remains pending. Source
hashes must match the frozen bundle; editing the adapter requires a new bundle.

Inspect `batch.json` and per-trial `run.json`/`normalized.json` for status; systemd
journal retains launch failures. Existing historical smoke and calibration data stay
under `.local/math-pilot/`. All generated research data are gitignored. Preparing
bundles creates no cloud resources. Execution uses the existing GCP VM and account.
