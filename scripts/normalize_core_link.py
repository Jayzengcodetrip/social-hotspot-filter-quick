#!/usr/bin/env python3
"""Resolve and validate one raw selected-item href for a fixed core source."""

from __future__ import annotations

import argparse
import sys

from _core_link_rules import REQUIRED_LINK_SOURCE_IDS, normalize_core_link


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=REQUIRED_LINK_SOURCE_IDS)
    parser.add_argument("href", help="raw absolute, protocol-relative, or relative item href")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    normalized, error = normalize_core_link(args.source, args.href)
    if error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(normalized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
