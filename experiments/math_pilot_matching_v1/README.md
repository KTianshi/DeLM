# General matching long-horizon calibration

One single-agent calibration for a self-contained blossom algorithm, Tutte-Berge
certificate and Gallai-Edmonds structural theorem and construction. The task is
known mathematics, not a claim of novel discovery. The reference and rubric are
controller-only and never exposed to the solver.

The model, effort, scratch-Python tool policy, common and DeLM prompt templates,
worker/runtime implementations, individual proof.md plus full final submission,
and timing/collection rules are unchanged from math_pilot_matroid_v1. The task
and deadline change. No problem-specific agent roles are prescribed.

Prepare with `python3.13 -m experiments.math_harness.prepare --experiment
math_pilot_matching_v1 --phase calibration --output NEW_BUNDLE` and execute with
`python3.13 -m experiments.math_harness.batch --bundle NEW_BUNDLE --output
NEW_RESULTS` from the repository root in a detached system service.
Only one single worker is scheduled, with a 3600-second limit. Authentication,
network restrictions and pinned image validation use the established harness.

Selection is frozen before the calibration: a correct complete submission taking
1800--3600 seconds qualifies as a candidate for the long-horizon comparison.
A faster correct result does not meet that target. Incorrect/uncertain results
and timeouts do not qualify. A timeout alone is not evidence of useful long-horizon
work. Review occurs after collection; a submitted proof is never auto-correct.
No orchestrator or DeLM trials are scheduled by this bundle. Calibration is not
one of the three future measured repeats. Any later comparison must use a new
frozen plan and preserve this observation and its review.
