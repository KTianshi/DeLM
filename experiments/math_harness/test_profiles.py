"""Regression checks against offline bundles produced before consolidation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from experiments.math_harness.prepare import prepare, settings, provenance
from experiments.math_harness.profiles import EXPERIMENTS, ROOT, profile_dir
from experiments.math_harness.run import checked_bundle
from experiments.math_harness.export_results import export
from experiments.math_harness import batch, run_queue


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ProfileTests(unittest.TestCase):
    def test_all_historical_inputs_schedules_and_defaults_are_preserved(self):
        golden = json.loads((Path(__file__).parent / "fixtures/legacy-bundles.json").read_text())
        for name, phases in golden.items():
            for phase, expected in phases.items():
                with self.subTest(experiment=name, phase=phase), tempfile.TemporaryDirectory() as temporary:
                    folder = Path(temporary) / "bundle"
                    manifest = prepare(folder, settings(experiment=name), phase)
                    config = dict(manifest["configuration"])
                    self.assertEqual(config.pop("experiment"), name)
                    self.assertEqual(fingerprint(config), expected["configuration_sha256"])
                    self.assertEqual(fingerprint(manifest["runs"]), expected["schedule_sha256"])
                    self.assertEqual(fingerprint(manifest["worker_files"]), expected["worker_files_sha256"])
                    for run in manifest["runs"]:
                        _, _, inputs = checked_bundle(folder, run["run_id"])
                        self.assertEqual(len(inputs), run["worker_count"])

    def test_cli_uses_each_profiles_default_peer_count(self):
        for name in EXPERIMENTS:
            with self.subTest(experiment=name), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary) / "bundle"
                subprocess.run([sys.executable, "-m", "experiments.math_harness.prepare",
                                "--experiment", name, "--phase", "smoke", "--output", str(folder)],
                               cwd=ROOT, check=True, capture_output=True, text=True)
                manifest = json.loads((folder / "manifest.json").read_text())
                peer_run = next(r for r in manifest["runs"] if r["condition"] == "delm")
                self.assertEqual(peer_run["worker_count"], settings(experiment=name).get("delm_worker_count", 2))

    def test_profiles_do_not_leak_through_module_globals(self):
        for name in ("math_pilot_matching_v1", "math_pilot", "math_pilot_matroid_v1", "math_pilot_v4"):
            config = settings(experiment=name)
            files = provenance(config)["files"]
            for prefix in ("experiments/math_harness/", f"experiments/{name}/", "runtime/", "board/"):
                self.assertTrue(any(key.startswith(prefix) for key in files))
            for other in set(EXPERIMENTS) - {name}:
                self.assertFalse(any(key.startswith(f"experiments/{other}/") for key in files))

    def test_invalid_profile_and_legacy_bundle_fail_before_execution(self):
        with self.assertRaises(ValueError):
            settings(experiment="../math_pilot")
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "bundle"
            manifest = prepare(folder, settings(), "smoke")
            del manifest["configuration"]["experiment"]
            (folder / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Legacy bundle"):
                checked_bundle(folder, manifest["runs"][0]["run_id"])

    def test_changed_shared_runtime_or_profile_source_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "bundle"
            manifest = prepare(folder, settings(), "smoke")
            for prefix in ("experiments/math_harness/", "experiments/math_pilot_v4/", "runtime/", "board/"):
                with self.subTest(source=prefix):
                    changed = json.loads(json.dumps(manifest))
                    key = next(k for k in changed["provenance"]["files"] if k.startswith(prefix))
                    changed["provenance"]["files"][key] = "changed"
                    (folder / "manifest.json").write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, "source changed"):
                        checked_bundle(folder, manifest["runs"][0]["run_id"])

    def test_rejects_empty_schedule_and_incompatible_original_prompt(self):
        for updates in ({"selected_conditions": []}, {"selected_conditions": ["typo"]}, {"delm_worker_count": 4}):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as temporary:
                config = settings(experiment="math_pilot")
                config.update(updates)
                folder = Path(temporary) / "bundle"
                with self.assertRaises(ValueError):
                    prepare(folder, config, "measured")
                self.assertFalse(folder.exists())

    def test_review_packet_uses_selected_problem_and_rubric(self):
        for name in EXPERIMENTS:
            with self.subTest(experiment=name), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                config = settings(experiment=name)
                export({"configuration": config, "runs": []}, folder, complete=True)
                profile = profile_dir(config)
                problem = config["problem_id"]
                self.assertEqual((folder / "review-packet/PROBLEM.txt").read_bytes(),
                                 (profile / "problems" / f"{problem}.txt").read_bytes())
                self.assertEqual((folder / "review-packet/rubric.json").read_bytes(),
                                 (profile / "grader" / f"{problem}.json").read_bytes())

    def test_batch_and_queue_stop_on_worker_error_without_retry(self):
        for module, option in ((batch, "--bundle"), (run_queue, "--bundles")):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                bundle = base / "bundle"
                config = settings(experiment="math_pilot_matching_v1")
                prepare(bundle, config, "calibration")
                output = base / "results"
                def fake_run(command, **kwargs):
                    self.assertEqual(command[1:3], ["-m", "experiments.math_harness.run"])
                    self.assertEqual(kwargs["cwd"], ROOT)
                    self.assertEqual(Path(command[command.index("--bundle") + 1]), bundle.resolve())
                    trial = Path(command[command.index("--output") + 1])
                    trial.mkdir(parents=True)
                    (trial / "run.json").write_text(json.dumps({"workers": [{"status": "error"}]}))
                argv = [module.__name__, option, str(bundle), "--output", str(output)]
                with patch.object(sys, "argv", argv), patch.object(module.subprocess, "run", side_effect=fake_run) as launched:
                    with self.assertRaises(RuntimeError):
                        module.main()
                    self.assertEqual(launched.call_count, 1)
                self.assertEqual(json.loads((output / "batch.json").read_text())["status"], "failed")
