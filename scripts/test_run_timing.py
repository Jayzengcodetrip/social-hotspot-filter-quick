import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_timing as timing


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "timing.jsonl"

    def command(self, command, seconds=0, *extra, fails=False, run_id="test-run", live=False):
        args = [command, str(self.path), "--run-id", run_id, *extra]
        if not live:
            args += ["--test-at", f"2026-09-04T10:00:{seconds:02d}+08:00"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if fails:
                before = self.path.read_bytes() if self.path.exists() else None
                with self.assertRaises(SystemExit) as caught:
                    timing.main(args)
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(before, self.path.read_bytes() if self.path.exists() else None)
                return stderr.getvalue()
            self.assertEqual(timing.main(args), 0)
        return json.loads(stdout.getvalue())

    def init(self, *extra):
        return self.command("init", 0, *extra)

    def test_disjoint_source_phase_and_actual_request_total(self):
        self.init("--request-at", "2026-09-04T09:59:58+08:00")
        self.command("start", 2, "--id", "pre", "--phase", "preflight")
        self.command("end", 5, "--id", "pre")
        self.command("start", 6, "--id", "bj", "--phase", "collect", "--source", "bjnews")
        self.command("end", 12, "--id", "bj")
        self.command("start", 13, "--id", "cx", "--phase", "collect", "--source", "caixin")
        self.command("end", 18, "--id", "cx")
        self.command("finish", 20, "--outcome", "delivered")
        result = self.command("report", 25)
        self.assertEqual(result["elapsed_seconds"], 22)
        self.assertEqual(result["request_to_delivery_seconds"], 22)
        self.assertEqual(result["phase_seconds"], {"collect": 11, "preflight": 3})
        self.assertEqual(result["source_seconds"], {"bjnews": 6, "caixin": 5, "unassigned": 3})
        self.assertEqual(result["unattributed_seconds"], 8)
        self.assertEqual(sum(result["phase_seconds"].values()) + result["unattributed_seconds"], 22)
        self.assertIn("No network cause", result["boundary_note"])

    def test_unknown_request_is_not_claimed(self):
        self.init()
        self.command("finish", 10, "--outcome", "delivered")
        result = self.command("report", 12)
        self.assertEqual(result["origin"], "instrumentation_start")
        self.assertIsNone(result["request_to_delivery_seconds"])
        self.assertEqual(result["unattributed_seconds"], 10)

    def test_open_span_uses_observation_boundary(self):
        self.init()
        self.command("start", 2, "--id", "bj", "--phase", "readiness", "--source", "bjnews")
        result = self.command("report", 8)
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["open_span_ids"], ["bj"])
        self.assertEqual(result["intervals"][0]["seconds"], 6)
        self.assertEqual(result["intervals"][0]["status"], "open")
        self.assertIsNone(result["request_to_delivery_seconds"])

    def test_abort_keeps_incomplete_span(self):
        self.init()
        self.command("start", 1, "--id", "bj", "--phase", "readiness", "--source", "bjnews")
        self.command("finish", 6, "--outcome", "delivered", fails=True)
        self.command("finish", 6, "--outcome", "aborted")
        result = self.command("report", 8)
        self.assertEqual(result["elapsed_seconds"], 6)
        self.assertEqual(result["incomplete_span_ids"], ["bj"])
        self.assertEqual(result["open_span_ids"], [])
        self.assertEqual(result["intervals"][0]["status"], "incomplete")

    def test_overlaps_duplicates_wrong_ids_and_run_ids_rejected(self):
        self.init()
        self.command("init", 1, fails=True)
        self.command("end", 1, "--id", "missing", fails=True)
        self.command("start", 1, "--id", "a", "--phase", "collect")
        self.command("start", 2, "--id", "b", "--phase", "collect", fails=True)
        self.command("end", 2, "--id", "b", fails=True)
        self.command("end", 2, "--id", "a", fails=True, run_id="other")
        self.command("end", 3, "--id", "a")
        self.command("start", 4, "--id", "a", "--phase", "collect", fails=True)
        self.command("finish", 5, "--outcome", "delivered")
        self.command("start", 6, "--id", "b", "--phase", "collect", fails=True)
        self.command("finish", 6, "--outcome", "delivered", fails=True)

    def test_out_of_order_clock_and_test_live_mixing(self):
        self.init()
        self.command("start", 3, "--id", "a", "--phase", "collect")
        self.command("end", 2, "--id", "a", fails=True)
        self.command("report", 2, fails=True)
        self.command("end", 5, "--id", "a", fails=True, live=True)
        self.command("report", 5, fails=True, live=True)

    def test_live_clock_is_timezone_aware(self):
        result = self.command("init", live=True)
        self.assertIsNotNone(timing.timestamp(result["at"]).utcoffset())
        self.assertEqual(timing.read_events(self.path)[0]["clock"], "live")
        self.command("report", 5, fails=True)

    def test_bad_timestamps_and_identifiers(self):
        for value in ("2026-09-04T10:00:00", "bad", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                timing.timestamp(value)
        self.command("init", 0, "--request-at", "2026-09-04T10:00:01+08:00", fails=True)
        self.command("init", 0, "--request-at", "2026-09-04T10:00:00", fails=True)
        self.init()
        self.command("start", 1, "--id", "../../x", "--phase", "collect", fails=True)
        self.command("start", 1, "--id", "a", "--phase", "", fails=True)

    def test_equal_instants_and_timezone_offsets(self):
        self.init("--request-at", "2026-09-04T02:00:00Z")
        self.command("start", 0, "--id", "a", "--phase", "collect")
        self.command("end", 0, "--id", "a")
        result = self.command("report", 0)
        self.assertEqual(result["elapsed_seconds"], 0)
        self.assertEqual(result["intervals"][0]["seconds"], 0)

    def test_truncated_corrupt_and_unexpected_fields_rejected(self):
        self.init()
        original = self.path.read_text()
        for corrupted in (original.rstrip(), "invalid\n", "[]\n", original.replace('"version":1', '"version":true'),
                          original.replace('"event":"init"', '"event":"init","extra":1')):
            with self.subTest(corrupted=corrupted):
                self.path.write_text(corrupted)
                self.command("report", 1, fails=True)
                self.command("start", 1, "--id", "a", "--phase", "collect", fails=True)
        self.path.write_text(original)
        self.command("report", 1)

    def test_lock_and_atomic_replace_failure_preserve_original(self):
        self.init()
        lock = self.path.with_name(self.path.name + ".lock")
        lock.touch()
        self.command("start", 1, "--id", "a", "--phase", "collect", fails=True)
        self.assertTrue(lock.exists())
        lock.unlink()
        with patch.object(timing.os, "replace", side_effect=OSError("simulated replace failure")):
            self.command("start", 1, "--id", "a", "--phase", "collect", fails=True)
        self.assertFalse(lock.exists())
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])
        self.command("start", 1, "--id", "a", "--phase", "collect")

    def test_path_guards(self):
        with self.assertRaises(ValueError):
            timing.safe_path(timing.SKILL_ROOT / "timing.jsonl")
        with self.assertRaises(ValueError):
            timing.safe_path(self.path.with_suffix(".md"))
        with self.assertRaises(ValueError):
            timing.safe_path(self.path.parent / "missing" / "timing.jsonl")
        target = self.path.parent / "target.jsonl"
        target.touch()
        self.path.symlink_to(target)
        self.command("init", 0, fails=True)
        self.assertEqual(target.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
