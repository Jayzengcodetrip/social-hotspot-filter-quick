#!/usr/bin/env python3
"""Reject report tampering while accepting the complete, research-free quick scope."""

from __future__ import annotations

import copy
import unittest

from _core_link_rules import REQUIRED_LINK_SOURCE_IDS, SOURCE_BASE_URLS
from _test_fixtures import make_ledger, regroup_positions, source_items, refresh_review, refresh_runtime_evidence
from generate_report import build_ranking_payload, generate_report
from validate_report import validate_report


class ReportValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = make_ledger()
        errors, ranking, _ = build_ranking_payload(self.ledger, 10)
        self.assertEqual(errors, [])
        self.report, errors = generate_report(self.ledger, ranking)
        self.assertEqual(errors, [])

    def assert_rejected(self, report: str, ledger: dict | None = None) -> None:
        self.assertTrue(validate_report(ledger or self.ledger, report, 10))

    def test_unmodified_quick_report_passes(self) -> None:
        self.assertEqual(validate_report(self.ledger, self.report, 10), [])

    def test_altered_coverage_or_hit_breakdown_fails(self) -> None:
        for replacement in ("9（微博、知乎）", "2（微博、百度）"):
            with self.subTest(replacement=replacement):
                self.assert_rejected(self.report.replace("2（微博、知乎）", replacement))
        self.assert_rejected(self.report.replace("2（微博1、知乎1）", "3（微博2、知乎1）"))

    def test_changed_original_title_or_url_fails(self) -> None:
        item = self.ledger["clusters"][0]["items"][0]
        self.assert_rejected(self.report.replace(item["title"], "擅自改写的标题"))
        self.assert_rejected(self.report.replace(item["url"], "https://s.weibo.com/weibo?q=wrong"))

    def test_changed_compact_completeness_fails(self) -> None:
        self.assert_rejected(self.report.replace("微博50/50", "微博25/50"))

    def test_changed_source_time_or_browser_fails(self) -> None:
        self.assert_rejected(self.report.replace("09:00-09:10", "08:00-08:10"))
        self.assert_rejected(self.report.replace("用户连接的 Chrome", "虚构浏览器"))

    def test_missing_or_misleading_version_note_fails(self) -> None:
        self.assert_rejected(self.report.replace("不开展额外补充检索与事实核实", "已完成独立事实核实"))
        self.assert_rejected(self.report.replace("- 版本说明：", "- Google补充核实："))

    def test_full_edition_title_fails(self) -> None:
        self.assert_rejected(self.report.replace("Top 10（快速版）", "Top 10"))

    def test_extra_sections_or_unheaded_summary_fail(self) -> None:
        for suffix in (
            "\n### 事件概述\n\n这是事件叙述。",
            "\n### 补充核实来源\n\n无",
            "\n## 核心来源采集完整性\n\n额外表格",
            "\n这是一段没有标题的额外事件概述。",
            "\n## 认知价值\n\n评价",
        ):
            with self.subTest(suffix=suffix):
                self.assert_rejected(self.report + suffix)

    def test_missing_original_heading_fails(self) -> None:
        self.assert_rejected(self.report.replace("### 各平台原始标题", ""))

    def test_duplicate_or_wrong_rank_heading_fails(self) -> None:
        self.assert_rejected(self.report.replace("## 1. ", "## 2. "))
        topic = self.report[self.report.index("## 1. "):]
        self.assert_rejected(self.report + "\n" + topic)

    def test_no_topic_claim_cannot_replace_selected_report(self) -> None:
        self.assert_rejected(self.report + "\n当前没有话题同时出现在至少两个不同核心域名。\n")

    def test_missing_required_link_is_checked_independently(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        ledger["clusters"][0]["items"][0]["url"] = None
        self.assert_rejected(self.report, ledger)

    def test_all_seven_sources_reject_cross_host_or_entry_links(self) -> None:
        ledger = make_ledger()
        regroup_positions(ledger, "七平台链接", [(source, 1) for source in REQUIRED_LINK_SOURCE_IDS])
        refresh_review(ledger)
        _, ranking, _ = build_ranking_payload(ledger, 10)
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        for source in REQUIRED_LINK_SOURCE_IDS:
            for url in (SOURCE_BASE_URLS[source], "https://unrelated.example/news/1"):
                with self.subTest(source=source, url=url):
                    case = copy.deepcopy(ledger)
                    next(item for item in source_items(case, source) if item["display_order"] == 1)["url"] = url
                    self.assertTrue(validate_report(case, report, 10))

    def test_incomplete_source_cannot_pass_final_validator(self) -> None:
        for source in ("weibo", "zhihu", "bilibili", "baidu", "douyin", "jiemian", "bjnews", "caixin", "people"):
            with self.subTest(source=source):
                case = copy.deepcopy(self.ledger)
                next(row for row in case["sources"] if row["id"] == source)["collection"]["end_reached"] = False
                self.assert_rejected(self.report, case)

    def test_legitimate_original_title_may_contain_summary_words(self) -> None:
        ledger = make_ledger()
        ledger["clusters"][0]["items"][0]["title"] = "媒体介绍事件概述与Google搜索功能"
        refresh_runtime_evidence(ledger)
        refresh_review(ledger)
        _, ranking, _ = build_ranking_payload(ledger, 10)
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        self.assertEqual(validate_report(ledger, report, 10), [])


if __name__ == "__main__":
    unittest.main()
