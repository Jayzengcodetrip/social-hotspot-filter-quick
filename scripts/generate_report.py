#!/usr/bin/env python3
"""Generate a quick report after collection, ranking, and selected-link prechecks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _core_link_rules import validate_selected_core_links
from _report_utils import (
    NO_TOPIC_SENTENCE, SOURCE_NOTE_FIELDS, TABLE_HEADER, TABLE_SEPARATOR,
    escape_table, expected_source_notes, report_title,
)
from validate_ranking import (
    load_ledger, ranking_payload, validate_and_rank,
)
from validate_report import (
    _expected_original_lines, _format_coverage, _format_hits, validate_report,
)


def build_ranking_payload(
    ledger: dict[str, Any], limit: int
) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    errors, qualified, selected = validate_and_rank(ledger, limit)
    if errors:
        return errors, {}, []
    payload = ranking_payload(ledger, limit, qualified, selected)
    return [], payload, selected


def generate_report(
    ledger: dict[str, Any], ranking: dict[str, Any]
) -> tuple[str, list[str]]:
    limit = ranking.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return "", ["ranking.limit must be a positive integer"]
    errors, canonical, selected = build_ranking_payload(ledger, limit)
    if errors:
        return "", ["ranking ledger failed validation: " + error for error in errors]
    if ranking != canonical:
        return "", ["ranking JSON does not exactly match the current ledger; rerun validate_ranking.py"]
    link_errors = validate_selected_core_links(selected)
    if link_errors:
        return "", link_errors

    notes = expected_source_notes(ledger)
    lines = [report_title(limit), ""]
    lines.extend(f"- {field}：{notes[field]}" for field in SOURCE_NOTE_FIELDS)
    lines.append("")
    if not selected:
        lines.extend((NO_TOPIC_SENTENCE, ""))
    else:
        lines.extend((TABLE_HEADER, TABLE_SEPARATOR))
        for entry in selected:
            lines.append(
                f"| {entry['rank']} | {escape_table(entry['display_title'])} | "
                f"{_format_coverage(entry)} | {_format_hits(entry)} |"
            )
        lines.append("")
        for entry in selected:
            lines.extend((
                f"## {entry['rank']}. {entry['display_title']}", "",
                "### 各平台原始标题", "",
            ))
            lines.extend(_expected_original_lines(entry))
            lines.append("")
    report = "\n".join(lines)
    errors = validate_report(ledger, report, limit)
    return ("", errors) if errors else (report, [])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("ranking", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        ledger = load_ledger(args.ledger)
        ranking = load_ledger(args.ranking)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    report, errors = generate_report(ledger, ranking)
    if errors:
        print("FAIL quick report generation precheck", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except OSError as exc:
        print(f"FAIL: cannot write quick report: {exc}", file=sys.stderr)
        return 1
    print(f"PASS quick report generation: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
