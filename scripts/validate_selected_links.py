#!/usr/bin/env python3
"""Validate required original-entry links for the canonically selected topics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _core_link_rules import REQUIRED_LINK_SOURCE_IDS, SOURCE_NAMES, validate_selected_core_links
from validate_ranking import load_ledger, validate_and_rank


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="run-ledger JSON path")
    parser.add_argument("--limit", type=int, default=10, help="maximum selected topics")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.limit < 1:
        print("FAIL: --limit must be at least 1", file=sys.stderr)
        return 1
    try:
        ledger = load_ledger(args.ledger)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    ranking_errors, _, selected = validate_and_rank(ledger, args.limit)
    if ranking_errors:
        print("FAIL selected-link validation: ranking ledger is invalid", file=sys.stderr)
        for error in ranking_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    link_errors = validate_selected_core_links(selected)
    if link_errors:
        print("FAIL selected-link validation", file=sys.stderr)
        for error in link_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    required_names = "、".join(SOURCE_NAMES[source_id] for source_id in REQUIRED_LINK_SOURCE_IDS)
    print(
        f"PASS selected-link validation: {len(selected)} selected topics; "
        f"required sources={required_names}; B站、抖音 exempt"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
