#!/usr/bin/env python3
"""Build a quick ledger from this run's saved, same-row browser capture packets.

This is a deterministic data transport step, not a collector or a clustering
model. It neither fetches a website nor manufactures missing links/end evidence.
Partial checkpoints deliberately fail the existing publication validators.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from _core_link_rules import REQUIRED_LINK_SOURCE_IDS, SOURCE_HOST_SUFFIXES, normalize_core_link
from validate_ranking import (
    EXCLUSION_REASONS,
    HOMEPAGE_COMPLETION_SOURCES,
    HOTLIST_EXPECTED_COUNTS,
    SOURCE_IDS,
    SOURCE_TYPES,
    SURFACES,
    SCHEMA_VERSION,
    clustering_review_fingerprint,
    validate_and_rank,
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _assert_equal(record: dict, field: str, expected: Any, label: str) -> None:
    """Fill a mechanical field, rejecting rather than overwriting disagreement."""
    if field in record and (
        record[field] != expected or type(record[field]) is not type(expected)
    ):
        raise ValueError(
            f"{label}.{field} disagrees with captured rows/source contract: "
            f"expected {expected!r}, got {record[field]!r}"
        )
    record[field] = expected


def _run_record(run: dict) -> dict:
    if not isinstance(run, dict):
        raise ValueError("run must be an object")
    for field in ("run_id", "date", "collection_time", "browser_method", "freshness_note"):
        if not _text(run.get(field)):
            raise ValueError(f"run.{field} must be a non-empty string")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run["date"]):
        raise ValueError("run.date must use YYYY-MM-DD")
    for field in ("google_status", "google_limitation"):
        if field in run:
            raise ValueError(f"quick run must omit {field}")
    return copy.deepcopy(run)


def _observed_url(source: str, row: dict, label: str) -> str | None:
    if "url" not in row:
        raise ValueError(f"{label}.url is missing; record its observed href or explicit null")
    url = row["url"]
    if url is None:
        return None
    if not isinstance(url, str):
        raise ValueError(f"{label}.url must be an observed href string or null")
    if not _text(url):
        return None
    if source in REQUIRED_LINK_SOURCE_IDS:
        normalized, error = normalize_core_link(source, url)
        if error:
            return None  # Preserve raw_url; only selected items must be backfilled.
        return normalized
    # The normalizer intentionally has no invented entry-link scheme for the two
    # exempt sources. Keep an actually observed absolute URL, otherwise use a
    # browser-confirmed null in the packet; do not turn relative text into a URL.
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid or url != url.strip():
        return None
    return url


def _item(source: str, row: dict, order: int) -> dict:
    label = f"capture {source!r} rows[{order - 1}]"
    if not isinstance(row, dict):
        raise ValueError(f"{label} must be an object")
    if not _text(row.get("title")):
        raise ValueError(f"{label}.title must be an exact non-empty source title")
    item = copy.deepcopy(row)
    if "raw_url" in row and row["raw_url"] != row.get("url"):
        raise ValueError(f"{label}.raw_url disagrees with immutable capture url")
    item["raw_url"] = row.get("url")
    item["raw_eligible"] = row.get("eligible", True)
    item["raw_exclusion_reason"] = row.get("exclusion_reason")
    for field, expected in (
        ("id", f"{source}-{order:03d}"),
        ("source", source),
        ("display_order", order),
        ("hotlist_position", order if SOURCE_TYPES[source] == "hotlist" else None),
    ):
        _assert_equal(item, field, expected, label)
    item["url"] = _observed_url(source, row, label)

    has_eligible = "eligible" in row
    has_reason = "exclusion_reason" in row
    if has_eligible != has_reason:
        raise ValueError(f"{label} must provide eligible and exclusion_reason together")
    if not has_eligible:
        item.update(eligible=True, exclusion_reason=None)
    if not isinstance(item["eligible"], bool):
        raise ValueError(f"{label}.eligible must be a boolean")
    if item["eligible"] and item["exclusion_reason"] is not None:
        raise ValueError(f"{label}.exclusion_reason must be null for an eligible row")
    if not item["eligible"] and item["exclusion_reason"] not in EXCLUSION_REASONS:
        raise ValueError(f"{label}.exclusion_reason is not an allowed technical exclusion")

    item.setdefault("surface", "hotlist" if SOURCE_TYPES[source] == "hotlist"
                    else "current_issue" if source == "people" else "homepage")
    if item["surface"] not in SURFACES:
        raise ValueError(f"{label}.surface is invalid")
    if item["eligible"]:
        allowed = {"hotlist"} if SOURCE_TYPES[source] == "hotlist" else {"current_issue"} if source == "people" else {"homepage"}
        if item["surface"] not in allowed:
            raise ValueError(f"{label}.surface is outside this source's current candidate surface")
    item.setdefault("publication_date", None)
    if item["publication_date"] is not None and not (
        isinstance(item["publication_date"], str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["publication_date"])
    ):
        raise ValueError(f"{label}.publication_date must use YYYY-MM-DD or null")
    return item


def _source_items(source: str, rows: list) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"capture {source!r}.rows must be an array")
    items = []
    titles: dict[str, str] = {}
    urls: dict[str, str] = {}
    for order, row in enumerate(rows, 1):
        item = _item(source, row, order)
        if item["eligible"]:
            duplicate_of = titles.get(item["title"]) or (
                urls.get(item["url"]) if item["url"] is not None else None
            )
            if duplicate_of is not None:
                item.update(
                    eligible=False,
                    exclusion_reason="technical_duplicate",
                    duplicate_of=duplicate_of,
                )
            else:
                titles[item["title"]] = item["id"]
                if item["url"] is not None:
                    urls[item["url"]] = item["id"]
        items.append(item)
    return items


def _collection(source: str, observed: dict, items: list[dict]) -> dict:
    label = f"capture {source!r}.collection"
    if not isinstance(observed, dict):
        raise ValueError(f"{label} must be an object")
    collection = copy.deepcopy(observed)
    eligible = sum(item["eligible"] for item in items)
    derived = {
        "discovered_count": len(items),
        "eligible_count": eligible,
        "excluded_count": len(items) - eligible,
        "last_item_title": items[-1]["title"] if items else None,
    }
    if source in HOTLIST_EXPECTED_COUNTS:
        derived.update(
            completion_method="fixed_count",
            expected_item_count=HOTLIST_EXPECTED_COUNTS[source],
        )
    elif source in HOMEPAGE_COMPLETION_SOURCES:
        derived["completion_method"] = "homepage_footer_stable"
    else:
        derived["completion_method"] = "current_issue_all_pages"
    for field, value in derived.items():
        _assert_equal(collection, field, value, label)
    # Do not invent scope, footer, quiet rounds, issue dates/pages, end_reached,
    # or end_evidence. The existing validator demands those observed facts.
    return collection


def _clusters(items: list[dict], groups: list[dict] | None) -> list[dict]:
    if groups is None:
        groups = []
    if not isinstance(groups, list):
        raise ValueError("groups must be an array")
    by_id = {item["id"]: item for item in items}
    reserved_singletons = {f"singleton-{item_id}" for item_id in by_id}
    cluster_ids: set[str] = set()
    assigned: set[str] = set()
    clusters = []
    for index, group in enumerate(groups):
        label = f"groups[{index}]"
        if not isinstance(group, dict):
            raise ValueError(f"{label} must be an object")
        unexpected = set(group) - {"id", "normalized_title", "display_title", "items"}
        if unexpected:
            raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(unexpected))}")
        group_id = group.get("id")
        if not _text(group_id):
            raise ValueError(f"{label}.id must be a non-empty string")
        if group_id in cluster_ids or group_id in by_id or group_id in reserved_singletons:
            raise ValueError(f"{label}.id collides with another cluster/item/reserved singleton: {group_id!r}")
        if not _text(group.get("normalized_title")):
            raise ValueError(f"{label}.normalized_title must be a non-empty string")
        if "display_title" in group and not _text(group["display_title"]):
            raise ValueError(f"{label}.display_title must be a non-empty string")
        member_ids = group.get("items")
        if not isinstance(member_ids, list) or not member_ids:
            raise ValueError(f"{label}.items must be a non-empty array of item IDs")
        members: set[str] = set()
        for item_id in member_ids:
            if not isinstance(item_id, str) or item_id not in by_id:
                raise ValueError(f"{label} references unknown item ID: {item_id!r}")
            if item_id in members or item_id in assigned:
                raise ValueError(f"{label} assigns item more than once: {item_id!r}")
            members.add(item_id)
        assigned.update(members)
        cluster_ids.add(group_id)
        cluster = {
            "id": group_id,
            "normalized_title": group["normalized_title"],
            "items": [item for item in items if item["id"] in members],
        }
        if "display_title" in group:
            cluster["display_title"] = group["display_title"]
        clusters.append(cluster)
    clusters.extend(
        {
            "id": f"singleton-{item['id']}",
            "normalized_title": item["title"],
            "items": [item],
        }
        for item in items if item["id"] not in assigned
    )
    return clusters


def _aware_time(value: Any, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("missing timezone")
        return result
    except (ValueError, AttributeError):
        raise ValueError(f"{label} requires an actual timezone-aware ISO timestamp") from None


def _apply_corrections(rows_by_source: dict[str, list], run_id: str,
                       corrections: list | None) -> tuple[dict[str, list], dict[str, list[str]]]:
    """Replay an append-only identity-bound before/after chain without raw writes."""
    if corrections is None:
        corrections = []
    if not isinstance(corrections, list):
        raise ValueError("schema 2 corrections must be an append-only record array; convert the old item map to identity-bound before/after records with observed evidence")
    corrected = copy.deepcopy(rows_by_source)
    by_id = {f"{source}-{order:03d}": (source, order, row)
             for source, rows in corrected.items() for order, row in enumerate(rows, 1)}
    ids: set[str] = set()
    applied: dict[str, list[str]] = {}
    previous_time = None
    for index, record in enumerate(corrections):
        label = f"corrections[{index}]"
        if not isinstance(record, dict):
            raise ValueError(f"{label} must be a record object")
        required = {"id", "run_id", "source", "item_id", "title", "display_order", "corrected_at", "before", "after", "evidence", "observation"}
        if set(record) != required:
            raise ValueError(f"{label} requires exactly {sorted(required)}")
        record_id = record["id"]
        if not _text(record_id) or record_id in ids:
            raise ValueError(f"{label}.id must be a unique non-empty record ID")
        ids.add(record_id)
        if not _text(record["item_id"]) or record["item_id"] not in by_id:
            raise ValueError(f"{label} references unknown captured item ID")
        source, order, row = by_id[record["item_id"]]
        if (record["run_id"] != run_id or record["source"] != source
                or record["title"] != row.get("title") or type(record["display_order"]) is not int
                or record["display_order"] != order):
            raise ValueError(f"{label} run/source/title/order does not match immutable item identity")
        corrected_at = _aware_time(record["corrected_at"], f"{label}.corrected_at")
        if previous_time is not None and corrected_at < previous_time:
            raise ValueError(f"{label} append-only record timestamps must be nondecreasing")
        previous_time = corrected_at
        before, after = record["before"], record["after"]
        if (not isinstance(before, dict) or not isinstance(after, dict) or not before
                or set(before) != set(after) or set(after) - {"url", "eligible", "exclusion_reason"}):
            raise ValueError(f"{label} before/after must contain identical allowed change fields")
        if ("eligible" in after) != ("exclusion_reason" in after):
            raise ValueError(f"{label} eligible and exclusion_reason must be paired")
        for field, value in before.items():
            prior = row.get(field, True if field == "eligible" else None)
            if prior != value or type(prior) is not type(value):
                raise ValueError(f"{label}.before.{field} does not match the current correction chain")
        if before == after:
            raise ValueError(f"{label} must describe an actual correction")
        if not _text(record["evidence"]):
            raise ValueError(f"{label}.evidence must describe a specific same-row/core-page observation")
        observation = record["observation"]
        if not isinstance(observation, dict) or observation.get("observed_title") != row.get("title"):
            raise ValueError(f"{label} observation must retain the exact same-row title")
        observed_at = _aware_time(observation.get("observed_at"), f"{label}.observation.observed_at")
        if observed_at > corrected_at:
            raise ValueError(f"{label} observation cannot occur after its correction record")
        document_url = observation.get("document_url")
        try:
            document = urlsplit(document_url)
            host = document.hostname or ""
            suffix = SOURCE_HOST_SUFFIXES.get(source, "bilibili.com" if source == "bilibili" else "douyin.com")
            same_source = host == suffix or host.endswith("." + suffix)
            if source == "douyin":
                same_source = same_source or host == "iesdouyin.com" or host.endswith(".iesdouyin.com")
            if document.scheme not in {"http", "https"} or not same_source:
                raise ValueError("bad observed document")
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"{label} observation.document_url must be a directly observed same-source page") from None
        if "url" in after:
            if not _text(after["url"]) or observation.get("observed_url") != after["url"]:
                raise ValueError(f"{label} URL must equal the observed same-row URL; null cannot erase a link")
            if _observed_url(source, {"url": after["url"]}, label) is None:
                raise ValueError(f"{label} corrected URL must satisfy the original-entry link contract")
        if "eligible" in after:
            if type(after["eligible"]) is not bool or (after["eligible"] and after["exclusion_reason"] is not None):
                raise ValueError(f"{label} corrected eligibility/reason is invalid")
            if not after["eligible"] and after["exclusion_reason"] not in EXCLUSION_REASONS:
                raise ValueError(f"{label} allows only documented technical exclusions")
        row.update(copy.deepcopy(after))
        applied.setdefault(record["item_id"], []).append(record_id)
    return corrected, applied


def validate_correction_provenance(ledger: dict) -> None:
    """Recompute effective links/exclusions from raw values and the record chain."""
    clusters = ledger.get("clusters")
    if not isinstance(clusters, list) or not isinstance(ledger.get("run"), dict):
        raise ValueError("valid run and cluster records are required before checking corrections")
    all_items = []
    for cluster in clusters:
        if not isinstance(cluster, dict) or not isinstance(cluster.get("items"), list):
            raise ValueError("valid cluster item arrays are required before checking corrections")
        if any(not isinstance(item, dict) for item in cluster["items"]):
            raise ValueError("valid item objects are required before checking corrections")
        all_items.extend(cluster["items"])
    raw_rows = {}
    originals = {}
    for source in SOURCE_IDS:
        items = sorted((item for item in all_items if item.get("source") == source), key=lambda item: item["display_order"])
        originals[source] = items
        rows = []
        for item in items:
            if not {"raw_url", "raw_eligible", "raw_exclusion_reason"} <= set(item):
                raise ValueError(f"item {item.get('id')!r} must preserve raw_url/raw_eligible/raw_exclusion_reason")
            rows.append({"title": item["title"], "url": item["raw_url"],
                         "eligible": item["raw_eligible"], "exclusion_reason": item["raw_exclusion_reason"],
                         "surface": item["surface"], "publication_date": item.get("publication_date")})
        raw_rows[source] = rows
    corrected, applied = _apply_corrections(raw_rows, ledger.get("run", {}).get("run_id"), ledger.get("corrections", []))
    for source in SOURCE_IDS:
        expected = _source_items(source, corrected[source])
        for item, rebuilt in zip(originals[source], expected):
            for field in ("url", "eligible", "exclusion_reason"):
                if item.get(field) != rebuilt.get(field) or type(item.get(field)) is not type(rebuilt.get(field)):
                    raise ValueError(f"item {item.get('id')!r}.{field} differs from raw capture and correction records")
            if item.get("correction_ids", []) != applied.get(item.get("id"), []):
                raise ValueError(f"item {item.get('id')!r}.correction_ids differs from the correction chain")


def build_ledger(
    captures: dict[str, dict],
    run: dict,
    groups: list[dict] | None = None,
    *,
    partial: bool = False,
    corrections: list | None = None,
    review: dict | None = None,
) -> dict:
    """Return a lossless, deterministic ledger, or reject a concrete input error.

    ``partial=True`` permits absent source packets and unproven source endings
    for internal checkpoints only. It never turns them into read/completed facts
    and its report_mode intentionally cannot pass publication validation.
    """
    run_record = _run_record(run)
    if not isinstance(captures, dict):
        raise ValueError("captures must map fixed source IDs to capture packet objects")
    unknown = set(captures) - set(SOURCE_IDS)
    if unknown:
        raise ValueError(f"unknown capture sources: {', '.join(sorted(map(str, unknown)))}")
    missing = [source for source in SOURCE_IDS if source not in captures]
    if missing and not partial:
        raise ValueError(f"missing capture packets: {', '.join(missing)}")
    source_rows = []
    all_items = []
    # First reconcile the unchanged capture evidence. Corrections are a separate
    # explicit overlay and never rewrite source packet files or original titles.
    baseline_sources = {}
    for source in SOURCE_IDS:
        if source not in captures:
            continue
        packet = captures[source]
        if not isinstance(packet, dict):
            raise ValueError(f"capture {source!r} must be an object")
        if packet.get("source") != source:
            raise ValueError(f"capture {source!r}.source does not match its packet filename/key")
        if packet.get("run_id") != run_record["run_id"]:
            raise ValueError(f"capture {source!r}.run_id does not match the current run")
        items = _source_items(source, packet.get("rows"))
        collection = _collection(source, packet.get("collection"), items)
        baseline_sources[source] = (items, collection)
    corrected_sources, applied_records = _apply_corrections(
        {source: captures[source]["rows"] for source in baseline_sources}, run_record["run_id"], corrections)
    for source in SOURCE_IDS:
        if source not in baseline_sources:
            continue
        items, collection = baseline_sources[source]
        if any(item["id"] in applied_records for item in items):
            original_items = {item["id"]: item for item in items}
            items = _source_items(source, corrected_sources[source])
            for item in items:
                original = original_items[item["id"]]
                for field in ("raw_url", "raw_eligible", "raw_exclusion_reason"):
                    item[field] = original[field]
                if item["id"] in applied_records:
                    item["correction_ids"] = applied_records[item["id"]]
            # The baseline reconciliation above already checked supplied counts.
            # Only eligibility counts can change after an explicit correction;
            # discovered count, order, title, source, and end evidence cannot.
            collection.pop("eligible_count", None)
            collection.pop("excluded_count", None)
            collection = _collection(source, collection, items)
        source_rows.append({"id": source, "status": "read", "reason": "", "collection": collection})
        all_items.extend(items)
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "report_mode": "quick-partial" if partial else "quick",
        "run": run_record,
        "sources": source_rows,
        "clusters": _clusters(all_items, groups),
        "corrections": copy.deepcopy(corrections or []),
    }
    if review is not None:
        ledger["clustering_review"] = copy.deepcopy(review)
    if not partial:
        if groups is None:
            raise ValueError("full build requires an explicit groups file (an reviewed empty array is allowed); use --partial and --review-template to prepare the review")
        errors, _, _ = validate_and_rank(ledger, 10)
        if errors:
            raise ValueError("capture ledger validation failed:\n- " + "\n- ".join(errors))
    return ledger


def review_template(ledger: dict) -> dict:
    """A pending template never claims that semantic review has happened."""
    eligible = sorted(item["id"] for cluster in ledger["clusters"] for item in cluster["items"] if item["eligible"])
    return {"status": "pending", "run_id": ledger["run"]["run_id"], "reviewed_at": None,
            "reviewed_item_ids": eligible, "decision_sha256": clustering_review_fingerprint(ledger)}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc


def load_captures(directory: Path) -> dict[str, dict]:
    if not directory.is_dir():
        raise ValueError(f"capture directory does not exist: {directory}")
    captures = {}
    for path in sorted(directory.glob("*.json")):
        if path.stem not in SOURCE_IDS:
            raise ValueError(f"unexpected capture JSON {path.name}; keep only <source>.json packets here")
        captures[path.stem] = _load_json(path)
    return captures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", required=True, type=Path, help="directory of <source>.json capture packets")
    parser.add_argument("--run", required=True, type=Path, help="run metadata JSON including run_id")
    parser.add_argument("--groups", type=Path, help="array of cluster labels and item IDs; required for full build (may be []), optional only with --partial")
    parser.add_argument("--corrections", type=Path, help="optional append-only identity-bound before/after correction records")
    parser.add_argument("--review", type=Path, help="completed semantic review JSON bound to this run and grouping")
    parser.add_argument("--review-template", type=Path, help="with --partial, create a fresh pending review template; never marks review complete")
    parser.add_argument("--output", required=True, type=Path, help="ledger JSON output")
    parser.add_argument("--partial", action="store_true", help="write a non-publishable internal checkpoint only")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.review_template and not args.partial:
            raise ValueError("--review-template requires --partial; complete actual clustering review before publishing")
        captures = load_captures(args.captures)
        ledger = build_ledger(
            captures,
            _load_json(args.run),
            _load_json(args.groups) if args.groups else None,
            partial=args.partial,
            corrections=_load_json(args.corrections) if args.corrections else None,
            review=_load_json(args.review) if args.review else None,
        )
        # Never replace a capture packet or run/group source with derived output.
        inputs = [args.run, *(args.captures / f"{source}.json" for source in captures)]
        if args.groups:
            inputs.append(args.groups)
        if args.corrections:
            inputs.append(args.corrections)
        if args.review:
            inputs.append(args.review)
        if args.output.resolve() in {path.resolve() for path in inputs}:
            raise ValueError("output must not overwrite a capture packet, run, or groups input")
        args.output.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.review_template:
            if args.review_template.resolve() in {args.output.resolve(), *(path.resolve() for path in inputs)}:
                raise ValueError("review template must not overwrite a ledger or input file")
            with args.review_template.open("x", encoding="utf-8") as stream:
                json.dump(review_template(ledger), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    count = sum(len(cluster["items"]) for cluster in ledger["clusters"])
    if args.partial:
        print(f"CHECKPOINT ONLY: {len(captures)}/9 sources, {count} rows; publication is disabled")
    else:
        print(f"PASS capture ledger: 9/9 sources, {count} rows; selected-link and report gates still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
