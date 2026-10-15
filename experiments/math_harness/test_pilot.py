import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.math_harness.prepare import prepare, schedule, settings, worker_prompt
from experiments.math_harness.summarize import summarize, summarize_run
from experiments.math_harness.egress import destination
from experiments.math_harness.run import submission_status, checked_bundle, record_usage
from experiments.math_harness.export_results import export


class PreparationTests(unittest.TestCase):
    def test_control_prompts_identical_and_board_only_in_delm(self):
        config = settings()
        single = worker_prompt(config, "single", "agent-1")
        orchestrator = worker_prompt(config, "orchestrator", "agent-1")
        self.assertNotIn("Do not create additional agents", orchestrator)
        self.assertTrue(orchestrator.endswith("Use Codex’s native subagents to help solve this task.\n"))
        self.assertNotIn("/tools/context.sh", orchestrator)
        self.assertNotIn("/tools/context.sh", single)
        self.assertIn("/tools/context.sh", worker_prompt(config, "delm", "agent-1"))

    def test_frozen_bundle_rejects_changed_adapter_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'bundle'
            manifest=prepare(output,settings(),'smoke')
            manifest['provenance']['files']['worker.py']='changed'
            (output/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'source changed'):
                checked_bundle(output,manifest['runs'][0]['run_id'])

    def test_four_peer_bundle_roster_and_collection(self):
        config=settings();config.update(delm_worker_count=4,selected_conditions=['delm'])
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'bundle'
            manifest=prepare(output,config,'measured')
            self.assertEqual(len(manifest['runs']),3)
            self.assertTrue(all(r['worker_count']==4 and r['board_enabled'] for r in manifest['runs']))
            _,_,inputs=checked_bundle(output,manifest['runs'][0]['run_id'])
            self.assertEqual(len(inputs),4)
            prompt=inputs[-1]['PROMPT.txt'].read_text()
            self.assertIn('one of 4 equal peers',prompt)
            self.assertIn('agent-1, agent-2, agent-3, agent-4',prompt)
            self.assertIn('You are agent-4',prompt)
            self.assertIn('Reassess the division of work',prompt)
            self.assertNotIn('n(n-1)/2',prompt)

    def test_balanced_order_and_calibration_separation(self):
        config = settings()
        measured = schedule(config, "measured")
        self.assertEqual(len(measured), 9)
        self.assertEqual({r["condition"] for r in measured[::3]}, {"single", "orchestrator", "delm"})
        self.assertEqual(len({r["run_id"] for r in measured}), 9)
        self.assertEqual([r["condition"] for r in schedule(config, "calibration")], ["single", "single"])

    def test_worker_export_allowlist_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            manifest = prepare(output, settings(), "measured")
            self.assertFalse(manifest["ready_for_live_execution"])
            worker_files = [p for p in output.rglob("*") if p.is_file() and p.name != "manifest.json"]
            self.assertEqual(len(worker_files), 24)
            self.assertEqual({p.name for p in worker_files}, {"PROBLEM.txt", "PROMPT.txt"})
            for path in worker_files:
                self.assertNotIn("n(n-1)/2", path.read_text())
                self.assertNotIn("imo-official.org", path.read_text())
            with self.assertRaises(ValueError):
                prepare(output, settings())

    def test_reasoning_only_policy_propagates_to_all_conditions(self):
        config = settings(tool_policy="reasoning-only")
        for condition in config["conditions"]:
            self.assertIn("Do not use Python", worker_prompt(config, condition, "agent-1"))

    def test_smoke_does_not_export_benchmark_problem(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = prepare(Path(temporary) / "smoke", settings(), "smoke")
            self.assertEqual(len(manifest["runs"]), 3)
            self.assertEqual(manifest["configuration"]["problem_id"], "arithmetic_smoke")
            self.assertEqual(manifest["configuration"]["deadline_seconds"], 180)
            self.assertTrue(all(r["phase"] == "smoke" for r in manifest["runs"]))

    def test_proxy_rejects_other_hosts_ports_and_host_confusion(self):
        self.assertEqual(destination("chatgpt.com:443"), ("chatgpt.com", 443))
        for authority in ("example.org:443", "chatgpt.com:80", "chatgpt.com.evil.test:443",
                          "user@chatgpt.com:443", "127.0.0.1:443", "chatgpt.com:443:443"):
            with self.assertRaises(ValueError):
                destination(authority)

    def test_live_runner_rejects_modified_worker_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            manifest = prepare(output, settings(), "smoke")
            run_id = manifest["runs"][0]["run_id"]
            checked_bundle(output, run_id)
            (output / run_id / "agent-1/PROBLEM.txt").write_text("modified")
            with self.assertRaisesRegex(ValueError, "changed after preparation"):
                checked_bundle(output, run_id)

    def test_controller_deadline_classifies_late_final_as_timeout(self):
        self.assertEqual(submission_status(100, 200, 200, {}), "submitted")
        self.assertEqual(submission_status(100, 200, 201, {}), "timeout")
        self.assertEqual(submission_status(100, 200, None, {"timed_out": True}), "timeout")
        self.assertEqual(submission_status(None, None, None, {"error": "login"}), "error")

    def test_interrupted_turn_usage_is_not_reported_as_complete_total(self):
        observed = {"input_tokens": 40, "output_tokens": 20, "cached_input_tokens": 10}
        row = {"input_tokens": None, "output_tokens": None}
        record_usage(row, observed, {"timed_out": True})
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["output_tokens"])
        self.assertEqual(row["observed_usage"], observed)
        self.assertEqual(row["usage_coverage"], "partial_or_unknown")
        record_usage(row, observed, {"completed": True})
        self.assertEqual(row["input_tokens"], 40)
        self.assertEqual(row["output_tokens"], 20)
        self.assertEqual(row["usage_coverage"], "complete")


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = settings()
        self.plan = schedule(self.config, "measured")[2]
        self.record = {"run_id": self.plan["run_id"], "workers": [
            {"worker_id": "agent-1", "status": "submitted", "final_elapsed_seconds": 30,
             "verdict": "incorrect", "input_tokens": 20, "output_tokens": 10, "cost_usd": None},
            {"worker_id": "agent-2", "status": "submitted", "final_elapsed_seconds": 60,
             "verdict": "correct", "input_tokens": 40, "output_tokens": 20, "cost_usd": None},
        ]}

    def score(self):
        return summarize_run(self.record, self.plan, 1200)

    def test_each_delm_submission_is_scored(self):
        self.assertEqual(self.score()['submission_accuracy'],0.5)
        self.record['workers'][0]['verdict']='ungraded'
        self.assertIsNone(self.score()['submission_accuracy'])

    def test_export_preserves_proofs_but_separates_identity_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)
            folder=output/self.plan['run_id']
            folder.mkdir()
            (folder/'normalized.json').write_text(json.dumps(self.record))
            for worker in self.record['workers']:
                child=folder/worker['worker_id']; child.mkdir()
                (child/'final.txt').write_text('Complete proof '+worker['worker_id'])
            export({'configuration':self.config,'runs':[self.plan]},output,complete=True)
            packet=output/'review-packet'
            self.assertEqual(len(list(packet.glob('proof-*.txt'))),2)
            self.assertTrue((output/'review-mapping-private.json').exists())
            self.assertFalse((packet/'review-mapping-private.json').exists())
            self.assertTrue(all(r['verdict']=='ungraded' for r in json.loads((packet/'reviews.json').read_text()).values()))

    def test_fast_wrong_answer_is_failure_not_oracle_success(self):
        result = self.score()
        self.assertFalse(result["primary_success"])
        self.assertEqual(result["primary_submission_seconds"], 30)
        self.assertEqual(result["time_to_first_correct_seconds_oracle"], 60)
        self.assertEqual(result["total_usage_all_workers"]["input_tokens"], 60)
        self.assertIsNone(result["total_usage_all_workers"]["cost_usd"])

    def test_ungraded_earlier_submission_does_not_become_failure_or_oracle_time(self):
        self.record["workers"][0]["verdict"] = "uncertain"
        result = self.score()
        self.assertIsNone(result["primary_success"])
        self.assertFalse(result["oracle_resolved"])
        self.assertIsNone(result["time_to_first_correct_seconds_oracle"])

    def test_late_answer_is_not_timely_success(self):
        for worker in self.record["workers"]:
            worker["final_elapsed_seconds"] = 1201
        self.assertEqual(self.score()["primary_outcome"], "no_timely_submission")
        self.assertFalse(self.score()["primary_success"])

    def test_timeout_and_missing_usage_preserved(self):
        self.record["workers"][0] = {"worker_id": "agent-1", "status": "timeout"}
        result = self.score()
        self.assertTrue(result["primary_success"])
        self.assertIsNone(result["total_usage_all_workers"]["input_tokens"])

    def test_duplicate_worker_or_nan_time_rejected(self):
        bad = copy.deepcopy(self.record)
        bad["workers"][1]["worker_id"] = "agent-1"
        with self.assertRaises(ValueError):
            summarize_run(bad, self.plan, 1200)
        self.record["workers"][0]["final_elapsed_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            self.score()

    def test_ties_use_worker_identity_not_correctness(self):
        self.record["workers"][1]["final_elapsed_seconds"] = 30
        self.record["workers"].reverse()
        self.assertFalse(self.score()["primary_success"])

    def test_missing_runs_are_visible_and_duplicate_records_rejected(self):
        manifest = {"configuration": self.config, "runs": schedule(self.config, "measured")}
        result = summarize(manifest, [self.record])
        self.assertEqual(len(result["missing_run_ids"]), 8)
        self.assertTrue(all(g["primary_success_fraction"] is None for g in result["groups"]))
        with self.assertRaises(ValueError):
            summarize(manifest, [self.record, self.record])


if __name__ == "__main__":
    unittest.main()
