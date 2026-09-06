#!/usr/bin/env python3
"""Contract tests for browser preflight recovery instructions."""

from __future__ import annotations

import unittest
from pathlib import Path


SKILL_TEXT = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
    encoding="utf-8"
)


class SkillContractTests(unittest.TestCase):
    def test_transient_failures_receive_four_total_attempts(self) -> None:
        self.assertIn("initial attempt plus up to three automatic retries", SKILL_TEXT)
        self.assertIn("5, 15, and 30 seconds", SKILL_TEXT)

    def test_user_action_blocks_do_not_consume_transient_retry_loop(self) -> None:
        self.assertIn("login wall, captcha, explicit access denial", SKILL_TEXT)
        self.assertIn("pause immediately", SKILL_TEXT)

    def test_interactive_recovery_restarts_coherent_preflight(self) -> None:
        self.assertIn("restart the nine-source preflight from the first source", SKILL_TEXT)
        self.assertIn("one fresh four-attempt budget", SKILL_TEXT)

    def test_client_rendering_waits_before_consuming_retry(self) -> None:
        self.assertIn("Separate navigation completion from page-data readiness", SKILL_TEXT)
        self.assertIn("post-navigation readiness phase", SKILL_TEXT)
        self.assertIn("does not consume a retry attempt", SKILL_TEXT)
        self.assertIn("inter-attempt backoff intervals", SKILL_TEXT)

    def test_dynamic_hotlists_have_positive_readiness_signals(self) -> None:
        self.assertIn("For Bilibili, wait for visible numbered topic titles", SKILL_TEXT)
        self.assertIn("excluding any unnumbered pinned item", SKILL_TEXT)
        self.assertIn("For Douyin, wait for visible topic rows", SKILL_TEXT)
        self.assertIn("current `更新于` timestamp", SKILL_TEXT)

    def test_ranking_is_coverage_first_with_competition_ties(self) -> None:
        self.assertIn(
            "Rank every remaining cluster by domain coverage in descending order",
            SKILL_TEXT,
        )
        self.assertIn("assign the same standard competition rank", SKILL_TEXT)
        self.assertIn("a tied rank does not automatically expand", SKILL_TEXT)

    def test_fixed_hotlists_have_hard_complete_counts(self) -> None:
        self.assertIn("Weibo `50`, Zhihu `30`, Bilibili `30`, Baidu `50`, and Douyin `50`", SKILL_TEXT)
        self.assertIn("display_order` covers `1..N` exactly once", SKILL_TEXT)

    def test_variable_news_sources_require_end_evidence(self) -> None:
        self.assertIn("Scroll to the footer", SKILL_TEXT)
        self.assertIn("at least two consecutive scroll/load rounds", SKILL_TEXT)
        self.assertIn("read pages to equal total pages", SKILL_TEXT)

    def test_report_compacts_source_completeness_into_source_note(self) -> None:
        self.assertIn("成功直读来源及完整性结果", SKILL_TEXT)
        self.assertIn("Do not render a separate source-completeness heading or table", SKILL_TEXT)
        self.assertIn("微博50/50", SKILL_TEXT)
        self.assertIn("九个来源均通过", SKILL_TEXT)
        self.assertIn('report_mode: "quick"', SKILL_TEXT)

    def test_selected_links_are_required_for_seven_sources_only(self) -> None:
        self.assertIn("Weibo, Zhihu, Baidu, Jiemian News, The Beijing News, Caixin, and People's Daily", SKILL_TEXT)
        self.assertIn("Bilibili and Douyin are explicitly exempt", SKILL_TEXT)
        self.assertIn("validate_selected_links.py", SKILL_TEXT)


if __name__ == "__main__":
    unittest.main()
