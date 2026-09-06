#!/usr/bin/env python3
"""Quick-edition generation, failure gates, and standalone CLI tests."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _test_fixtures import (
    SOURCE_IDS, make_ledger, refresh_source_collection, regroup_positions, source_items,
    refresh_review, refresh_runtime_evidence,
)
from _core_link_rules import REQUIRED_LINK_SOURCE_IDS
from _report_utils import NO_TOPIC_SENTENCE, VERSION_NOTE
from generate_report import build_ranking_payload, generate_report
from validate_report import validate_report


class ReportGeneratorTests(unittest.TestCase):
    def ranking(self, ledger: dict, limit: int = 10) -> dict:
        # These output tests model fresh synthetic captures after fixture edits.
        # The separate contract tests exercise stale evidence/review rejection.
        refresh_runtime_evidence(ledger, reset_raw=True)
        refresh_review(ledger)
        errors, ranking, _ = build_ranking_payload(ledger, limit)
        self.assertEqual(errors, [])
        return ranking

    def test_quick_report_needs_no_google_or_summary_fields(self) -> None:
        ledger = make_ledger()
        self.assertNotIn("google_status", ledger["run"])
        self.assertNotIn("verification", ledger["clusters"][0])
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertEqual(validate_report(ledger, report, 10), [])
        self.assertTrue(report.startswith("# 当日跨来源共同热点 Top 10（快速版）"))
        self.assertIn(VERSION_NOTE, report)
        self.assertNotIn("### 补充核实来源", report)
        self.assertNotIn("### 事件概述", report)
        self.assertNotIn("- Google补充核实：", report)

    def test_completeness_is_compact_and_exact(self) -> None:
        ledger = make_ledger()
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertIn(
            "微博50/50、知乎30/30、B站30/30、百度50/50、抖音50/50；"
            "界面新闻2条（页脚稳定）、新京报2条（页脚稳定）、"
            "财新2条（页脚稳定）；人民日报2条（当日版面2/2）；九个来源均通过",
            report,
        )
        self.assertNotIn("## 核心来源采集完整性", report)

    def test_actual_exclusions_are_disclosed(self) -> None:
        ledger = make_ledger()
        item = next(item for item in source_items(ledger, "weibo") if item["display_order"] == 40)
        item["eligible"] = False
        item["exclusion_reason"] = "spam_or_unidentifiable"
        refresh_source_collection(ledger, "weibo")
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertIn("微博50/50（有效49、排除1）", report)

    def test_missing_selected_link_blocks_generation_for_all_seven_sources(self) -> None:
        base = make_ledger()
        regroup_positions(base, "七平台链接测试", [(source, 1) for source in REQUIRED_LINK_SOURCE_IDS])
        for source in REQUIRED_LINK_SOURCE_IDS:
            with self.subTest(source=source):
                ledger = copy.deepcopy(base)
                item = next(item for item in source_items(ledger, source) if item["display_order"] == 1)
                item["url"] = None
                report, errors = generate_report(ledger, self.ranking(ledger))
                self.assertEqual(report, "")
                self.assertTrue(any("requires a non-empty" in e for e in errors), errors)

    def test_bilibili_douyin_plain_titles_are_kept(self) -> None:
        ledger = make_ledger()
        cluster = regroup_positions(ledger, "B站抖音讨论", [("bilibili", 1), ("douyin", 1)])
        for item in cluster["items"]:
            item["url"] = None
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertIn("  - bilibili测试标题1", report)
        self.assertIn("  - douyin测试标题1", report)

    def test_nonselected_missing_links_do_not_trigger_extra_link_pass(self) -> None:
        ledger = make_ledger()
        for cluster in ledger["clusters"][1:]:
            for item in cluster["items"]:
                item["url"] = None
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertTrue(report)

    def test_stale_ranking_is_rejected(self) -> None:
        ledger = make_ledger()
        ranking = self.ranking(ledger)
        ranking["selected"][0]["hit_count"] += 1
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(report, "")
        self.assertTrue(any("does not exactly match" in e for e in errors))

    def test_unchanged_scores_do_not_allow_ranking_from_a_different_ledger(self) -> None:
        ledger = make_ledger()
        ranking = self.ranking(ledger)
        ledger["run"]["collection_time"] = "2026-08-06 09:20-09:30 CST"
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(report, "")
        self.assertTrue(any("does not exactly match" in e for e in errors))

    def test_competition_ties_and_strict_limit(self) -> None:
        ledger = make_ledger()
        regroup_positions(ledger, "第二个并列话题", [("weibo", 10), ("zhihu", 10)])
        ranking = self.ranking(ledger)
        self.assertEqual([e["rank"] for e in ranking["selected"]], [1, 1])
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        self.assertIn("## 1. 第二个并列话题", report)
        ranking = self.ranking(ledger, 1)
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        self.assertEqual(len(ranking["selected"]), 1)
        self.assertIn("Top 1（快速版）", report)
        self.assertNotIn("第二个并列话题", report)

    def test_no_topic_result_is_not_padding(self) -> None:
        ledger = make_ledger()
        regroup_positions(ledger, "拆分为单来源", [("weibo", 1)])
        ranking = self.ranking(ledger)
        self.assertEqual(ranking["selected"], [])
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        self.assertEqual(report.count(NO_TOPIC_SENTENCE), 1)
        self.assertNotIn("| 排名 |", report)
        self.assertNotIn("### 各平台原始标题", report)

    def test_unicode_quotes_and_markdown_brackets_keep_exact_titles(self) -> None:
        ledger = make_ledger()
        ledger["clusters"][0]["normalized_title"] = '话题“原话” | [说明]'
        ledger["clusters"][0]["display_title"] = '话题“原话” | [说明]'
        ledger["clusters"][0]["items"][0]["title"] = '原文“弯引号”与"直引号" [细节]'
        report, errors = generate_report(ledger, self.ranking(ledger))
        self.assertEqual(errors, [])
        self.assertIn(r'原文“弯引号”与"直引号" \[细节\]', report)
        self.assertIn(r'话题“原话” \| [说明]', report)

    def test_invalid_limit_is_rejected(self) -> None:
        for limit in (0, -1, True, "10", None):
            with self.subTest(limit=limit):
                report, errors = generate_report(make_ledger(), {"limit": limit})
                self.assertEqual(report, "")
                self.assertTrue(errors)

    def test_four_command_cli_and_link_normalizer(self) -> None:
        scripts = Path(__file__).resolve().parent
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_path, ranking_path, report_path = (
                root / "ledger.json", root / "ranking.json", root / "report.md"
            )
            ledger = make_ledger()
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            commands = [
                ["normalize_core_link.py", "weibo", "/weibo?q=test"],
                ["validate_ranking.py", str(ledger_path), "--output", str(ranking_path)],
                ["validate_selected_links.py", str(ledger_path)],
                ["generate_report.py", str(ledger_path), str(ranking_path), "--output", str(report_path)],
                ["validate_report.py", str(ledger_path), str(report_path)],
            ]
            for name, *args in commands:
                result = subprocess.run(
                    [sys.executable, str(scripts / name), *args],
                    cwd=tmp, env=env, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, (name, result.stdout, result.stderr))
            self.assertEqual(
                validate_report(ledger, report_path.read_text(encoding="utf-8"), 10), []
            )
            # A failure must not overwrite an existing good report.
            before = report_path.read_bytes()
            ledger["clusters"][0]["items"][0]["url"] = None
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(scripts / "generate_report.py"), str(ledger_path),
                 str(ranking_path), "--output", str(report_path)],
                cwd=tmp, env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
