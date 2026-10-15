# Matroid intersection exploratory comparison

The user selected a constructive proof of finite, unweighted matroid intersection
by judgment and authorized running the comparison without a separate calibration
screen. This is an exploratory problem change, not a validated harder benchmark.
The theorem is public and may be familiar to the model.

The problem supplies independent-set axioms and independence-oracle access. It
requires a polynomial algorithm, a complete proof and a tight rank certificate.
Every additional matroid lemma must be proved. No problem-specific proof route,
reference solution, or suggested decomposition is supplied to the workers.

The common and DeLM prompt templates are byte-identical to math_pilot_v4. The
model remains gpt-6-astra at xhigh with scratch Python. Individual proof.md files,
complete final-response submissions, shared-board snapshots and per-worker stop
rules remain unchanged, including the duplicate final writeout. Native subagent
orchestration uses the same existing instruction and configuration.

This batch uses single, orchestrator and DeLM-4, three repeats each, with rotating
condition order. Each trial has a common 2400-second deadline. The orchestrator
chooses its own native subagent count, which is recorded; it is not forced to
have the same headcount as DeLM-4. The two changes from the latest experiment are
the problem and the deadline; comparisons to the earlier task are descriptive.

The added exploratory preparation phase accurately records the absence of
problem-specific calibration. It uses the existing run/batch implementation;
no calibration-approved flag is needed or asserted. Historical frozen bundles, source snapshots and results
remain unchanged. Worker inputs contain only the task and existing prompts.
Grading and references stay on the controller and are never mounted to workers.

Prepare with `python3.13 -m experiments.math_harness.prepare --experiment
math_pilot_matroid_v1 --phase exploratory --peers 4 --output BUNDLE`, then
run `python3.13 -m experiments.math_harness.batch --bundle BUNDLE --output
NEW_RESULTS` from the repository root in a detached system service.
Infrastructure errors stop the batch; timeouts remain observations. Reviews
occur after collection; final submissions are not automatically correct.
