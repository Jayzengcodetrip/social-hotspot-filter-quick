#!/usr/bin/env python3
"""Regression tests for the canonical ranking validator."""

from __future__ import annotations

import copy
import unittest

from _test_fixtures import (
    SOURCE_IDS,
    make_ledger,
    refresh_source_collection,
    remove_source_position,
    source_items,
    refresh_review,
    refresh_runtime_evidence,
)
from validate_ranking import validate_and_rank


def hotlist_item(item_id: str, source: str, title: str, position: int) -> dict:
    return {
        "id": item_id,
        "source": source,
        "title": title,
        "url": f"https://{source}.example/{item_id}",
        "surface": "hotlist",
        "display_order": position,
        "hotlist_position": position,
        "eligible": True,
        "exclusion_reason": None,
        "publication_date": None,
    }


class RankingValidatorTests(unittest.TestCase):
    def test_valid_ledger_qualifies_cross_domain_cluster(self) -> None:
        errors, qualified, selected = validate_and_rank(
            make_ledger(complete_sources=False),
            10,
            enforce_source_completeness=False,
        )
        self.assertEqual(errors, [])
        self.assertEqual([entry["id"] for entry in qualified], ["seven-entities"])
        self.assertEqual([entry["id"] for entry in selected], ["seven-entities"])

    def test_domain_coverage_precedes_hit_count(self) -> None:
        ledger = make_ledger(complete_sources=False)
        ledger["clusters"][0]["items"].extend(
            [
                hotlist_item("weibo-003", "weibo", "名单措施后续一", 3),
                hotlist_item("zhihu-004", "zhihu", "名单措施后续二", 4),
            ]
        )
        ledger["clusters"].append(
            {
                "id": "broader-coverage",
                "normalized_title": "覆盖更广但命中更少的话题",
                "items": [
                    hotlist_item("weibo-005", "weibo", "广覆盖话题一", 5),
                    hotlist_item("zhihu-006", "zhihu", "广覆盖话题二", 6),
                    hotlist_item("bilibili-001", "bilibili", "广覆盖话题三", 1),
                ],
            }
        )
        errors, qualified, _ = validate_and_rank(
            ledger, 10, enforce_source_completeness=False
        )
        self.assertEqual(errors, [])
        self.assertEqual(qualified[0]["id"], "broader-coverage")
        self.assertEqual((qualified[0]["coverage"], qualified[0]["hit_count"]), (3, 3))
        self.assertEqual((qualified[1]["coverage"], qualified[1]["hit_count"]), (2, 4))

    def test_equal_coverage_and_hits_share_competition_rank(self) -> None:
        ledger = make_ledger(complete_sources=False)
        ledger["clusters"][0]["items"].append(
            hotlist_item("bilibili-001", "bilibili", "首个并列话题三", 1)
        )
        ledger["clusters"].extend(
            [
                {
                    "id": "second-tied-topic",
                    "normalized_title": "第二个并列话题",
                    "items": [
                        hotlist_item("weibo-003", "weibo", "第二个并列话题一", 3),
                        hotlist_item("zhihu-004", "zhihu", "第二个并列话题二", 4),
                        hotlist_item("bilibili-002", "bilibili", "第二个并列话题三", 2),
                    ],
                },
                {
                    "id": "lower-topic",
                    "normalized_title": "覆盖较少的话题",
                    "items": [
                        hotlist_item("baidu-001", "baidu", "覆盖较少话题一", 1),
                        hotlist_item("douyin-001", "douyin", "覆盖较少话题二", 1),
                    ],
                },
            ]
        )
        errors, qualified, selected = validate_and_rank(
            ledger, 1, enforce_source_completeness=False
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            [entry["id"] for entry in qualified],
            ["seven-entities", "second-tied-topic", "lower-topic"],
        )
        self.assertEqual([entry["rank"] for entry in qualified], [1, 1, 3])
        self.assertEqual([entry["id"] for entry in selected], ["seven-entities"])

    def test_confirmed_example_metrics_produce_expected_order_and_ranks(self) -> None:
        ledger = make_ledger(complete_sources=False)
        ledger["clusters"] = []
        next_order = {source_id: 0 for source_id in SOURCE_IDS}
        metrics = [
            ("current-1", 5, 12),
            ("current-2", 9, 9),
            ("current-3", 3, 8),
            ("current-4", 4, 7),
            ("current-5", 3, 6),
            ("current-6", 4, 5),
            ("current-7", 3, 4),
            ("current-8", 3, 4),
            ("current-9", 3, 3),
            ("current-10", 3, 3),
        ]
        hotlist_sources = set(SOURCE_IDS[:5])
        for cluster_id, coverage, hit_count in metrics:
            covered_sources = SOURCE_IDS[:coverage]
            items = []
            for hit_index in range(hit_count):
                source_id = covered_sources[hit_index % coverage]
                next_order[source_id] += 1
                display_order = next_order[source_id]
                is_hotlist = source_id in hotlist_sources
                item_id = f"{cluster_id}-{hit_index + 1}"
                items.append(
                    {
                        "id": item_id,
                        "source": source_id,
                        "title": f"{cluster_id}标题{hit_index + 1}",
                        "url": f"https://{source_id}.example/{item_id}",
                        "surface": "hotlist" if is_hotlist else "current_issue" if source_id == "people" else "homepage",
                        "display_order": display_order,
                        "hotlist_position": display_order if is_hotlist else None,
                        "eligible": True,
                        "exclusion_reason": None,
                        "publication_date": None,
                    }
                )
            ledger["clusters"].append(
                {
                    "id": cluster_id,
                    "normalized_title": cluster_id,
                    "items": items,
                }
            )

        errors, qualified, selected = validate_and_rank(
            ledger, 10, enforce_source_completeness=False
        )
        self.assertEqual(errors, [])
        self.assertEqual(
            [entry["id"] for entry in selected],
            [
                "current-2",
                "current-1",
                "current-4",
                "current-6",
                "current-3",
                "current-5",
                "current-7",
                "current-8",
                "current-9",
                "current-10",
            ],
        )
        self.assertEqual(
            [entry["rank"] for entry in qualified],
            [1, 2, 3, 4, 5, 6, 7, 7, 9, 9],
        )

    def test_duplicate_source_display_order_fails(self) -> None:
        ledger = make_ledger(complete_sources=False)
        ledger["clusters"][0]["items"].append(
            hotlist_item("weibo-duplicate-order", "weibo", "不同标题但位置重复", 1)
        )
        errors, _, _ = validate_and_rank(
            ledger, 10, enforce_source_completeness=False
        )
        self.assertTrue(any("display_order 1" in error for error in errors))

    def test_unreadable_core_source_blocks_ranking(self) -> None:
        ledger = copy.deepcopy(make_ledger(complete_sources=False))
        source = next(row for row in ledger["sources"] if row["id"] == "caixin")
        source["status"] = "inaccessible"
        source["reason"] = "页面没有可读正文"
        errors, _, _ = validate_and_rank(
            ledger, 10, enforce_source_completeness=False
        )
        self.assertTrue(any("all nine fixed core sources" in error for error in errors))

    def test_old_schema_version_fails(self) -> None:
        ledger = make_ledger(complete_sources=False)
        ledger["schema_version"] = 1
        errors, _, _ = validate_and_rank(
            ledger, 10, enforce_source_completeness=False
        )
        self.assertTrue(any("quick schema_version must equal 2" in error and "rebuild" in error for error in errors))

    def test_unreviewed_or_stale_clustering_cannot_publish(self) -> None:
        for action in ("missing", "pending", "wrong_run", "missing_singleton", "changed_group", "changed_title"):
            ledger = make_ledger()
            if action == "missing":
                ledger.pop("clustering_review")
            elif action == "pending":
                ledger["clustering_review"]["status"] = "pending"
            elif action == "wrong_run":
                ledger["clustering_review"]["run_id"] = "other-run"
            elif action == "missing_singleton":
                ledger["clustering_review"]["reviewed_item_ids"].pop()
            elif action == "changed_group":
                ledger["clusters"][0]["normalized_title"] = "Different grouping interpretation"
            else:
                ledger["clusters"][0]["items"][0]["title"] = "Changed original title"
            errors, _, _ = validate_and_rank(ledger, 10)
            self.assertTrue(any("clustering_review" in error for error in errors), (action, errors))

    def test_pure_link_backfill_does_not_change_semantic_review_fingerprint(self) -> None:
        from validate_ranking import clustering_review_fingerprint
        ledger = make_ledger()
        before = clustering_review_fingerprint(ledger)
        ledger["clusters"][0]["items"][0]["url"] = "https://s.weibo.com/weibo?q=corrected"
        self.assertEqual(before, clustering_review_fingerprint(ledger))

    def test_runtime_evidence_is_required_and_bound_to_raw_rows(self) -> None:
        for action in ("missing", "wrong_run", "wrong_source", "row_tamper"):
            ledger = make_ledger()
            collection = ledger["sources"][0]["collection"]
            if action == "missing":
                collection.pop("runtime_evidence")
            elif action == "wrong_run":
                collection["runtime_evidence"]["run_id"] = "old-run"
            elif action == "wrong_source":
                collection["runtime_evidence"]["source"] = "zhihu"
            else:
                source_items(ledger, "weibo")[0]["raw_url"] = "https://s.weibo.com/weibo?q=tamper"
            errors, _, _ = validate_and_rank(ledger, 10)
            self.assertTrue(any("runtime evidence failed" in error for error in errors), (action, errors))

    def test_three_news_sources_accept_only_homepage_and_people_only_current_issue(self) -> None:
        for source in ("jiemian", "bjnews", "caixin", "people"):
            for surface in ("current_section", "other", "homepage" if source == "people" else "current_issue"):
                ledger = make_ledger()
                source_items(ledger, source)[0]["surface"] = surface
                errors, _, _ = validate_and_rank(ledger, 10)
                self.assertTrue(any("surface" in error for error in errors), (source, surface, errors))

    def test_silent_effective_link_or_exclusion_change_is_rejected(self) -> None:
        for field, value in (("url", "https://s.weibo.com/weibo?q=unrecorded"), ("eligible", False)):
            ledger = make_ledger()
            item = source_items(ledger, "weibo")[0]
            item[field] = value
            if field == "eligible":
                item["exclusion_reason"] = "broken"
            refresh_review(ledger)
            errors, _, _ = validate_and_rank(ledger, 10)
            self.assertTrue(any("correction provenance failed" in error for error in errors), errors)

    def test_complete_source_collections_pass(self) -> None:
        errors, _, _ = validate_and_rank(make_ledger(), 10)
        self.assertEqual(errors, [])

    def test_completed_review_cannot_predate_its_source_capture(self) -> None:
        ledger = make_ledger()
        ledger["clustering_review"]["reviewed_at"] = f"{ledger['run']['date']}T09:10:00+08:00"
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertIn("clustering_review.reviewed_at cannot precede source capture completion", errors)

    def test_fixed_hotlist_shortfall_blocks_ranking(self) -> None:
        ledger = make_ledger()
        remove_source_position(ledger, "weibo", 50)
        refresh_source_collection(ledger, "weibo")
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(
            any("fixed hot-list count 50" in error for error in errors), errors
        )

    def test_fixed_hotlist_display_order_gap_blocks_ranking(self) -> None:
        ledger = make_ledger()
        item = next(
            item for item in source_items(ledger, "weibo") if item["display_order"] == 25
        )
        item["display_order"] = 51
        item["hotlist_position"] = 51
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(
            any("cover every position from 1 through 50" in error for error in errors),
            errors,
        )

    def test_news_homepage_requires_footer_and_two_stable_rounds(self) -> None:
        ledger = make_ledger()
        collection = next(
            row["collection"] for row in ledger["sources"] if row["id"] == "jiemian"
        )
        collection["footer_reached"] = False
        collection["no_new_item_rounds"] = 1
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(any("footer_reached must be true" in error for error in errors))
        self.assertTrue(any("no_new_item_rounds must be at least 2" in error for error in errors))

    def test_people_requires_every_current_issue_page(self) -> None:
        ledger = make_ledger()
        collection = next(
            row["collection"] for row in ledger["sources"] if row["id"] == "people"
        )
        collection["issue_read_pages"] = 1
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(
            any("issue_read_pages must equal issue_total_pages" in error for error in errors)
        )

    def test_reported_source_counts_must_match_ledger_items(self) -> None:
        ledger = make_ledger()
        collection = next(
            row["collection"] for row in ledger["sources"] if row["id"] == "douyin"
        )
        collection["eligible_count"] = 49
        collection["excluded_count"] = 1
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(
            any("eligible_count does not match ledger items" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
