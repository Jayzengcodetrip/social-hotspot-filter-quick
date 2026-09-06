#!/usr/bin/env python3
"""Independently validate a quick Markdown report against its complete ledger."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from _core_link_rules import validate_selected_core_links
from validate_ranking import SOURCE_IDS, SOURCE_INDEX, SOURCE_NAMES, load_ledger, validate_and_rank
from _report_utils import (
    NO_TOPIC_SENTENCE, SOURCE_NOTE_FIELDS, TABLE_HEADER, TABLE_SEPARATOR,
    escape_link_text as _escape_link_text, escape_table as _escape_table,
    expected_source_notes, report_title, split_table_row as _split_table_row,
)


def _expected_original_lines(entry: dict[str, Any]) -> list[str]:
    items = [item for item in entry["cluster"]["items"] if item.get("eligible") is True]
    items.sort(key=lambda item: (SOURCE_INDEX[item["source"]], item["display_order"]))
    lines: list[str] = []
    for source_id in SOURCE_IDS:
        source_items = [item for item in items if item["source"] == source_id]
        if not source_items:
            continue
        lines.append(f"- {SOURCE_NAMES[source_id]}：")
        for item in source_items:
            title = str(item["title"])
            url = item.get("url")
            if isinstance(url, str) and url:
                lines.append(f"  - [{_escape_link_text(title)}]({url})")
            else:
                lines.append(f"  - {title}")
    return lines


def _eligible_source_counts(entry: dict[str, Any]) -> dict[str, int]:
    counts = {source_id: 0 for source_id in SOURCE_IDS}
    for item in entry["cluster"]["items"]:
        if item.get("eligible") is True:
            counts[item["source"]] += 1
    return {source_id: counts[source_id] for source_id in SOURCE_IDS if counts[source_id]}


def _format_coverage(entry: dict[str, Any]) -> str:
    counts = _eligible_source_counts(entry)
    platforms = "、".join(SOURCE_NAMES[source_id] for source_id in counts)
    return f"{entry['coverage']}（{platforms}）"


def _format_hits(entry: dict[str, Any]) -> str:
    counts = _eligible_source_counts(entry)
    breakdown = "、".join(
        f"{SOURCE_NAMES[source_id]}{count}" for source_id, count in counts.items()
    )
    return f"{entry['hit_count']}（{breakdown}）"



def _nonblank_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def validate_report(ledger: dict[str, Any], report: str, limit: int) -> list[str]:
    errors, _, selected = validate_and_rank(ledger, limit)
    if errors:
        return ["ranking ledger failed validation: " + error for error in errors]
    errors.extend(validate_selected_core_links(selected))
    notes = expected_source_notes(ledger)
    expected_preamble = [report_title(limit)]
    expected_preamble.extend(f"- {field}：{notes[field]}" for field in SOURCE_NOTE_FIELDS)
    lines = report.splitlines()

    if lines.count(report_title(limit)) != 1:
        errors.append("report must contain the exact quick-edition H1 title once")
    # Exact scope avoids both invisible full-edition sections and invented extra prose.
    # These checks apply to headings, not words occurring in legitimate original titles.
    if re.search(r"^#{1,6}\s+(?:补充核实来源|事件概述|核心来源采集完整性)(?:\s|$)", report, re.MULTILINE):
        errors.append("quick report must not contain supplementary, summary, or legacy completeness sections")
    if re.search(r"^- Google补充核实：", report, re.MULTILINE):
        errors.append("quick report must use 版本说明, not a Google completion note")

    if not selected:
        expected = expected_preamble + [NO_TOPIC_SENTENCE]
        if _nonblank_lines(report) != expected:
            errors.append("no-topic report must contain only the quick source notes and the exact no-topic sentence")
        return errors

    headings = list(re.finditer(r"^## (\d+)\. (.+)$", report, re.MULTILINE))
    if len(headings) != len(selected):
        errors.append(f"report has {len(headings)} topic sections; expected {len(selected)}")
    overview = report[:headings[0].start()] if headings else report
    overview_lines = _nonblank_lines(overview)
    try:
        header_index = overview_lines.index(TABLE_HEADER)
    except ValueError:
        errors.append("report is missing the exact overview-table header")
        header_index = -1
    if header_index >= 0:
        if overview_lines[:header_index] != expected_preamble:
            errors.append("source notes must exactly match the quick ledger in fixed order")
        if overview_lines[header_index + 1:header_index + 2] != [TABLE_SEPARATOR]:
            errors.append("overview table is missing the exact separator row")
        table_lines = overview_lines[header_index + 2:]
        if len(table_lines) != len(selected):
            errors.append(f"overview table has {len(table_lines)} data rows; expected {len(selected)}")
        for index, (entry, line) in enumerate(zip(selected, table_lines), start=1):
            expected = [
                str(entry["rank"]), _escape_table(entry["display_title"]),
                _format_coverage(entry), _format_hits(entry),
            ]
            if _split_table_row(line) != expected:
                errors.append(f"overview row {index} does not match canonical ranking and ledger")

    for index, (entry, heading) in enumerate(zip(selected, headings), start=1):
        if heading.group(0) != f"## {entry['rank']}. {entry['display_title']}":
            errors.append(f"topic heading {index} does not match canonical ranking")
        end = headings[index].start() if index < len(headings) else len(report)
        actual_lines = _nonblank_lines(report[heading.end():end])
        expected_lines = ["### 各平台原始标题"] + _expected_original_lines(entry)
        if actual_lines != expected_lines:
            errors.append(f"topic {index} original-title block does not exactly match eligible ledger items")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 1:
        print("FAIL: --limit must be at least 1", file=sys.stderr)
        return 1
    try:
        ledger = load_ledger(args.ledger)
        report = args.report.read_text(encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"FAIL: cannot read validation input: {exc}", file=sys.stderr)
        return 1
    errors = validate_report(ledger, report, args.limit)
    if errors:
        print("FAIL quick report validation", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PASS quick report validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
