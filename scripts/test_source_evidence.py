"""Synthetic replay/ingestion tests, never live browser evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from _runtime_test_fixtures import fixture_evidence, fixture_trace
from capture_io import MARKER, init_run
from finish_source import finish_source
from source_evidence import make_runtime_evidence, raw_rows_sha256, verify_runtime_evidence


def test_rows(count=2):
    return [{"title": f"Synthetic 原始标题 {i}", "url": None if i == 0 else f"/article/{i}"} for i in range(count)]


class SourceEvidenceTests(unittest.TestCase):
    def test_all_nine_replay_without_network_or_retyped_titles(self):
        for source, count in [("weibo", 50), ("zhihu", 30), ("bilibili", 30), ("baidu", 50), ("douyin", 50),
                              ("jiemian", 2), ("bjnews", 2), ("caixin", 2), ("people", 2)]:
            with self.subTest(source=source):
                rows = test_rows(count)
                evidence, summary = fixture_evidence(source, rows)
                self.assertTrue(summary["end_reached"])
                self.assertEqual(summary["discovered_count"], count)
                self.assertEqual(summary, verify_runtime_evidence(evidence, source, "fixture-run", rows, "2026-08-06"))

    def test_missing_evidence_is_not_replaced_by_claimed_summary(self):
        for evidence in [None, {}, {"evidence_schema": 1, "complete": True}]:
            with self.assertRaises(ValueError):
                verify_runtime_evidence(evidence, "caixin", "fixture-run", test_rows(), "2026-08-06")

    def test_mutated_raw_title_url_order_are_all_detected(self):
        rows = test_rows()
        evidence, _ = fixture_evidence("caixin", rows)
        for changed in [[rows[1], rows[0]], [dict(rows[0], title="Edited"), rows[1]], [dict(rows[0], url="https://invented.example"), rows[1]]]:
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                verify_runtime_evidence(evidence, "caixin", "fixture-run", changed, "2026-08-06")

    def test_stale_cross_run_cross_source_trace_rejected(self):
        rows = test_rows()
        evidence, _ = fixture_evidence("caixin", rows)
        for change in [{"run_id": "other"}, {"source": "bjnews"}, {"captured_at": "2026-08-05T12:01:00+08:00"}]:
            changed = deepcopy(evidence)
            changed["trace"].update(change)
            with self.assertRaises(ValueError):
                verify_runtime_evidence(changed, "caixin", "fixture-run", rows, "2026-08-06")

    def test_new_run_cannot_reuse_older_capture_date(self):
        rows = test_rows()
        evidence, _ = fixture_evidence("caixin", rows)
        with self.assertRaisesRegex(ValueError, "capture date"):
            verify_runtime_evidence(evidence, "caixin", "fixture-run", rows, "2026-08-07")

    def test_invented_long_observation_clock_exceeds_run_duration(self):
        rows = test_rows()
        evidence, _ = fixture_evidence("caixin", rows)
        evidence["capture_started_at"] = "2026-08-06T12:00:59+08:00"
        with self.assertRaisesRegex(ValueError, "duration exceeds"):
            verify_runtime_evidence(evidence, "caixin", "fixture-run", rows, "2026-08-06")

    def test_claimed_complete_does_not_hide_missing_round_or_early_final(self):
        rows = test_rows()
        trace = fixture_trace("caixin", rows)
        for observations in [trace["observations"][-1:], trace["observations"][:2] + trace["observations"][-1:]]:
            changed = dict(trace, observations=observations, complete=True, noNewItemRounds=2)
            with self.assertRaisesRegex(ValueError, "homepage_incomplete"):
                make_runtime_evidence(changed, "caixin", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")

    def test_final_capture_order_must_be_actual_final_page_order(self):
        rows = test_rows()
        trace = fixture_trace("caixin", rows)
        second_sample = deepcopy(trace["observations"][0])
        second_sample["timestampMs"] = 200
        second_sample["readinessEvidence"]["observedAtMs"] = 200
        second_sample["orderEvidence"]["observedAtMs"] = 200
        trace["observations"][0]["itemIndices"] = [1]
        trace["observations"].insert(1, second_sample)
        evidence, summary = make_runtime_evidence(trace, "caixin", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")
        self.assertEqual(summary["last_item_title"], rows[1]["title"])
        trace["observations"][-1]["orderEvidence"]["itemIndices"] = [1, 0]
        with self.assertRaisesRegex(ValueError, "full_dom_order|final_observed_order"):
            make_runtime_evidence(trace, "caixin", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")

    def test_homepage_section_is_never_a_valid_source_even_as_accepted_redirect(self):
        rows = test_rows()
        trace = fixture_trace("caixin", rows)
        for observation in trace["observations"]:
            context = observation["context"]
            context.update({"topLevelUrl": "https://www.caixin.com/news/", "documentUrl": "https://www.caixin.com/news/",
                            "acceptedMainUrls": ["https://www.caixin.com/news/"], "homepageIdentityEvidence": "Fixture claim"})
        with self.assertRaisesRegex(ValueError, "not_section"):
            make_runtime_evidence(trace, "caixin", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")

    def test_observed_index_html_homepage_redirect_supported_with_identity_evidence(self):
        rows = test_rows()
        trace = fixture_trace("caixin", rows)
        for observation in trace["observations"]:
            observation["context"].update({"topLevelUrl": "https://www.caixin.com/index.html", "documentUrl": "https://www.caixin.com/index.html",
                "acceptedMainUrls": ["https://www.caixin.com/index.html"], "homepageIdentityEvidence": "Fixture observed homepage identity"})
        _, summary = make_runtime_evidence(trace, "caixin", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")
        self.assertTrue(summary["end_reached"])

    def test_people_missing_page_and_wrong_date_rejected(self):
        rows = test_rows()
        for change in [{"totalPages": 2}, {"date": "2026-08-05"}]:
            trace = fixture_trace("people", rows)
            trace["issue"].update(change)
            with self.assertRaisesRegex(ValueError, "issue_index"):
                make_runtime_evidence(trace, "people", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")

    def test_fixed_list_missing_tail_or_count_is_not_complete(self):
        for count in [49, 50]:
            rows = test_rows(count)
            trace = fixture_trace("weibo", rows)
            trace["observations"][-1].pop("listEnd")
            with self.assertRaises(ValueError):
                make_runtime_evidence(trace, "weibo", "fixture-run", rows, "2026-08-06T12:00:00+08:00", run_date="2026-08-06")

    def test_missing_node_is_actionable_and_never_skips_guard(self):
        rows = test_rows()
        evidence, _ = fixture_evidence("caixin", rows)
        with self.assertRaisesRegex(ValueError, "could not run"):
            verify_runtime_evidence(evidence, "caixin", "fixture-run", rows, "2026-08-06", node="/nonexistent/synthetic/node")


class FinishSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shf-finish-source-test-")
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        with patch("capture_io.utc_now", return_value=self.now - timedelta(minutes=1)):
            self.manifest = init_run(self.root, "fixture-run")
        self.rows = test_rows()
        self.trace = fixture_trace("caixin", self.rows, captured_at=self.now.isoformat(), token=self.manifest["tokens"]["caixin"])
        envelope = {"capture_schema": 1, "run_id": "fixture-run", "source": "caixin", "token": self.manifest["tokens"]["caixin"],
                    "part": 1, "parts": 1, "captured_at": self.now.isoformat(), "rows": self.rows}
        self.export = self.root / "actual-synthetic-tool-output.txt"
        self.export.write_text(MARKER + json.dumps(envelope, ensure_ascii=False) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_finish_source_derives_collection_and_preserves_raw(self):
        result = finish_source(self.root, "caixin", self.trace, self.export)
        packet = json.loads(Path(result["path"]).read_text())
        self.assertEqual(packet["rows"], self.rows)
        self.assertTrue(result["complete"])
        self.assertEqual(packet["collection"]["runtime_evidence"]["raw_rows_sha256"], raw_rows_sha256(self.rows))
        self.assertNotIn("eligible_count", packet["collection"])
        self.assertTrue(finish_source(self.root, "caixin", self.trace, self.export)["already_finished"])

    def test_interrupted_pair_write_is_resumable_but_conflicts_are_preserved(self):
        import finish_source as module
        original_create = module.create_json
        def interrupted(path, value):
            if path.parent.name == "captures":
                raise OSError("Synthetic disk interruption")
            return original_create(path, value)
        with patch("finish_source.create_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                finish_source(self.root, "caixin", self.trace, self.export)
        self.assertTrue((self.root / "evidence" / "caixin.json").exists())
        self.assertFalse((self.root / "captures" / "caixin.json").exists())
        self.assertTrue(finish_source(self.root, "caixin", self.trace, self.export)["complete"])
        packet = self.root / "captures" / "caixin.json"
        packet.write_text('{"synthetic": "conflicting original"}', encoding="utf-8")
        before = packet.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflicting content"):
            finish_source(self.root, "caixin", self.trace, self.export)
        self.assertEqual(packet.read_bytes(), before)

    def test_invalid_trace_creates_no_immutable_capture(self):
        self.trace["observations"] = self.trace["observations"][-1:]
        with self.assertRaises(ValueError):
            finish_source(self.root, "caixin", self.trace, self.export)
        self.assertFalse((self.root / "captures" / "caixin.json").exists())
        self.assertFalse((self.root / "evidence" / "caixin.json").exists())

    def test_mismatched_token_and_time_are_rejected(self):
        for change in [{"token": "wrong"}, {"captured_at": (self.now - timedelta(seconds=1)).isoformat()}]:
            with self.assertRaises(ValueError):
                finish_source(self.root, "caixin", dict(self.trace, **change), self.export)

    def test_cli_produces_compact_summary(self):
        path = self.root / "trace.json"
        path.write_text(json.dumps(self.trace), encoding="utf-8")
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("finish_source.py")), str(self.root), "caixin", "--trace", str(path),
                                 "--export-file", str(self.export)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["rows"], 2)
        self.assertNotIn("trace", summary)


if __name__ == "__main__":
    unittest.main()
