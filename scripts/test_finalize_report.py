"""Synthetic end-to-end finalization, fail-closed handoff and timing tests."""

from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _test_fixtures import refresh_review, refresh_capture_evidence
from build_capture_ledger import build_ledger
import finalize_report as finalizer
from test_build_capture_ledger import fixture_inputs
import run_timing
from validate_ranking import canonical_sha256


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.captures, self.run, self.groups, _ = fixture_inputs()
        (self.root / "captures").mkdir()
        self.prepare()

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def prepare(self, groups=None):
        chosen = self.groups if groups is None else groups
        self.write("run.json", self.run)
        self.write("groups.json", chosen)
        for source, packet in self.captures.items():
            self.write(f"captures/{source}.json", packet)
        checkpoint = build_ledger(self.captures, self.run, chosen, partial=True)
        refresh_review(checkpoint)
        self.write("review.json", checkpoint["clustering_review"])

    def assert_pass(self, result):
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(Path(result["report"]).is_file())
        self.assertEqual([step["status"] for step in result["steps"]], ["passed"] * 5)
        self.assertEqual(result["delivery_status"], "not_recorded")
        return Path(result["report"]).read_text(encoding="utf-8")

    def assert_fail(self, result, stage=None):
        self.assertEqual(result["status"], "failed", result)
        self.assertNotIn("report", result)
        self.assertEqual(self.read("latest-attempt.json")["status"], "failed")
        if stage:
            self.assertEqual(result["failed_stage"], stage, result)

    def test_nondefault_limit_propagates_and_artifacts_bind_to_current_ledger(self):
        result = finalizer.finalize(self.root, limit=1)
        report = self.assert_pass(result)
        self.assertIn("Top 1（快速版）", report)
        ledger = json.loads(Path(result["ledger"]).read_text(encoding="utf-8"))
        ranking = json.loads(Path(result["ranking"]).read_text(encoding="utf-8"))
        self.assertEqual(ranking["limit"], 1)
        self.assertEqual(result["ledger_sha256"], ranking["ledger_sha256"])
        self.assertEqual(result["ledger_sha256"], canonical_sha256(ledger))
        self.assertEqual(result["run_id"], ranking["run_id"])
        self.assertEqual(self.read("latest-success.json")["attempt_id"], result["attempt_id"])

    def test_missing_review_stops_before_build_and_never_uses_empty_groups_as_review(self):
        (self.root / "review.json").unlink()
        self.write("groups.json", [])
        result = finalizer.finalize(self.root)
        self.assert_fail(result, "inputs")
        self.assertIn("review.json", str(result["errors"]))
        self.assertEqual(result["steps"], [])

    def test_incomplete_capture_stops_and_saves_actual_failure(self):
        (self.root / "captures/caixin.json").unlink()
        result = finalizer.finalize(self.root)
        self.assert_fail(result, "build_ledger")
        self.assertEqual(result["steps"][-1]["status"], "failed")
        self.assertIn("caixin", str(result["errors"]))
        self.assertFalse((self.root / "latest-success.json").exists())

    def test_explicitly_completed_no_match_review_allows_genuine_zero_topic_output(self):
        self.prepare(groups=[])
        result = finalizer.finalize(self.root)
        report = self.assert_pass(result)
        self.assertEqual(result["selected_count"], 0)
        self.assertIn("当前没有话题同时出现在至少两个不同核心域名。", report)
        self.assertNotIn("| 排名 |", report)

    def test_changed_grouping_without_review_blocks_publication(self):
        self.write("groups.json", [])
        result = finalizer.finalize(self.root)
        self.assert_fail(result, "build_ledger")
        self.assertIn("review", str(result["errors"]))

    def test_missing_selected_link_blocks_then_observed_backfill_rerun_succeeds(self):
        row = self.captures["weibo"]["rows"][0]
        observed_url = row["url"]
        row["url"] = None
        row.pop("raw_url", None)
        refresh_capture_evidence(self.captures, self.run)
        self.prepare()
        raw_before = (self.root / "captures/weibo.json").read_bytes()
        review_before = (self.root / "review.json").read_bytes()
        failure = finalizer.finalize(self.root)
        self.assert_fail(failure, "selected_links")
        self.assertIn("weibo-001", str(failure["errors"]))
        self.write("corrections.json", [{
            "id": "synthetic-link-backfill-1", "run_id": self.run["run_id"], "source": "weibo",
            "item_id": "weibo-001", "title": row["title"], "display_order": 1,
            "corrected_at": "2026-08-06T09:12:00+08:00", "before": {"url": None},
            "after": {"url": observed_url}, "evidence": "Synthetic same-row href observation",
            "observation": {"document_url": "https://s.weibo.com/top/summary", "observed_title": row["title"],
                            "observed_url": observed_url, "observed_at": "2026-08-06T09:11:59+08:00"},
        }])
        success = finalizer.finalize(self.root)
        report = self.assert_pass(success)
        self.assertIn(observed_url, report)
        self.assertEqual((self.root / "captures/weibo.json").read_bytes(), raw_before)
        self.assertEqual((self.root / "review.json").read_bytes(), review_before)

    def test_independent_final_validation_catches_tampered_generated_text(self):
        original = finalizer.generate_report
        def tamper(*args):
            report, errors = original(*args)
            self.assertEqual(errors, [])
            return report + "\n额外添加未经授权的概述。\n", []
        with patch.object(finalizer, "generate_report", side_effect=tamper):
            result = finalizer.finalize(self.root)
        self.assert_fail(result, "final_validation")
        self.assertFalse((Path(result["attempt_dir"]) / "report.md").exists())
        self.assertTrue((Path(result["attempt_dir"]) / "report.pending.md").exists())

    def test_failed_rerun_keeps_old_success_but_current_result_never_claims_it(self):
        success = finalizer.finalize(self.root)
        self.assert_pass(success)
        old_pointer = (self.root / "latest-success.json").read_bytes()
        old_report = Path(success["report"]).read_bytes()
        (self.root / "review.json").unlink()
        failure = finalizer.finalize(self.root)
        self.assert_fail(failure)
        self.assertNotEqual(success["attempt_id"], failure["attempt_id"])
        self.assertEqual((self.root / "latest-success.json").read_bytes(), old_pointer)
        self.assertEqual(Path(success["report"]).read_bytes(), old_report)

    def init_timing(self, active=False):
        path = self.root / "timing.jsonl"
        base = {"version": 1, "run_id": self.run["run_id"], "at": datetime.now().astimezone().isoformat(), "clock": "live"}
        run_timing.append_event(path, {**base, "event": "init", "request_at": None})
        if active:
            run_timing.append_event(path, {**base, "event": "start", "id": "still-collecting", "phase": "collect", "source": "weibo"})
        return path

    def test_existing_timing_span_is_never_ended_by_finalization(self):
        path = self.init_timing(active=True)
        before = path.read_bytes()
        result = finalizer.finalize(self.root)
        self.assert_fail(result, "inputs")
        self.assertIn("still-collecting", str(result["errors"]))
        self.assertEqual(path.read_bytes(), before)

    def test_local_timing_is_disjoint_and_does_not_claim_delivery(self):
        path = self.init_timing()
        result = finalizer.finalize(self.root)
        self.assert_pass(result)
        events = run_timing.read_events(path)
        active, finished = run_timing.validate(events, self.run["run_id"])
        self.assertIsNone(active)
        self.assertIsNone(finished)
        self.assertEqual([event["phase"] for event in events if event["event"] == "start"], ["persist", "validate"])

    def test_failed_build_closes_only_its_own_timing_span(self):
        path = self.init_timing()
        (self.root / "captures/caixin.json").unlink()
        result = finalizer.finalize(self.root)
        self.assert_fail(result, "build_ledger")
        active, finished = run_timing.validate(run_timing.read_events(path), self.run["run_id"])
        self.assertIsNone(active)
        self.assertIsNone(finished)

    def test_cli_runs_all_stages_and_returns_nonzero_on_failed_followup(self):
        script = Path(finalizer.__file__)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        command = [sys.executable, str(script), "--run-dir", str(self.root), "--limit", "3"]
        success = subprocess.run(command, text=True, capture_output=True, env=env)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn("PASS quick report ready", success.stdout)
        self.assertIn("limit=3", success.stdout)
        (self.root / "review.json").unlink()
        failure = subprocess.run(command, text=True, capture_output=True, env=env)
        self.assertNotEqual(failure.returncode, 0)
        self.assertNotIn("PASS", failure.stdout)
        self.assertIn("FAIL finalization", failure.stderr)

    def test_invalid_limit_or_concurrent_run_is_rejected_without_mutating_inputs(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            finalizer.finalize(self.root, limit=True)
        with finalizer.run_lock(self.root / ".finalize.lock"):
            with self.assertRaisesRegex(ValueError, "another finalization"):
                finalizer.finalize(self.root)

    def test_abrupt_process_exit_releases_lock_and_stale_filename_does_not_block(self):
        scripts = str(Path(finalizer.__file__).parent)
        code = "import os,sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from finalize_report import run_lock;guard=run_lock(Path(sys.argv[2]));guard.__enter__();os._exit(0)"
        result = subprocess.run([sys.executable, "-c", code, scripts, str(self.root / ".finalize.lock")],
                                capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".finalize.lock").exists())
        self.assert_pass(finalizer.finalize(self.root))

    def test_run_directory_inside_another_skill_is_rejected(self):
        other = self.root / "another-installed-skill"
        nested = other / "work" / "run"
        nested.mkdir(parents=True)
        (other / "SKILL.md").write_text("Synthetic different Skill", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside the installed Skill"):
            finalizer.finalize(nested)
        self.assertFalse((nested / "attempts").exists())


if __name__ == "__main__":
    unittest.main()
