#!/usr/bin/env python3
"""Source-specific link rules for selected social-hotspot core items."""

from __future__ import annotations

from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit


REQUIRED_LINK_SOURCE_IDS = (
    "weibo",
    "zhihu",
    "baidu",
    "jiemian",
    "bjnews",
    "caixin",
    "people",
)
OPTIONAL_LINK_SOURCE_IDS = ("bilibili", "douyin")

SOURCE_NAMES = {
    "weibo": "微博",
    "zhihu": "知乎",
    "bilibili": "B站",
    "baidu": "百度",
    "douyin": "抖音",
    "jiemian": "界面新闻",
    "bjnews": "新京报",
    "caixin": "财新",
    "people": "人民日报",
}

SOURCE_BASE_URLS = {
    "weibo": "https://s.weibo.com/top/summary",
    "zhihu": "https://www.zhihu.com/hot",
    "baidu": "https://top.baidu.com/board?tab=realtime",
    "jiemian": "https://www.jiemian.com/",
    "bjnews": "https://www.bjnews.com.cn/",
    "caixin": "https://www.caixin.com/",
    "people": "https://paper.people.com.cn/",
}

SOURCE_HOST_SUFFIXES = {
    "weibo": "s.weibo.com",
    "zhihu": "zhihu.com",
    "baidu": "baidu.com",
    "jiemian": "jiemian.com",
    "bjnews": "bjnews.com.cn",
    "caixin": "caixin.com",
    "people": "people.com.cn",
}


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _is_listing_page(source_id: str, host: str, path: str) -> bool:
    normalized_path = "/" + path.strip("/") if path.strip("/") else "/"
    if source_id == "weibo":
        return host == "s.weibo.com" and normalized_path == "/top/summary"
    if source_id == "zhihu":
        return normalized_path in {"/", "/hot"}
    if source_id == "baidu":
        return host == "top.baidu.com" and normalized_path == "/board"
    return normalized_path == "/"


def validate_required_source_url(source_id: str, url: Any) -> str | None:
    """Return a concrete error when a required selected-item URL is invalid."""
    if source_id not in REQUIRED_LINK_SOURCE_IDS:
        return None
    source_name = SOURCE_NAMES[source_id]
    if not isinstance(url, str) or not url.strip():
        return f"{source_name} selected item requires a non-empty original-entry URL"
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return f"{source_name} selected-item URL is malformed"
    if parts.scheme.lower() not in {"http", "https"} or not host:
        return f"{source_name} selected-item URL must be an absolute http:// or https:// URL"
    expected_suffix = SOURCE_HOST_SUFFIXES[source_id]
    if not _host_matches(host, expected_suffix):
        return (
            f"{source_name} selected-item URL host {host!r} does not belong to "
            f"{expected_suffix!r}"
        )
    if _is_listing_page(source_id, host, parts.path):
        return f"{source_name} selected-item URL points to the source entry page, not the item"
    return None


def normalize_core_link(source_id: str, raw_href: str) -> tuple[str | None, str | None]:
    """Resolve a raw or relative href and enforce the selected-source link contract."""
    if source_id not in REQUIRED_LINK_SOURCE_IDS:
        return None, f"source {source_id!r} does not require a report link"
    if not isinstance(raw_href, str) or not raw_href.strip():
        return None, "href must be a non-empty string"
    resolved = urljoin(SOURCE_BASE_URLS[source_id], unescape(raw_href.strip()))
    try:
        parts = urlsplit(resolved)
    except ValueError:
        return None, "href could not be resolved as a URL"
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, "")
    )
    error = validate_required_source_url(source_id, normalized)
    if error:
        return None, error
    return normalized, None


def validate_selected_core_links(selected: list[dict[str, Any]]) -> list[str]:
    """Require valid links for selected items from seven fixed sources."""
    errors: list[str] = []
    for topic_index, entry in enumerate(selected, start=1):
        cluster = entry.get("cluster", {})
        title = str(entry.get("display_title", entry.get("id", "unknown topic")))
        items = cluster.get("items", []) if isinstance(cluster, dict) else []
        for item_index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("eligible") is not True:
                continue
            source_id = item.get("source")
            if source_id not in REQUIRED_LINK_SOURCE_IDS:
                continue
            error = validate_required_source_url(str(source_id), item.get("url"))
            if error:
                item_id = item.get("id", f"item-{item_index + 1}")
                errors.append(
                    f"selected topic {topic_index} {title!r}, item {item_id!r}: {error}"
                )
    return errors
