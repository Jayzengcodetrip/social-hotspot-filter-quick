#!/usr/bin/env python3
"""Synthetic lossless-capture and unchanged-publication-gate regression tests."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _core_link_rules import validate_selected_core_links
from _test_fixtures import make_ledger, refresh_review, refresh_capture_evidence
from build_capture_ledger import build_ledger as production_build_ledger, load_captures, review_template
from validate_ranking import SOURCE_IDS, serializable_entry, validate_and_rank


def build_ledger(captures, run, groups=None, *, partial=False, corrections=None):
    """Existing fixture assertions explicitly model completed synthetic review."""
    captures = copy.deepcopy(captures)
    groups = [] if groups is None else groups
    checkpoint = production_build_ledger(captures, run, groups, partial=True, corrections=corrections)
    if not partial:
        # Rows mutated by a fixture represent another synthetic direct capture.
        # Keep contradictory collection claims untouched for rejection tests.
        refresh_capture_evidence(captures, run)
        checkpoint = production_build_ledger(captures, run, groups, partial=True, corrections=corrections)
    if partial:
        return checkpoint
    refresh_review(checkpoint)
    return production_build_ledger(captures, run, groups, corrections=corrections,
                                   review=checkpoint["clustering_review"])


def fixture_inputs() -> tuple[dict, dict, list, dict]:
    """Convert an existing complete synthetic fixture, not historical news."""
    original = make_ledger()
    run = {**original["run"], "run_id": "synthetic-current-run"}
    captures = {}
    for source in SOURCE_IDS:
        items = sorted(
            (
                item for cluster in original["clusters"] for item in cluster["items"]
                if item["source"] == source
            ),
            key=lambda item: item["display_order"],
        )
        rows = [
            {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key not in {"id", "source", "display_order", "hotlist_position", "raw_url", "raw_eligible", "raw_exclusion_reason", "correction_ids"}
            }
            for item in items
        ]
        collection = next(row["collection"] for row in original["sources"] if row["id"] == source)
        captures[source] = {
            "source": source,
            "run_id": run["run_id"],
            "rows": rows,
            "collection": copy.deepcopy(collection),
        }
    groups = [
        {
            "id": cluster["id"],
            "normalized_title": cluster["normalized_title"],
            **({"display_title": cluster["display_title"]} if "display_title" in cluster else {}),
            "items": [f"{item['source']}-{item['display_order']:03d}" for item in cluster["items"]],
        }
        for cluster in original["clusters"]
    ]
    return captures, run, groups, original


def all_items(ledger: dict) -> dict[str, dict]:
    return {item["id"]: item for cluster in ledger["clusters"] for item in cluster["items"]}


def unset_derived(packet: dict) -> None:
    for field in ("discovered_count", "eligible_count", "excluded_count", "last_item_title"):
        packet["collection"].pop(field, None)


class BuildCaptureLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captures, self.run, self.groups, self.original = fixture_inputs()

    def test_every_row_exact_title_url_order_and_metadata_are_preserved(self) -> None:
        title = "  原题 [甲] | ‘乙’\n 不改空格  "
        row = self.captures["bjnews"]["rows"][0]
        row.update(title=title, capture_metadata={"container": "card-1", "frame": "main"})
        frozen = copy.deepcopy((self.captures, self.run, self.groups))
        ledger = build_ledger(self.captures, self.run, self.groups)
        items = all_items(ledger)
        self.assertEqual(len(items), sum(len(packet["rows"]) for packet in self.captures.values()))
        for source, packet in self.captures.items():
            for order, captured in enumerate(packet["rows"], 1):
                item = items[f"{source}-{order:03d}"]
                self.assertEqual(item["title"], captured["title"])
                self.assertEqual(item["url"], captured["url"])
                self.assertEqual(item["display_order"], order)
        self.assertEqual(items["bjnews-001"]["capture_metadata"], row["capture_metadata"])
        self.assertEqual((self.captures, self.run, self.groups), frozen)

    def test_same_capture_and_groups_keep_the_existing_ranking(self) -> None:
        ledger = build_ledger(self.captures, self.run, self.groups)
        errors, qualified, selected = validate_and_rank(ledger, 10)
        old_errors, old_qualified, old_selected = validate_and_rank(self.original, 10)
        self.assertEqual(errors, old_errors)
        self.assertEqual(errors, [])
        self.assertEqual(
            [serializable_entry(item) for item in qualified],
            [serializable_entry(item) for item in old_qualified],
        )
        self.assertEqual(
            [serializable_entry(item) for item in selected],
            [serializable_entry(item) for item in old_selected],
        )

    def test_no_groups_leave_every_row_a_singleton(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit groups"):
            production_build_ledger(self.captures, self.run)
        ledger = build_ledger(self.captures, self.run, [])
        self.assertEqual(len(ledger["clusters"]), len(all_items(ledger)))
        self.assertTrue(all(len(cluster["items"]) == 1 for cluster in ledger["clusters"]))
        self.assertEqual(validate_and_rank(ledger, 10)[1], [])

    def test_complete_capture_without_review_cannot_publish(self) -> None:
        with self.assertRaisesRegex(ValueError, "clustering_review"):
            production_build_ledger(self.captures, self.run, self.groups)
        checkpoint = production_build_ledger(self.captures, self.run, self.groups, partial=True)
        template = review_template(checkpoint)
        self.assertEqual(template["status"], "pending")
        self.assertIsNone(template["reviewed_at"])
        self.assertEqual(len(template["reviewed_item_ids"]), len(all_items(checkpoint)))
        with self.assertRaisesRegex(ValueError, "clustering_review"):
            production_build_ledger(self.captures, self.run, self.groups, review=template)

    def test_reviewed_empty_grouping_can_generate_a_true_zero_topic_report(self) -> None:
        from generate_report import build_ranking_payload, generate_report
        from validate_report import validate_report
        ledger = build_ledger(self.captures, self.run, [])
        errors, ranking, selected = build_ranking_payload(ledger, 10)
        self.assertEqual((errors, selected), ([], []))
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(errors, [])
        self.assertIn("当前没有话题同时出现在至少两个不同核心域名。", report)
        self.assertEqual(validate_report(ledger, report, 10), [])

    def test_group_edit_invalidates_prior_review_but_pure_link_fill_does_not(self) -> None:
        ledger = build_ledger(self.captures, self.run, self.groups)
        changed = copy.deepcopy(self.groups)
        changed[0]["normalized_title"] = "Changed semantic interpretation"
        with self.assertRaisesRegex(ValueError, "decision_sha256 is stale"):
            production_build_ledger(self.captures, self.run, changed, review=ledger["clustering_review"])
        raw = self.captures["weibo"]["rows"][0]["url"]
        records = [self.correction("weibo", 1, {"url": raw}, {"url": "/weibo?q=observed-new-link"})]
        repaired = production_build_ledger(self.captures, self.run, self.groups,
                                            corrections=records, review=ledger["clustering_review"])
        self.assertEqual(repaired["clustering_review"], ledger["clustering_review"])

    def test_malformed_raw_url_has_local_append_only_repair(self) -> None:
        self.captures["weibo"]["rows"][0]["url"] = "javascript:void(0)"
        record = self.correction("weibo", 1, {"url": "javascript:void(0)"}, {"url": "/weibo?q=observed-entry"})
        ledger = build_ledger(self.captures, self.run, self.groups, corrections=[record])
        self.assertEqual(all_items(ledger)["weibo-001"]["raw_url"], "javascript:void(0)")
        self.assertEqual(all_items(ledger)["weibo-001"]["url"], "https://s.weibo.com/weibo?q=observed-entry")
        self.assertEqual(self.captures["weibo"]["rows"][0]["url"], "javascript:void(0)")
        ledger["corrections"][0]["before"]["url"] = None
        self.assertTrue(any("correction provenance failed" in error for error in validate_and_rank(ledger, 10)[0]))

    def test_unassigned_items_stay_singletons_and_group_items_follow_source_order(self) -> None:
        groups = [{"id": "pair", "normalized_title": "事件", "items": ["zhihu-002", "weibo-001"]}]
        ledger = build_ledger(self.captures, self.run, groups)
        self.assertEqual([item["id"] for item in ledger["clusters"][0]["items"]], ["weibo-001", "zhihu-002"])
        self.assertEqual(len(all_items(ledger)), sum(len(p["rows"]) for p in self.captures.values()))
        self.assertTrue(all(len(cluster["items"]) == 1 for cluster in ledger["clusters"][1:]))

    def test_relative_same_row_link_normalizes_without_title_based_reconstruction(self) -> None:
        row = self.captures["weibo"]["rows"][0]
        row.update(title="标题完全不能用于拼接查询", url="/weibo?q=%23observed%23&amp;Refer=top#fragment")
        ledger = build_ledger(self.captures, self.run)
        item = all_items(ledger)["weibo-001"]
        self.assertEqual(item["title"], row["title"])
        self.assertEqual(item["url"], "https://s.weibo.com/weibo?q=%23observed%23&Refer=top")

    def test_null_required_link_is_not_excluded_and_selected_gate_still_blocks(self) -> None:
        self.captures["weibo"]["rows"][0]["url"] = None
        ledger = build_ledger(self.captures, self.run, self.groups)
        item = all_items(ledger)["weibo-001"]
        self.assertTrue(item["eligible"])
        self.assertIsNone(item["exclusion_reason"])
        errors, _, selected = validate_and_rank(ledger, 10)
        self.assertEqual(errors, [])
        self.assertTrue(any("weibo-001" in error for error in validate_selected_core_links(selected)))

    def test_optional_sources_keep_null_links(self) -> None:
        for source in ("bilibili", "douyin"):
            self.captures[source]["rows"][0]["url"] = None
        ledger = build_ledger(self.captures, self.run)
        for source in ("bilibili", "douyin"):
            item = all_items(ledger)[f"{source}-001"]
            self.assertIsNone(item["url"])
            self.assertTrue(item["eligible"])

    def test_invalid_raw_href_is_preserved_as_pending_until_selected(self) -> None:
        for value in ("", "javascript:alert(1)", "https://example.org/article", "https://www.bjnews.com.cn/"):
            with self.subTest(value=value):
                captures = copy.deepcopy(self.captures)
                captures["bjnews"]["rows"][0]["url"] = value
                ledger = build_ledger(captures, self.run)
                item = all_items(ledger)["bjnews-001"]
                self.assertEqual(item["raw_url"], value)
                self.assertIsNone(item["url"])
                self.assertTrue(item["eligible"])
        del self.captures["bjnews"]["rows"][0]["url"]
        with self.assertRaisesRegex(ValueError, "url is missing"):
            build_ledger(self.captures, self.run)

    def test_exact_title_duplicates_are_marked_not_dropped(self) -> None:
        packet = self.captures["weibo"]
        packet["rows"][1]["title"] = packet["rows"][0]["title"]
        unset_derived(packet)
        ledger = build_ledger(self.captures, self.run)
        items = all_items(ledger)
        self.assertTrue(items["weibo-001"]["eligible"])
        self.assertFalse(items["weibo-002"]["eligible"])
        self.assertEqual(items["weibo-002"]["exclusion_reason"], "technical_duplicate")
        self.assertEqual(items["weibo-002"]["duplicate_of"], "weibo-001")
        collection = ledger["sources"][0]["collection"]
        self.assertEqual((collection["discovered_count"], collection["eligible_count"], collection["excluded_count"]), (50, 49, 1))

    def test_same_normalized_source_url_duplicate_is_preserved(self) -> None:
        packet = self.captures["bjnews"]
        packet["rows"][1]["url"] = packet["rows"][0]["url"] + "#second-render"
        unset_derived(packet)
        ledger = build_ledger(self.captures, self.run)
        item = all_items(ledger)["bjnews-002"]
        self.assertFalse(item["eligible"])
        self.assertEqual(item["exclusion_reason"], "technical_duplicate")
        self.assertEqual(item["title"], packet["rows"][1]["title"])

    def test_same_title_across_sources_is_not_a_technical_duplicate(self) -> None:
        title = "相同原题"
        for source in ("weibo", "zhihu"):
            self.captures[source]["rows"][0]["title"] = title
        ledger = build_ledger(self.captures, self.run)
        self.assertTrue(all_items(ledger)["weibo-001"]["eligible"])
        self.assertTrue(all_items(ledger)["zhihu-001"]["eligible"])

    def test_old_current_homepage_and_topic_categories_remain_eligible(self) -> None:
        row = self.captures["caixin"]["rows"][0]
        row.update(title="娱乐饭圈 AI 体育 金融生活方式", publication_date="2016-01-01")
        ledger = build_ledger(self.captures, self.run)
        self.assertTrue(all_items(ledger)["caixin-001"]["eligible"])

    def test_explicit_technical_exclusion_requires_a_valid_paired_flag(self) -> None:
        packet = self.captures["bjnews"]
        packet["rows"][0].update(eligible=False, exclusion_reason="instruction_polluted")
        unset_derived(packet)
        ledger = build_ledger(self.captures, self.run)
        self.assertEqual(all_items(ledger)["bjnews-001"]["exclusion_reason"], "instruction_polluted")
        for changes in ({"eligible": False}, {"exclusion_reason": "broken"}, {"eligible": False, "exclusion_reason": "entertainment"}):
            captures = copy.deepcopy(self.captures)
            row = captures["bjnews"]["rows"][0]
            row.pop("eligible", None)
            row.pop("exclusion_reason", None)
            row.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_ledger(captures, self.run)

    def test_earlier_excluded_record_does_not_suppress_later_usable_record(self) -> None:
        packet = self.captures["bjnews"]
        packet["rows"][0].update(eligible=False, exclusion_reason="instruction_polluted")
        packet["rows"][1]["title"] = packet["rows"][0]["title"]
        unset_derived(packet)
        ledger = build_ledger(self.captures, self.run)
        self.assertTrue(all_items(ledger)["bjnews-002"]["eligible"])

    def test_counts_and_last_title_are_derived_without_inventing_end_evidence(self) -> None:
        packet = self.captures["bjnews"]
        unset_derived(packet)
        packet["collection"].pop("completion_method")
        ledger = build_ledger(self.captures, self.run)
        collection = next(source["collection"] for source in ledger["sources"] if source["id"] == "bjnews")
        self.assertEqual(collection["discovered_count"], 2)
        self.assertEqual(collection["last_item_title"], packet["rows"][-1]["title"])
        self.assertEqual(collection["completion_method"], "homepage_footer_stable")
        packet["collection"].pop("end_evidence")
        with self.assertRaisesRegex(ValueError, "end_evidence"):
            build_ledger(self.captures, self.run)

    def test_disagreeing_supplied_count_title_or_method_is_not_overwritten(self) -> None:
        for field, value in (("discovered_count", 3), ("eligible_count", 1), ("last_item_title", "假的末条"), ("completion_method", "homepage_end")):
            captures = copy.deepcopy(self.captures)
            captures["bjnews"]["collection"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "disagrees"):
                build_ledger(captures, self.run)
        self.captures["weibo"]["collection"]["expected_item_count"] = 49
        with self.assertRaisesRegex(ValueError, "expected_item_count disagrees"):
            build_ledger(self.captures, self.run)

    def test_fixed_list_shortfall_and_excess_both_fail(self) -> None:
        for delta in (-1, 1):
            captures = copy.deepcopy(self.captures)
            packet = captures["weibo"]
            if delta < 0:
                packet["rows"].pop()
            else:
                packet["rows"].append({"title": "多出的一条", "url": None})
            unset_derived(packet)
            with self.subTest(delta=delta), self.assertRaisesRegex(ValueError, "fixed.hot.?list.count"):
                build_ledger(captures, self.run)

    def test_incomplete_footer_end_flag_or_quiet_rounds_fail(self) -> None:
        for field, value in (("footer_reached", False), ("page_height_stable", False), ("no_new_item_rounds", 1), ("end_reached", False)):
            captures = copy.deepcopy(self.captures)
            captures["bjnews"]["collection"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                build_ledger(captures, self.run)

    def test_people_issue_date_and_page_coverage_fail_closed(self) -> None:
        for field, value in (("issue_date", "2020-01-01"), ("issue_read_pages", 1), ("issue_total_pages", 0)):
            captures = copy.deepcopy(self.captures)
            captures["people"]["collection"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                build_ledger(captures, self.run)

    def test_all_nine_packets_and_current_run_id_are_required(self) -> None:
        del self.captures["caixin"]
        with self.assertRaisesRegex(ValueError, "missing capture packets: caixin"):
            build_ledger(self.captures, self.run)
        self.captures, _, _, _ = fixture_inputs()
        self.captures["caixin"]["run_id"] = "previous-run"
        with self.assertRaisesRegex(ValueError, "run_id"):
            build_ledger(self.captures, self.run)

    def test_partial_checkpoints_always_fail_publication_even_when_complete(self) -> None:
        for complete in (False, True):
            captures = self.captures if complete else {"weibo": self.captures["weibo"]}
            ledger = build_ledger(captures, self.run, partial=True)
            self.assertEqual(ledger["report_mode"], "quick-partial")
            errors, _, _ = validate_and_rank(ledger, 10)
            self.assertTrue(any("report_mode must equal quick" in error for error in errors))

    def test_partial_does_not_fabricate_unobserved_ending(self) -> None:
        packet = copy.deepcopy(self.captures["bjnews"])
        packet["collection"] = {"scope": "本轮首页，未完成"}
        ledger = build_ledger({"bjnews": packet}, self.run, partial=True)
        collection = ledger["sources"][0]["collection"]
        for field in ("end_reached", "end_evidence", "footer_reached", "no_new_item_rounds", "page_height_stable"):
            self.assertNotIn(field, collection)

    def test_group_unknown_id_duplicate_assignment_and_id_collisions_reject(self) -> None:
        invalid_groups = [
            [{"id": "pair", "normalized_title": "事件", "items": ["weibo-999"]}],
            [{"id": "pair", "normalized_title": "事件", "items": ["weibo-001", "weibo-001"]}],
            [{"id": "a", "normalized_title": "甲", "items": ["weibo-001"]}, {"id": "b", "normalized_title": "乙", "items": ["weibo-001"]}],
            [{"id": "same", "normalized_title": "甲", "items": ["weibo-001"]}, {"id": "same", "normalized_title": "乙", "items": ["weibo-002"]}],
            [{"id": "weibo-001", "normalized_title": "事件", "items": ["weibo-001"]}],
            [{"id": "singleton-weibo-002", "normalized_title": "事件", "items": ["weibo-001"]}],
        ]
        for groups in invalid_groups:
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                build_ledger(self.captures, self.run, groups)

    def test_groups_cannot_smuggle_full_edition_fields(self) -> None:
        self.groups[0]["event_summary"] = "不应存在"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            build_ledger(self.captures, self.run, self.groups)

    def test_disagreeing_item_id_source_position_or_link_metadata_fails(self) -> None:
        for field, value in (("id", "invented"), ("source", "caixin"), ("display_order", 2), ("hotlist_position", 2)):
            captures = copy.deepcopy(self.captures)
            captures["weibo"]["rows"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "disagrees"):
                build_ledger(captures, self.run)

    def test_missing_run_metadata_and_packet_source_mismatch_fail(self) -> None:
        self.run.pop("run_id")
        with self.assertRaisesRegex(ValueError, "run.run_id"):
            build_ledger(self.captures, self.run)
        _, self.run, _, _ = fixture_inputs()
        self.captures["weibo"]["source"] = "zhihu"
        with self.assertRaisesRegex(ValueError, "packet filename/key"):
            build_ledger(self.captures, self.run)

    def test_observed_link_correction_is_reapplied_without_mutating_raw_packet(self) -> None:
        self.captures["weibo"]["rows"][0]["url"] = None
        correction = [self.correction("weibo", 1, {"url": None}, {"url": "/weibo?q=observed-entry"})]
        frozen = copy.deepcopy(self.captures)
        ledger = build_ledger(self.captures, self.run, self.groups, corrections=correction)
        item = all_items(ledger)["weibo-001"]
        self.assertEqual(item["url"], "https://s.weibo.com/weibo?q=observed-entry")
        self.assertEqual(item["title"], frozen["weibo"]["rows"][0]["title"])
        self.assertEqual(item["correction_ids"], [correction[0]["id"]])
        self.assertIsNone(item["raw_url"])
        self.assertEqual(ledger["corrections"], correction)
        self.assertEqual(self.captures, frozen)
        self.assertEqual(ledger, build_ledger(self.captures, self.run, self.groups, corrections=correction))

    def test_explicit_duplicate_adjudication_updates_only_eligible_counts(self) -> None:
        correction = [self.correction("bjnews", 2, {"eligible": True, "exclusion_reason": None},
                                      {"eligible": False, "exclusion_reason": "technical_duplicate"})]
        ledger = build_ledger(self.captures, self.run, corrections=correction)
        item = all_items(ledger)["bjnews-002"]
        self.assertFalse(item["eligible"])
        self.assertEqual(item["display_order"], 2)
        collection = next(row["collection"] for row in ledger["sources"] if row["id"] == "bjnews")
        self.assertEqual((collection["discovered_count"], collection["eligible_count"], collection["excluded_count"]), (2, 1, 1))
        self.assertEqual(collection["last_item_title"], self.captures["bjnews"]["rows"][-1]["title"])
        self.captures["bjnews"]["collection"]["eligible_count"] = 99
        with self.assertRaisesRegex(ValueError, "eligible_count disagrees"):
            build_ledger(self.captures, self.run, corrections=correction)

    def test_corrections_cannot_change_title_order_source_or_erase_link(self) -> None:
        for field, value in (("title", "改过的标题"), ("source", "caixin"), ("display_order", 3), ("url", None), ("url", "")):
            correction = [self.correction("weibo", 1, {field: self.captures["weibo"]["rows"][0].get(field)}, {field: value})]
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_ledger(self.captures, self.run, corrections=correction)

    def test_correction_requires_evidence_known_id_and_paired_allowed_exclusion(self) -> None:
        valid = self.correction("weibo", 1, {"url": self.captures["weibo"]["rows"][0]["url"]}, {"url": "/weibo?q=observed"})
        invalid = []
        for field, value in (("item_id", "weibo-999"), ("source", "caixin"), ("title", "different title"),
                             ("display_order", 2), ("evidence", ""), ("corrected_at", "yesterday")):
            changed = copy.deepcopy(valid)
            changed[field] = value
            invalid.append([changed])
        changed = copy.deepcopy(valid)
        changed["after"]["url"] = "https://example.org/not-source"
        changed["observation"]["observed_url"] = changed["after"]["url"]
        invalid.append([changed])
        invalid.append([valid, valid])
        invalid.append({"weibo-001": {"url": "/weibo?q=old-shape"}})
        for correction in invalid:
            with self.subTest(correction=correction), self.assertRaises(ValueError):
                build_ledger(self.captures, self.run, corrections=correction)

    def correction(self, source, order, before, after):
        row = self.captures[source]["rows"][order - 1]
        return {"id": f"correction-{source}-{order}", "run_id": self.run["run_id"], "source": source,
                "item_id": f"{source}-{order:03d}", "title": row["title"], "display_order": order,
                "corrected_at": "2026-08-06T12:03:00+08:00", "before": before, "after": after,
                "evidence": "Synthetic same-row observed correction; never live evidence",
                "observation": {"document_url": "https://s.weibo.com/top/summary" if source == "weibo" else "https://www.bjnews.com.cn/",
                                "observed_title": row["title"], "observed_url": after.get("url", row["url"]),
                                "observed_at": "2026-08-06T12:02:00+08:00"}}

    def test_cli_and_capture_loader_preserve_inputs_and_protect_overwrite(self) -> None:
        script = Path(__file__).with_name("build_capture_ledger.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captures_dir = root / "captures"
            captures_dir.mkdir()
            for source, packet in self.captures.items():
                (captures_dir / f"{source}.json").write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            run_path = root / "run.json"
            run_path.write_text(json.dumps(self.run), encoding="utf-8")
            groups_path = root / "groups.json"
            groups_path.write_text(json.dumps(self.groups), encoding="utf-8")
            review_path = root / "review.json"
            review_path.write_text(json.dumps(build_ledger(self.captures, self.run, self.groups)["clustering_review"]), encoding="utf-8")
            output = root / "ledger.json"
            command = [sys.executable, str(script), "--captures", str(captures_dir), "--run", str(run_path), "--groups", str(groups_path), "--review", str(review_path), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS capture ledger", result.stdout)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), build_ledger(self.captures, self.run, self.groups))
            self.assertEqual(load_captures(captures_dir), self.captures)
            original_run = run_path.read_text(encoding="utf-8")
            result = subprocess.run(command[:-1] + [str(run_path)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(run_path.read_text(encoding="utf-8"), original_run)
            (captures_dir / "unknown.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected capture JSON"):
                load_captures(captures_dir)


if __name__ == "__main__":
    unittest.main()
