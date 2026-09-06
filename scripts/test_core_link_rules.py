#!/usr/bin/env python3
"""Regression tests for selected core-source link normalization and gates."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _core_link_rules import (
    REQUIRED_LINK_SOURCE_IDS,
    normalize_core_link,
    validate_required_source_url,
    validate_selected_core_links,
)
from _test_fixtures import make_ledger, source_test_url, refresh_runtime_evidence
from validate_ranking import validate_and_rank


class CoreLinkRuleTests(unittest.TestCase):
    def selected(self, ledger: dict) -> list[dict]:
        errors, _, selected = validate_and_rank(ledger, 10)
        self.assertEqual(errors, [])
        return selected

    def test_relative_weibo_href_is_resolved_and_fragment_removed(self) -> None:
        url, error = normalize_core_link(
            "weibo", "/weibo?q=%23test%23&amp;Refer=top#footer"
        )
        self.assertIsNone(error)
        self.assertEqual(
            url, "https://s.weibo.com/weibo?q=%23test%23&Refer=top"
        )

    def test_wrong_host_and_fixed_entry_page_are_rejected(self) -> None:
        self.assertIn(
            "does not belong",
            validate_required_source_url("zhihu", "https://example.com/question/1") or "",
        )
        self.assertIn(
            "source entry page",
            validate_required_source_url("weibo", "https://s.weibo.com/top/summary") or "",
        )

    def test_missing_selected_link_fails_for_every_required_source(self) -> None:
        valid_urls = {
            source_id: source_test_url(source_id, 501 + index)
            for index, source_id in enumerate(REQUIRED_LINK_SOURCE_IDS)
        }
        selected = [
            {
                "id": "all-required-links",
                "display_title": "七个平台链接测试",
                "cluster": {
                    "items": [
                        {
                            "id": f"{source_id}-selected",
                            "source": source_id,
                            "title": source_id,
                            "url": valid_urls[source_id],
                            "eligible": True,
                        }
                        for source_id in REQUIRED_LINK_SOURCE_IDS
                    ]
                },
            }
        ]
        for source_id in REQUIRED_LINK_SOURCE_IDS:
            case = copy.deepcopy(selected)
            item = next(
                item
                for item in case[0]["cluster"]["items"]
                if item["source"] == source_id
            )
            item["url"] = None
            errors = validate_selected_core_links(case)
            self.assertEqual(len(errors), 1, source_id)
            self.assertIn("requires a non-empty", errors[0])

    def test_bilibili_and_douyin_null_links_are_exempt(self) -> None:
        selected = [
            {
                "id": "optional-links",
                "display_title": "B站抖音链接豁免",
                "cluster": {
                    "items": [
                        {"id": "bili", "source": "bilibili", "url": None, "eligible": True},
                        {"id": "douyin", "source": "douyin", "url": None, "eligible": True},
                    ]
                },
            }
        ]
        self.assertEqual(validate_selected_core_links(selected), [])

    def test_generator_fixture_selection_has_valid_required_links(self) -> None:
        ledger = make_ledger()
        self.assertEqual(validate_selected_core_links(self.selected(ledger)), [])

    def test_selected_link_cli_passes_and_then_rejects_missing_link(self) -> None:
        script = Path(__file__).resolve().parent / "validate_selected_links.py"
        ledger = make_ledger()
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.json"
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            passed = subprocess.run(
                [sys.executable, str(script), str(ledger_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertIn("PASS selected-link validation", passed.stdout)

            ledger["clusters"][0]["items"][0]["url"] = None
            # Model a genuinely missing observed link, not an unaudited mutation
            # of the effective URL on an immutable otherwise-complete capture.
            refresh_runtime_evidence(ledger, reset_raw=True)
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            failed = subprocess.run(
                [sys.executable, str(script), str(ledger_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failed.returncode, 1)
            self.assertIn("微博 selected item requires a non-empty", failed.stderr)


if __name__ == "__main__":
    unittest.main()
