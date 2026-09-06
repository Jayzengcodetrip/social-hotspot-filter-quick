import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from capture_io import (MARKER, assemble, import_capture, init_run, parse_time,
                        session_envelopes, utc_now)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "thread.jsonl"
        self.log.write_text('{"type":"old_data"}\n')
        self.manifest = init_run(self.root / "run", "test-run", self.log)

    def envelope(self, part=1, parts=1):
        return {"capture_schema": 1, "run_id": "test-run", "source": "bjnews",
                "token": self.manifest["tokens"]["bjnews"], "part": part, "parts": parts,
                "captured_at": utc_now().isoformat(),
                "rows": [{"title": '原题「不改」| 引号 " 空格',
                          "url": "https://www.bjnews.com.cn/detail/1.html?a=1&b=2"}]}

    def append(self, envelope, kind="function_call_output"):
        value = {"timestamp": utc_now().isoformat(), "type": "response_item",
                 "payload": {"type": kind, "output": [{"type": "text", "text": MARKER + json.dumps(envelope, ensure_ascii=False)}]}}
        with self.log.open("a") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    def test_log_transport_exact_no_retyping(self):
        envelope = self.envelope()
        self.append(envelope)
        result = import_capture(self.root / "run", "bjnews", {"scope": "observed"})
        saved = json.loads(Path(result["path"]).read_text())
        self.assertEqual(saved["rows"], envelope["rows"])
        self.assertEqual(saved["collection"], {"scope": "observed"})

    def test_parts_sort_without_dropping_or_reordering_rows(self):
        first, second = self.envelope(1, 2), self.envelope(2, 2)
        second["rows"][0]["title"] = "第二条"
        _, rows, _ = assemble(self.manifest, "bjnews", [second, first, copy.deepcopy(first)])
        self.assertEqual(rows, first["rows"] + second["rows"])

    def test_missing_or_conflicting_chunk_fails(self):
        first = self.envelope(1, 2)
        with self.assertRaisesRegex(ValueError, "missing"):
            assemble(self.manifest, "bjnews", [first])
        other = copy.deepcopy(first)
        other["rows"][0]["title"] = "冲突"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            assemble(self.manifest, "bjnews", [first, other])

    def test_old_or_other_run_or_other_source_not_used(self):
        for field, value in [("run_id", "old-run"), ("source", "caixin"), ("token", "wrong")]:
            env = self.envelope()
            env[field] = value
            with self.assertRaisesRegex(ValueError, "missing"):
                assemble(self.manifest, "bjnews", [env])
        env = self.envelope()
        env["captured_at"] = (parse_time(self.manifest["started_at"]) - timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "outside"):
            assemble(self.manifest, "bjnews", [env])

    def test_non_output_records_not_inspected(self):
        for kind in ["message", "reasoning", "function_call", "custom_tool_call"]:
            self.append(self.envelope(), kind)
        self.assertEqual(list(session_envelopes(self.manifest)), [])

    def test_incomplete_log_append_is_not_fabricated(self):
        with self.log.open("a") as stream:
            stream.write('{"type":')
        self.assertEqual(list(session_envelopes(self.manifest)), [])

    def test_log_truncation_fails(self):
        self.log.write_text("")
        with self.assertRaisesRegex(ValueError, "truncated"):
            list(session_envelopes(self.manifest))

    def test_capture_is_immutable(self):
        self.append(self.envelope())
        import_capture(self.root / "run", "bjnews", {})
        with self.assertRaisesRegex(ValueError, "already exists"):
            import_capture(self.root / "run", "bjnews", {})

    def test_portable_export_is_same_data(self):
        env = self.envelope()
        exported = self.root / "browser-output.txt"
        exported.write_text(MARKER + json.dumps(env, ensure_ascii=False) + "\n")
        result = import_capture(self.root / "run", "bjnews", {}, exported)
        self.assertEqual(json.loads(Path(result["path"]).read_text())["rows"], env["rows"])

    def test_refuse_artifacts_inside_skill(self):
        skill = self.root / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("example")
        with self.assertRaisesRegex(ValueError, "outside"):
            init_run(skill / "run", "wrong")

    def test_reject_wrong_types_and_timestampless_rows(self):
        for field, value in [("capture_schema", True), ("part", True), ("parts", 0), ("captured_at", "2026-09-04T12:00:00"), ("rows", [])]:
            env = self.envelope()
            env[field] = value
            with self.assertRaises(ValueError):
                assemble(self.manifest, "bjnews", [env])


if __name__ == "__main__":
    unittest.main()
