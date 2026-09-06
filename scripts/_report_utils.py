#!/usr/bin/env python3
"""Rendering helpers for the quick edition; no research or summary dependencies."""

from __future__ import annotations

from typing import Any

from validate_ranking import HOTLIST_EXPECTED_COUNTS, SOURCE_IDS, SOURCE_NAMES

TABLE_HEADER = "| 排名 | 共同热点 | 平台覆盖数 | 话题命中数 |"
TABLE_SEPARATOR = "| -- | ---- | ---- | ---- |"
NO_TOPIC_SENTENCE = "当前没有话题同时出现在至少两个不同核心域名。"
VERSION_NOTE = (
    "快速版仅整理九个核心来源的共同热点及原始标题链接，"
    "不开展额外补充检索与事实核实，不提供事件概述。"
)
SOURCE_NOTE_FIELDS = (
    "采集时间", "浏览方式", "成功直读来源及完整性结果",
    "未成功直读来源", "版本说明", "时效说明",
)


def report_title(limit: int) -> str:
    return f"# 当日跨来源共同热点 Top {limit}（快速版）"


def escape_table(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def escape_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def compact_source_collection_entry(
    source_id: str, collection: dict[str, Any]
) -> str:
    name = SOURCE_NAMES[source_id]
    discovered = collection["discovered_count"]
    eligible = collection["eligible_count"]
    excluded = collection["excluded_count"]
    exclusion = f"；有效{eligible}、排除{excluded}" if excluded else ""
    method = collection.get("completion_method")
    if method == "fixed_count":
        expected = HOTLIST_EXPECTED_COUNTS[source_id]
        if excluded:
            return f"{name}{discovered}/{expected}（有效{eligible}、排除{excluded}）"
        return f"{name}{discovered}/{expected}"
    if method == "homepage_footer_stable":
        return f"{name}{discovered}条（页脚稳定{exclusion}）"
    if method == "current_issue_all_pages":
        read_pages = collection.get("issue_read_pages")
        total_pages = collection.get("issue_total_pages")
        return f"{name}{discovered}条（当日版面{read_pages}/{total_pages}{exclusion}）"
    return f"{name}{discovered}条（未通过）"


def compact_source_completeness_note(ledger: dict[str, Any]) -> str:
    source_rows = {row["id"]: row for row in ledger["sources"]}
    entries = {
        source_id: compact_source_collection_entry(
            source_id, source_rows[source_id]["collection"]
        )
        for source_id in SOURCE_IDS
    }
    hotlists = "、".join(entries[source_id] for source_id in SOURCE_IDS[:5])
    homepages = "、".join(entries[source_id] for source_id in SOURCE_IDS[5:8])
    return f"{hotlists}；{homepages}；{entries['people']}；九个来源均通过"


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    text = stripped[1:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in text:
        if escaped:
            current.append("\\" + character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells



def expected_source_notes(ledger: dict[str, Any]) -> dict[str, str]:
    run = ledger["run"]
    return {
        "采集时间": str(run["collection_time"]),
        "浏览方式": str(run["browser_method"]),
        "成功直读来源及完整性结果": compact_source_completeness_note(ledger),
        "未成功直读来源": "无",
        "版本说明": VERSION_NOTE,
        "时效说明": str(run["freshness_note"]),
    }
