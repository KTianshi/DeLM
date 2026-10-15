# Adaptive coordination follow-up (v4)

This profile uses the [shared harness](../README.md). Historical frozen
plans, source snapshots, raw observations, and evaluations remain unchanged. This follow-up was
chosen after inspecting C3 results and is exploratory development, not a held-out
confirmation of speedup.

The sole substantive solver-prompt change is in prompts/delm.txt: reassess the work
split at completion/failure/new-idea events, choose distinct exploratory routes,
and publish explicitly incomplete sketches with specific gaps early. No C3 proof
strategy, lemma, answer, or previous-run trace is supplied to any worker. The common
solver prompt is byte-identical to v3. Model, tools, board, deadline, and grading
submission rules remain the same. Each DeLM peer still submits its own complete
proof. Variants with 2 or 4 peers use the same generalized startup instructions;
the 4-peer variant is an extension of this math protocol, not a reproduction of
the original coding prompt's four distinct startup roles.

The shared prepare command accepts configuration fields selected_conditions and delm_worker_count
for a filtered DeLM-only schedule. Use a new immutable bundle per peer count.
Source collection snapshots the shared harness and selected profile, and source hashes are enforced before
execution. Existing runtime/board code is unchanged.

Three repeats at a 20-minute limit take at most about one hour plus setup per
peer-count condition. Run with the same detached systemd service pattern as v3.
AI proof review occurs after collection; no final proof is automatically correct.

The historical v3 protocol is documented in ../math_pilot/README.md.
