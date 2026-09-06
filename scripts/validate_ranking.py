#!/usr/bin/env python3
"""Validate a quick-edition ledger and compute the unchanged core ranking."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any


SOURCE_INFO = (
    ("weibo", "微博", "hotlist"),
    ("zhihu", "知乎", "hotlist"),
    ("bilibili", "B站", "hotlist"),
    ("baidu", "百度", "hotlist"),
    ("douyin", "抖音", "hotlist"),
    ("jiemian", "界面新闻", "news"),
    ("bjnews", "新京报", "news"),
    ("caixin", "财新", "news"),
    ("people", "人民日报", "news"),
)
SOURCE_IDS = tuple(item[0] for item in SOURCE_INFO)
SOURCE_NAMES = {item[0]: item[1] for item in SOURCE_INFO}
SOURCE_TYPES = {item[0]: item[2] for item in SOURCE_INFO}
SOURCE_INDEX = {source_id: index for index, source_id in enumerate(SOURCE_IDS)}
HOTLIST_EXPECTED_COUNTS = {
    "weibo": 50,
    "zhihu": 30,
    "bilibili": 30,
    "baidu": 50,
    "douyin": 50,
}
HOMEPAGE_COMPLETION_SOURCES = {"jiemian", "bjnews", "caixin"}
EXCLUSION_REASONS = {
    "technical_duplicate",
    "broken",
    "fabricated",
    "instruction_polluted",
    "not_current_surface",
    "spam_or_unidentifiable",
    "unreliable_cluster_match",
}
SCHEMA_VERSION = 2
REPORT_MODE = "quick"
SURFACES = {"hotlist", "homepage", "current_issue", "other"}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def clustering_review_fingerprint(ledger: dict[str, Any]) -> str:
    """Bind semantic review to identities/decisions, not pure link backfill.

    Data array serialization order is irrelevant. Source display order, exact
    titles, eligibility and cluster allocation are not. A link inspection that
    changes identity must repair the allocation and obtain a fresh review.
    """
    clusters = []
    cluster_rows = ledger.get("clusters")
    for cluster in cluster_rows if isinstance(cluster_rows, list) else []:
        if not isinstance(cluster, dict):
            continue
        members = cluster.get("items")
        items = [{key: item.get(key) for key in (
            "id", "source", "title", "display_order", "hotlist_position",
            "surface", "eligible", "exclusion_reason",
        )} for item in (members if isinstance(members, list) else []) if isinstance(item, dict)]
        clusters.append({"id": cluster.get("id"), "normalized_title": cluster.get("normalized_title"),
                         "display_title": cluster.get("display_title", cluster.get("normalized_title")),
                         "items": sorted(items, key=lambda item: str(item.get("id")))})
    run = ledger.get("run")
    return canonical_sha256({"run_id": run.get("run_id") if isinstance(run, dict) else None,
                             "clusters": sorted(clusters, key=lambda cluster: str(cluster.get("id")))})


def _validate_clustering_review(ledger: dict[str, Any], errors: list[str]) -> None:
    review = ledger.get("clustering_review")
    if not isinstance(review, dict) or review.get("status") != "complete":
        errors.append("clustering_review must explicitly be complete; finish semantic review of all eligible items, including singletons, or use --partial")
        return
    run = ledger.get("run") if isinstance(ledger.get("run"), dict) else {}
    if review.get("run_id") != run.get("run_id"):
        errors.append("clustering_review.run_id must match this run")
    stamp = None
    try:
        stamp = datetime.fromisoformat(str(review.get("reviewed_at", "")).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError:
        stamp = None
        errors.append("clustering_review.reviewed_at must be an actual timezone-aware ISO timestamp")
    if stamp is not None:
        # Review can precede a later same-item URL backfill, but cannot precede
        # the candidate collection whose semantic assignments it certifies.
        source_rows = ledger.get("sources", [])
        for source in source_rows if isinstance(source_rows, list) else []:
            try:
                raw_time = source["collection"]["runtime_evidence"]["trace"]["captured_at"]
                captured = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if captured.tzinfo is not None and stamp < captured:
                    errors.append("clustering_review.reviewed_at cannot precede source capture completion")
                    break
            except (KeyError, TypeError, AttributeError, ValueError):
                pass  # Source-evidence validation reports its own missing/invalid data.
    eligible_ids = set()
    cluster_rows = ledger.get("clusters")
    for cluster in cluster_rows if isinstance(cluster_rows, list) else []:
        members = cluster.get("items") if isinstance(cluster, dict) else None
        for item in members if isinstance(members, list) else []:
            if isinstance(item, dict) and item.get("eligible") is True and isinstance(item.get("id"), str):
                eligible_ids.add(item["id"])
    ids = review.get("reviewed_item_ids")
    if (not isinstance(ids, list) or not all(isinstance(value, str) for value in ids)
            or len(ids) != len(set(ids)) or set(ids) != eligible_ids):
        errors.append("clustering_review.reviewed_item_ids must cover every eligible item exactly once, including reviewed singletons")
    if review.get("decision_sha256") != clustering_review_fingerprint(ledger):
        errors.append("clustering_review.decision_sha256 is stale; review the changed identities/grouping and record a new review")


def load_ledger(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read ledger JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("ledger root must be a JSON object")
    return value


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_source_collections(
    sources: dict[str, dict[str, Any]],
    source_items: dict[str, list[dict[str, Any]]],
    run: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate that every read source proves complete candidate extraction."""
    for source_id in SOURCE_IDS:
        row = sources.get(source_id)
        if not isinstance(row, dict) or row.get("status") != "read":
            continue
        label = f"source {source_id!r} collection"
        collection = row.get("collection")
        if not isinstance(collection, dict):
            errors.append(f"{label} must be an object for a read source")
            continue

        if not _nonempty_string(collection.get("scope")):
            errors.append(f"{label}.scope must be a non-empty string")
        method = collection.get("completion_method")
        discovered = collection.get("discovered_count")
        eligible = collection.get("eligible_count")
        excluded = collection.get("excluded_count")
        if not _positive_int(discovered):
            errors.append(f"{label}.discovered_count must be a positive integer")
        if not _nonnegative_int(eligible):
            errors.append(f"{label}.eligible_count must be a non-negative integer")
        if not _nonnegative_int(excluded):
            errors.append(f"{label}.excluded_count must be a non-negative integer")
        if (
            _positive_int(discovered)
            and _nonnegative_int(eligible)
            and _nonnegative_int(excluded)
            and discovered != eligible + excluded
        ):
            errors.append(
                f"{label}.discovered_count must equal eligible_count + excluded_count"
            )

        items = source_items[source_id]
        actual_discovered = len(items)
        actual_eligible = sum(item.get("eligible") is True for item in items)
        actual_excluded = actual_discovered - actual_eligible
        expected_actual = {
            "discovered_count": actual_discovered,
            "eligible_count": actual_eligible,
            "excluded_count": actual_excluded,
        }
        for field, actual in expected_actual.items():
            if collection.get(field) != actual:
                errors.append(
                    f"{label}.{field} does not match ledger items: "
                    f"expected {actual}, got {collection.get(field)!r}"
                )

        if _positive_int(discovered):
            orders = sorted(
                item.get("display_order")
                for item in items
                if _positive_int(item.get("display_order"))
            )
            expected_orders = list(range(1, discovered + 1))
            if orders != expected_orders:
                errors.append(
                    f"{label} display_order values must cover every position "
                    f"from 1 through {discovered} exactly once"
                )
            if orders:
                last_item = max(
                    items,
                    key=lambda item: (
                        item.get("display_order")
                        if _positive_int(item.get("display_order"))
                        else -1
                    ),
                )
                last_title = collection.get("last_item_title")
                if not _nonempty_string(last_title):
                    errors.append(f"{label}.last_item_title must be a non-empty string")
                elif last_title != last_item.get("title"):
                    errors.append(
                        f"{label}.last_item_title must match the final recorded item title"
                    )

        if collection.get("end_reached") is not True:
            errors.append(f"{label}.end_reached must be true")
        if not _nonempty_string(collection.get("end_evidence")):
            errors.append(f"{label}.end_evidence must be a non-empty string")

        try:
            from source_evidence import verify_runtime_evidence
            raw_rows = [{"title": item.get("title"), "url": item.get("raw_url")}
                        for item in sorted(items, key=lambda item: item.get("display_order")
                                           if _positive_int(item.get("display_order")) else -1)]
            if any("raw_url" not in item for item in items):
                raise ValueError("each item must preserve raw_url from its immutable capture")
            derived = verify_runtime_evidence(collection.get("runtime_evidence"), source_id,
                                               run.get("run_id"), raw_rows, run_date=run.get("date"))
            for field, value in derived.items():
                if field not in {"eligible_count", "excluded_count", "runtime_evidence"} and collection.get(field) != value:
                    errors.append(f"{label}.{field} differs from replayed runtime evidence")
        except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"{label} runtime evidence failed: {exc}")

        if source_id in HOTLIST_EXPECTED_COUNTS:
            expected_count = HOTLIST_EXPECTED_COUNTS[source_id]
            if method != "fixed_count":
                errors.append(f"{label}.completion_method must equal 'fixed_count'")
            if collection.get("expected_item_count") != expected_count:
                errors.append(
                    f"{label}.expected_item_count must equal {expected_count}"
                )
            if discovered != expected_count:
                errors.append(
                    f"{label}.discovered_count must equal fixed hot-list count "
                    f"{expected_count}"
                )
        elif source_id in HOMEPAGE_COMPLETION_SOURCES:
            if method != "homepage_footer_stable":
                errors.append(
                    f"{label}.completion_method must equal 'homepage_footer_stable'"
                )
            if collection.get("footer_reached") is not True:
                errors.append(f"{label}.footer_reached must be true")
            if collection.get("page_height_stable") is not True:
                errors.append(f"{label}.page_height_stable must be true")
            no_new_rounds = collection.get("no_new_item_rounds")
            if not _nonnegative_int(no_new_rounds) or no_new_rounds < 2:
                errors.append(f"{label}.no_new_item_rounds must be at least 2")
        elif source_id == "people":
            if method != "current_issue_all_pages":
                errors.append(
                    f"{label}.completion_method must equal 'current_issue_all_pages'"
                )
            if collection.get("issue_date") != run.get("date"):
                errors.append(f"{label}.issue_date must equal run.date")
            total_pages = collection.get("issue_total_pages")
            read_pages = collection.get("issue_read_pages")
            if not _positive_int(total_pages):
                errors.append(f"{label}.issue_total_pages must be a positive integer")
            if not _positive_int(read_pages):
                errors.append(f"{label}.issue_read_pages must be a positive integer")
            if _positive_int(total_pages) and read_pages != total_pages:
                errors.append(
                    f"{label}.issue_read_pages must equal issue_total_pages"
                )


def validate_and_rank(
    ledger: dict[str, Any], limit: int, *, enforce_source_completeness: bool = True
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[str] = []
    if ledger.get("schema_version") != SCHEMA_VERSION or isinstance(ledger.get("schema_version"), bool):
        errors.append("quick schema_version must equal 2; version 1 cannot publish: rebuild from current captures with runtime evidence and completed clustering review")
    if ledger.get("report_mode") != REPORT_MODE:
        errors.append("report_mode must equal quick; full-edition ledgers are not accepted")
    if not _positive_int(limit):
        return ["limit must be a positive integer"], [], []

    run = ledger.get("run")
    if not isinstance(run, dict):
        errors.append("run must be an object")
        run = {}
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(run.get("date", ""))):
        errors.append("run.date must use YYYY-MM-DD")
    for field in ("run_id", "collection_time", "browser_method", "freshness_note"):
        if not _nonempty_string(run.get(field)):
            errors.append(f"run.{field} must be a non-empty string")
    for field in ("google_status", "google_limitation"):
        if field in run:
            errors.append(f"quick run must omit {field}; supplementary research is out of scope")

    source_rows = ledger.get("sources")
    if not isinstance(source_rows, list):
        errors.append("sources must be an array")
        source_rows = []
    sources: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(source_rows):
        label = f"sources[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        source_id = row.get("id")
        if source_id not in SOURCE_IDS:
            errors.append(f"{label}.id is not a fixed core source: {source_id!r}")
            continue
        if source_id in sources:
            errors.append(f"source {source_id!r} appears more than once")
            continue
        status = row.get("status")
        if status not in {"read", "inaccessible"}:
            errors.append(f"{label}.status must be read or inaccessible")
        reason = row.get("reason", "")
        if not isinstance(reason, str):
            errors.append(f"{label}.reason must be a string")
        if status == "inaccessible" and not str(reason).strip():
            errors.append(f"{label}.reason is required when status is inaccessible")
        if status == "read" and str(reason).strip():
            errors.append(f"{label}.reason must be empty when status is read")
        sources[source_id] = row
    missing_sources = [source_id for source_id in SOURCE_IDS if source_id not in sources]
    if missing_sources:
        errors.append("missing fixed source records: " + ", ".join(missing_sources))
    unreadable_sources = [
        source_id
        for source_id in SOURCE_IDS
        if source_id in sources and sources[source_id].get("status") != "read"
    ]
    if unreadable_sources:
        errors.append(
            "all nine fixed core sources must be marked read before ranking; not read: "
            + ", ".join(unreadable_sources)
        )

    clusters = ledger.get("clusters")
    if not isinstance(clusters, list):
        errors.append("clusters must be an array")
        clusters = []

    cluster_ids: set[str] = set()
    item_ids: set[str] = set()
    global_titles: dict[tuple[str, str], str] = {}
    global_urls: dict[tuple[str, str], str] = {}
    global_orders: dict[tuple[str, int], str] = {}
    source_items: dict[str, list[dict[str, Any]]] = {
        source_id: [] for source_id in SOURCE_IDS
    }
    ranked: list[dict[str, Any]] = []

    for cluster_index, cluster in enumerate(clusters):
        cluster_label = f"clusters[{cluster_index}]"
        if not isinstance(cluster, dict):
            errors.append(f"{cluster_label} must be an object")
            continue
        for field in ("verification", "event_summary", "closure"):
            if field in cluster:
                errors.append(f"{cluster_label}: quick clusters must omit {field}")
        cluster_id = cluster.get("id")
        if not _nonempty_string(cluster_id):
            errors.append(f"{cluster_label}.id must be a non-empty string")
            cluster_id = f"invalid-cluster-{cluster_index}"
        elif cluster_id in cluster_ids:
            errors.append(f"duplicate cluster id: {cluster_id!r}")
        cluster_ids.add(str(cluster_id))

        normalized_title = cluster.get("normalized_title")
        if not _nonempty_string(normalized_title):
            errors.append(f"{cluster_label}.normalized_title must be a non-empty string")
            normalized_title = str(cluster_id)
        display_title = cluster.get("display_title", normalized_title)
        if not _nonempty_string(display_title):
            errors.append(f"{cluster_label}.display_title must be a non-empty string when present")
            display_title = normalized_title

        items = cluster.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"{cluster_label}.items must be a non-empty array")
            items = []

        eligible_items: list[dict[str, Any]] = []
        local_titles: set[tuple[str, str]] = set()
        local_urls: set[tuple[str, str]] = set()
        for item_index, item in enumerate(items):
            item_label = f"{cluster_label}.items[{item_index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_label} must be an object")
                continue
            item_id = item.get("id")
            if not _nonempty_string(item_id):
                errors.append(f"{item_label}.id must be a non-empty string")
                item_id = f"invalid-item-{cluster_index}-{item_index}"
            elif item_id in item_ids:
                errors.append(f"duplicate item id: {item_id!r}")
            item_ids.add(str(item_id))

            source_id = item.get("source")
            if source_id not in SOURCE_IDS:
                errors.append(f"{item_label}.source is not a fixed core source: {source_id!r}")
                continue
            source_items[source_id].append(item)
            if sources.get(source_id, {}).get("status") != "read":
                errors.append(f"{item_label} belongs to source {source_id!r}, which is not marked read")

            title = item.get("title")
            if not _nonempty_string(title):
                errors.append(f"{item_label}.title must be a non-empty string")
                title = str(item_id)
            url = item.get("url")
            if url is not None and not _nonempty_string(url):
                errors.append(f"{item_label}.url must be a non-empty string or null")
            if isinstance(url, str) and not re.match(r"^https?://", url):
                errors.append(f"{item_label}.url must start with http:// or https://")

            surface = item.get("surface")
            if surface not in SURFACES:
                errors.append(f"{item_label}.surface must be one of {sorted(SURFACES)}")
            display_order = item.get("display_order")
            if not _positive_int(display_order):
                errors.append(f"{item_label}.display_order must be a positive integer")
            hotlist_position = item.get("hotlist_position")
            if hotlist_position is not None and not _positive_int(hotlist_position):
                errors.append(f"{item_label}.hotlist_position must be a positive integer or null")

            eligible = item.get("eligible")
            if not isinstance(eligible, bool):
                errors.append(f"{item_label}.eligible must be true or false")
                eligible = False
            exclusion_reason = item.get("exclusion_reason")
            if eligible and exclusion_reason not in {None, ""}:
                errors.append(f"{item_label}.exclusion_reason must be null for an eligible item")
            if not eligible and exclusion_reason not in EXCLUSION_REASONS:
                errors.append(
                    f"{item_label}.exclusion_reason must be an allowed technical reason for an excluded item"
                )

            publication_date = item.get("publication_date")
            if publication_date is not None and not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", str(publication_date)
            ):
                errors.append(f"{item_label}.publication_date must use YYYY-MM-DD or null")

            if not eligible:
                continue
            if SOURCE_TYPES[source_id] == "hotlist":
                if surface != "hotlist":
                    errors.append(f"{item_label} is an eligible hot-list item but surface is not hotlist")
                if hotlist_position != display_order:
                    errors.append(
                        f"{item_label}.hotlist_position must equal display_order for an eligible hot-list item"
                    )
            else:
                expected_surface = "current_issue" if source_id == "people" else "homepage"
                if surface != expected_surface:
                    errors.append(
                        f"{item_label} is an eligible {source_id} item; surface must equal {expected_surface} (current_section is not eligible)"
                    )
                if hotlist_position is not None:
                    errors.append(f"{item_label} is a news/event item and must not have a hot-list position")

            title_key = (source_id, str(title))
            if title_key in local_titles:
                errors.append(f"{cluster_label} counts the same source title twice: {title!r}")
            local_titles.add(title_key)
            if title_key in global_titles and global_titles[title_key] != str(cluster_id):
                errors.append(
                    f"source title {title!r} is assigned to multiple clusters: "
                    f"{global_titles[title_key]!r} and {cluster_id!r}"
                )
            global_titles[title_key] = str(cluster_id)

            if isinstance(url, str):
                url_key = (source_id, url)
                if url_key in local_urls:
                    errors.append(f"{cluster_label} counts the same source URL twice: {url!r}")
                local_urls.add(url_key)
                if url_key in global_urls and global_urls[url_key] != str(cluster_id):
                    errors.append(
                        f"source URL {url!r} is assigned to multiple clusters: "
                        f"{global_urls[url_key]!r} and {cluster_id!r}"
                    )
                global_urls[url_key] = str(cluster_id)

            if _positive_int(display_order):
                order_key = (source_id, display_order)
                if order_key in global_orders:
                    errors.append(
                        f"source {source_id!r} display_order {display_order} is counted by both "
                        f"{global_orders[order_key]!r} and {item_id!r}"
                    )
                global_orders[order_key] = str(item_id)
            eligible_items.append(item)

        hit_count = len(eligible_items)
        source_ids = sorted(
            {str(item["source"]) for item in eligible_items}, key=SOURCE_INDEX.__getitem__
        )
        coverage = len(source_ids)
        positions = [
            int(item["hotlist_position"])
            for item in eligible_items
            if _positive_int(item.get("hotlist_position"))
        ]
        average_position = Fraction(sum(positions), len(positions)) if positions else None
        first_hit = min(
            (
                SOURCE_INDEX[str(item["source"])],
                int(item["display_order"]) if _positive_int(item.get("display_order")) else 10**9,
            )
            for item in eligible_items
        ) if eligible_items else (10**9, 10**9)
        ranked.append(
            {
                "id": str(cluster_id),
                "normalized_title": str(normalized_title),
                "display_title": str(display_title),
                "hit_count": hit_count,
                "coverage": coverage,
                "source_ids": source_ids,
                "source_names": [SOURCE_NAMES[source_id] for source_id in source_ids],
                "average_position": average_position,
                "first_hit": first_hit,
                "cluster": cluster,
            }
        )

    if enforce_source_completeness:
        _validate_source_collections(sources, source_items, run, errors)
        _validate_clustering_review(ledger, errors)
        try:
            from build_capture_ledger import validate_correction_provenance
            validate_correction_provenance(ledger)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"capture correction provenance failed: {exc}")

    def sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
        average = entry["average_position"]
        average_key = (1, Fraction(0, 1)) if average is None else (0, average)
        return (
            -entry["coverage"],
            -entry["hit_count"],
            average_key,
            entry["first_hit"],
            entry["normalized_title"],
        )

    qualified = sorted((entry for entry in ranked if entry["coverage"] >= 2), key=sort_key)
    previous_score: tuple[int, int] | None = None
    competition_rank = 0
    for position, entry in enumerate(qualified, start=1):
        score = (entry["coverage"], entry["hit_count"])
        if score != previous_score:
            competition_rank = position
            previous_score = score
        entry["rank"] = competition_rank
    selected = qualified[:limit]
    return errors, qualified, selected


def serializable_entry(entry: dict[str, Any]) -> dict[str, Any]:
    average = entry["average_position"]
    return {
        "rank": entry["rank"],
        "id": entry["id"],
        "normalized_title": entry["normalized_title"],
        "display_title": entry["display_title"],
        "hit_count": entry["hit_count"],
        "coverage": entry["coverage"],
        "source_ids": entry["source_ids"],
        "source_names": entry["source_names"],
        "average_position": None
        if average is None
        else {
            "numerator": average.numerator,
            "denominator": average.denominator,
            "decimal": float(average),
        },
        "first_hit": {
            "source": SOURCE_IDS[entry["first_hit"][0]],
            "display_order": entry["first_hit"][1],
        },
    }


def ranking_payload(ledger: dict[str, Any], limit: int,
                    qualified: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "report_mode": REPORT_MODE,
            "run_id": ledger.get("run", {}).get("run_id"), "ledger_sha256": canonical_sha256(ledger),
            "limit": limit, "qualified_count": len(qualified),
            "selected": [serializable_entry(entry) for entry in selected]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="run-ledger JSON path")
    parser.add_argument("--limit", type=int, default=10, help="maximum selected topics")
    parser.add_argument("--output", type=Path, help="optional canonical ranking JSON output")
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
    errors, qualified, selected = validate_and_rank(ledger, args.limit)
    if errors:
        print("FAIL ranking validation", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    payload = ranking_payload(ledger, args.limit, qualified, selected)
    if args.output:
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"PASS ranking validation: {len(qualified)} qualified, {len(selected)} selected"
    )
    for entry in payload["selected"]:
        average = entry["average_position"]
        average_text = "∞" if average is None else str(average["decimal"])
        print(
            f"{entry['rank']}. {entry['display_title']} | hits={entry['hit_count']} "
            f"| coverage={entry['coverage']} | average_position={average_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
