# Math experiment harness

One shared standard-library adapter runs the four historical math profiles using
DeLM's existing runtime and protected board. Profiles contain configuration,
problem statements, prompts, grading rubrics and protocol notes; Python execution,
collection, accounting and tests live in `math_harness/`.

| Profile | Task | Default DeLM peers | Deadline | Protocol |
| --- | --- | --- | --- | --- |
| [math_pilot](math_pilot/README.md) | IMO 2024 C3 | 2 | 20 minutes | Original math collaboration prompt |
| [math_pilot_v4](math_pilot_v4/README.md) | IMO 2024 C3 | 2 | 20 minutes | Adaptive coordination follow-up |
| [math_pilot_matroid_v1](math_pilot_matroid_v1/README.md) | Matroid intersection | 4 | 40 minutes | Exploratory selection; no calibration claim |
| [math_pilot_matching_v1](math_pilot_matching_v1/README.md) | General matching | 4 | 60 minutes | Single-agent calibration first |

Every profile preserves its original task, prompts, model/effort, repetition
counts and deadline. The v4 prompt is an experimental adaptation, not a claim of
reproducing the original coding protocol. Rubrics stay on the controller.

## Offline preparation and checks

Use Python 3.12+ from the repository root. These commands do not launch models,
Docker containers or cloud resources:

```sh
python3 -m unittest discover -s experiments/math_harness -t . -v
python3 -m experiments.math_harness.prepare \
  --experiment math_pilot_matching_v1 --phase calibration \
  --output .local/math-pilot/matching-calibration-NEW
python3 -m experiments.math_harness.prepare \
  --experiment math_pilot_v4 --phase measured --delm-only --peers 4 \
  --output .local/math-pilot/c3-four-peers-NEW
```

`--experiment` is required at the CLI; `--peers` overrides the profile default.
The original profile permits two peers; the adaptive profiles permit two or four.
This consolidation does not introduce eight-agent scaling or automatic retries.
The matching calibration still schedules only one single-agent run regardless of
the profile's default DeLM peer count. Exploratory preparation is available only
for the matroid and matching profiles. Smoke preparation uses arithmetic inputs.

## Explicit live execution

Execution requires an idle Linux Docker host, an appropriate prebuilt Codex image,
and the existing coding-plan authentication. The harness holds the machine lock,
limits each peer to 2 CPUs/4 GiB and enforces a shared monotonic deadline.

```sh
python3 -m experiments.math_harness.batch \
  --bundle .local/math-pilot/matching-calibration-NEW \
  --output .local/math-pilot/runs/matching-calibration-NEW
```

Measured trials require `--calibration-approved` after the profile's documented
review. Use `python3 -m experiments.math_harness.run --help` for one trial and its
image/auth options. `python3 -m experiments.math_harness.run_queue --help` describes
alternating equal-length variant bundles. A batch stops on infrastructure or
collection failures; ordinary timeouts remain observations. Nothing is retried
or graded correct automatically. Use fresh output paths for every invocation.

## Source freezing and migration

Previous commands such as `python experiments/math_pilot_v4/prepare.py ...` are
replaced by `python -m experiments.math_harness.prepare --experiment math_pilot_v4 ...`.
Batch, run and queue commands infer the profile from the prepared manifest.
New manifests hash the selected profile, shared harness, runtime and board sources.
The controller snapshots these sources; worker mounts remain restricted to the
execution tools and the rendered task/prompt. A changed source or worker input
invalidates a prepared bundle.

Existing frozen bundles cannot run with the consolidated adapter: use their
archived source snapshot, or prepare a new bundle for a new run. Historical outputs
and source snapshots under `.local/` are untouched and remain gitignored. The
regression fixture records SHA-256 fingerprints of configuration, schedules and
worker input hashes produced by each original adapter for every supported phase.
Tests compare the new outputs, excluding only the newly added profile identifier.
The fixture contains no model calls or private session data.

The local Erdős Lean runs and independent verification artifacts are not part of
these four prose-proof profiles. They remain separate local experiments; this PR
does not claim to migrate their formal-proof replay pipeline.

## Review and interpretation

`export_results` creates blinded proof packets and keeps identity mappings outside
the packet. `summarize` preserves per-submission accuracy, timeout censoring, first
final versus retrospective earliest-correct timing, and incomplete usage coverage.
Native session collection checks child identities and avoids counting inherited
history twice. Coding-plan token counts do not imply an attributable dollar charge.
Independent proof review is still required. A small pilot is not broad evidence of
speedup, and these profiles do not automatically diagnose dependency bottlenecks.
