"""Behavioral checks for quick/full separation and unchanged collection gates."""

from __future__ import annotations

import copy
import unittest

from _test_fixtures import (
    FIXED_COUNTS, SOURCE_IDS, make_ledger, refresh_source_collection,
    remove_source_position,
)
from generate_report import build_ranking_payload, generate_report
from validate_ranking import validate_and_rank


class QuickContractTests(unittest.TestCase):
    def assert_invalid(self, ledger: dict) -> None:
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertTrue(errors)

    def test_quick_mode_marker_is_required(self) -> None:
        for mode in (None, "full", "fast", True):
            with self.subTest(mode=mode):
                ledger = make_ledger()
                ledger["report_mode"] = mode
                self.assert_invalid(ledger)

    def test_full_or_ambiguous_schema_is_rejected(self) -> None:
        for version in (5, 1, "2", None, True):
            with self.subTest(version=version):
                ledger = make_ledger()
                ledger["schema_version"] = version
                self.assert_invalid(ledger)

    def test_google_fields_are_not_required_placeholders(self) -> None:
        for field, value in (("google_status", "used"), ("google_status", "not_used"), ("google_limitation", "")):
            with self.subTest(field=field, value=value):
                ledger = make_ledger()
                ledger["run"][field] = value
                self.assert_invalid(ledger)

    def test_full_research_fields_are_rejected_even_when_empty(self) -> None:
        for field in ("verification", "event_summary", "closure"):
            for value in (None, {}, ""):
                with self.subTest(field=field, value=value):
                    ledger = make_ledger()
                    ledger["clusters"][0][field] = value
                    self.assert_invalid(ledger)

    def test_all_five_hotlists_reject_shortfalls(self) -> None:
        for source, count in FIXED_COUNTS.items():
            with self.subTest(source=source):
                ledger = make_ledger()
                remove_source_position(ledger, source, count)
                refresh_source_collection(ledger, source)
                self.assert_invalid(ledger)

    def test_each_core_source_is_required(self) -> None:
        for source in SOURCE_IDS:
            with self.subTest(source=source):
                ledger = make_ledger()
                ledger["sources"] = [row for row in ledger["sources"] if row["id"] != source]
                self.assert_invalid(ledger)

    def test_stable_footer_is_required_for_each_homepage(self) -> None:
        for source in ("jiemian", "bjnews", "caixin"):
            for field, value in (("footer_reached", False), ("page_height_stable", False), ("no_new_item_rounds", 1)):
                with self.subTest(source=source, field=field):
                    ledger = make_ledger()
                    next(row for row in ledger["sources"] if row["id"] == source)["collection"][field] = value
                    self.assert_invalid(ledger)

    def test_people_issue_must_match_collection_date(self) -> None:
        ledger = make_ledger()
        next(row for row in ledger["sources"] if row["id"] == "people")["collection"]["issue_date"] = "2000-01-01"
        self.assert_invalid(ledger)

    def test_old_publication_date_does_not_exclude_current_news(self) -> None:
        ledger = make_ledger()
        for cluster in ledger["clusters"]:
            for item in cluster["items"]:
                if item["source"] in ("jiemian", "bjnews", "caixin", "people"):
                    item["publication_date"] = "2000-01-01"
        errors, _, _ = validate_and_rank(ledger, 10)
        self.assertEqual(errors, [])

    def test_data_order_does_not_change_ranking(self) -> None:
        ledger = make_ledger()
        errors, expected, _ = build_ranking_payload(ledger, 10)
        self.assertEqual(errors, [])
        ledger["sources"].reverse()
        ledger["clusters"].reverse()
        for cluster in ledger["clusters"]:
            cluster["items"].reverse()
        errors, actual, _ = build_ranking_payload(ledger, 10)
        self.assertEqual(errors, [])
        self.assertEqual(actual["selected"], expected["selected"])
        self.assertEqual(actual["qualified_count"], expected["qualified_count"])

    def test_full_mode_ranking_json_cannot_generate_quick_report(self) -> None:
        ledger = make_ledger()
        _, ranking, _ = build_ranking_payload(ledger, 10)
        ranking["schema_version"] = 5
        ranking.pop("report_mode")
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(report, "")
        self.assertTrue(errors)

    def test_missing_source_prevents_generation_even_with_stale_good_ranking(self) -> None:
        ledger = make_ledger()
        _, ranking, _ = build_ranking_payload(ledger, 10)
        ledger["sources"].pop()
        report, errors = generate_report(ledger, ranking)
        self.assertEqual(report, "")
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
