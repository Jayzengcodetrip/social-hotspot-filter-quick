"""Synthetic test fixtures only; these records are not browser evidence."""

from __future__ import annotations

from build_capture_ledger import review_template

SOURCE_IDS = (
    "weibo",
    "zhihu",
    "bilibili",
    "baidu",
    "douyin",
    "jiemian",
    "bjnews",
    "caixin",
    "people",
)
FIXED_COUNTS = {
    "weibo": 50,
    "zhihu": 30,
    "bilibili": 30,
    "baidu": 50,
    "douyin": 50,
}
NEWS_COUNTS = {"jiemian": 2, "bjnews": 2, "caixin": 2, "people": 2}


def source_test_url(source_id: str, position: int) -> str:
    urls = {
        "weibo": f"https://s.weibo.com/weibo?q=%23fixture-{position}%23",
        "zhihu": f"https://www.zhihu.com/question/{100000000 + position}",
        "bilibili": f"https://bilibili.example/fixture/{position}",
        "baidu": f"https://www.baidu.com/s?wd=fixture-{position}",
        "douyin": f"https://douyin.example/fixture/{position}",
        "jiemian": f"https://www.jiemian.com/article/{100000 + position}.html",
        "bjnews": f"https://www.bjnews.com.cn/detail/{100000 + position}.html",
        "caixin": f"https://www.caixin.com/{2026}-{position:02d}-01/{100000 + position}.html",
        "people": f"https://paper.people.com.cn/rmrb/pc/content/202608/{position:02d}/content_{100000 + position}.html",
    }
    return urls[source_id]


def fixture_item(source_id: str, position: int) -> dict:
    hotlist = source_id in FIXED_COUNTS
    return {
        "id": f"{source_id}-fixture-{position:03d}",
        "source": source_id,
        "title": f"{source_id}测试标题{position}",
        "url": source_test_url(source_id, position),
        "surface": "hotlist" if hotlist else "current_issue" if source_id == "people" else "homepage",
        "display_order": position,
        "hotlist_position": position if hotlist else None,
        "eligible": True,
        "exclusion_reason": None,
        "publication_date": None,
    }


def source_items(ledger: dict, source_id: str) -> list[dict]:
    return [
        item
        for cluster in ledger["clusters"]
        for item in cluster["items"]
        if item["source"] == source_id
    ]


def refresh_source_collection(ledger: dict, source_id: str) -> None:
    items = source_items(ledger, source_id)
    collection = next(
        row["collection"] for row in ledger["sources"] if row["id"] == source_id
    )
    collection["discovered_count"] = len(items)
    collection["eligible_count"] = sum(item["eligible"] is True for item in items)
    collection["excluded_count"] = sum(item["eligible"] is False for item in items)
    collection["last_item_title"] = max(
        items, key=lambda item: item["display_order"]
    )["title"]


def remove_source_position(ledger: dict, source_id: str, position: int) -> None:
    for cluster in ledger["clusters"]:
        cluster["items"] = [
            item
            for item in cluster["items"]
            if not (
                item["source"] == source_id and item["display_order"] == position
            )
        ]
    ledger["clusters"] = [cluster for cluster in ledger["clusters"] if cluster["items"]]


def make_collection(source_id: str, total: int, last_title: str, run_date: str) -> dict:
    common = {
        "scope": "完整公开热榜" if source_id in FIXED_COUNTS else "当前完整新闻范围",
        "discovered_count": total,
        "eligible_count": total,
        "excluded_count": 0,
        "end_reached": True,
        "end_evidence": "测试夹具已到达定义范围末尾",
        "last_item_title": last_title,
    }
    if source_id in FIXED_COUNTS:
        common.update(
            {
                "completion_method": "fixed_count",
                "expected_item_count": FIXED_COUNTS[source_id],
            }
        )
    elif source_id in {"jiemian", "bjnews", "caixin"}:
        common.update(
            {
                "completion_method": "homepage_footer_stable",
                "footer_reached": True,
                "page_height_stable": True,
                "no_new_item_rounds": 2,
            }
        )
    else:
        common.update(
            {
                "completion_method": "current_issue_all_pages",
                "issue_date": run_date,
                "issue_total_pages": 2,
                "issue_read_pages": 2,
            }
        )
    return common


def make_ledger(complete_sources: bool = True) -> dict:
    run_date = "2026-08-06"
    primary_cluster = {
        "id": "seven-entities",
        "normalized_title": "针对7家美国实体采取反制措施",
        "display_title": "针对7家美国实体采取反制措施",
        "items": [
            {
                "id": "weibo-001",
                "source": "weibo",
                "title": "中方对7家美国实体采取反制措施",
                "url": source_test_url("weibo", 1),
                "surface": "hotlist",
                "display_order": 1,
                "hotlist_position": 1,
                "eligible": True,
                "exclusion_reason": None,
                "publication_date": None,
            },
            {
                "id": "zhihu-001",
                "source": "zhihu",
                "title": "如何看待针对7家美国实体的反制措施",
                "url": source_test_url("zhihu", 2),
                "surface": "hotlist",
                "display_order": 2,
                "hotlist_position": 2,
                "eligible": True,
                "exclusion_reason": None,
                "publication_date": None,
            },
        ],
    }
    clusters = [primary_cluster]
    if complete_sources:
        occupied = {"weibo": {1}, "zhihu": {2}}
        for source_id in SOURCE_IDS:
            total = (
                FIXED_COUNTS[source_id]
                if source_id in FIXED_COUNTS
                else NEWS_COUNTS[source_id]
            )
            filler_items = [
                fixture_item(source_id, position)
                for position in range(1, total + 1)
                if position not in occupied.get(source_id, set())
            ]
            if filler_items:
                clusters.append(
                    {
                        "id": f"{source_id}-fixture-only",
                        "normalized_title": f"{source_id}单来源测试条目",
                        "items": filler_items,
                    }
                )
    sources = []
    for source_id in SOURCE_IDS:
        row = {"id": source_id, "status": "read", "reason": ""}
        if complete_sources:
            items = [
                item
                for cluster in clusters
                for item in cluster["items"]
                if item["source"] == source_id
            ]
            row["collection"] = make_collection(
                source_id,
                len(items),
                max(items, key=lambda item: item["display_order"])["title"],
                run_date,
            )
        sources.append(row)
    ledger = {
        "schema_version": 2,
        "report_mode": "quick",
        "run": {
            "run_id": "synthetic-current-run",
            "date": run_date,
            "collection_time": "2026-08-06 09:00-09:10 CST",
            "browser_method": "用户连接的 Chrome",
            "freshness_note": "无",
        },
        "sources": sources,
        "clusters": clusters,
        "corrections": [],
    }
    for cluster in ledger["clusters"]:
        for item in cluster["items"]:
            item["raw_url"] = item["url"]
            item["raw_eligible"] = item["eligible"]
            item["raw_exclusion_reason"] = item["exclusion_reason"]
    if complete_sources:
        refresh_runtime_evidence(ledger)
    refresh_review(ledger)
    return ledger


def refresh_review(ledger: dict) -> None:
    """Synthetic-fixture decision attestation; never use on live news work."""
    review = review_template(ledger)
    review.update(status="complete", reviewed_at=f"{ledger['run']['date']}T12:10:00+08:00")
    ledger["clustering_review"] = review


def refresh_runtime_evidence(ledger: dict, *, reset_raw: bool = False) -> None:
    """Generate/replay synthetic observations, not a mocked validation result."""
    for source in ledger["sources"]:
        source_id = source["id"]
        items = sorted(source_items(ledger, source_id), key=lambda item: item["display_order"])
        if reset_raw:
            for item in items:
                item["raw_url"] = item["url"]
                item["raw_eligible"] = item["eligible"]
                item["raw_exclusion_reason"] = item["exclusion_reason"]
        rows = [{"title": item["title"], "url": item["raw_url"]} for item in items]
        evidence, summary = _fixture_evidence(source_id, rows, ledger["run"]["run_id"], ledger["run"]["date"])
        source["collection"].update(summary, runtime_evidence=evidence)


def refresh_capture_evidence(captures: dict, run: dict, *, update_summary: bool = False) -> None:
    """Fixture-only recapture after deliberately changing source rows."""
    for source_id, packet in captures.items():
        evidence, summary = _fixture_evidence(source_id, packet["rows"], run["run_id"], run["date"])
        packet["collection"]["runtime_evidence"] = evidence
        if update_summary:
            packet["collection"].update(summary)


def _fixture_evidence(source: str, rows: list, run_id: str, date: str) -> tuple[dict, dict]:
    from _runtime_test_fixtures import fixture_trace, fixture_observation
    from source_evidence import make_runtime_evidence
    trace = fixture_trace(source, rows, run_id, date)
    if source == "people":
        first = trace["issue"]["pageUrls"][0]
        urls = [first, first.replace("node_01.html", "node_02.html")]
        trace["issue"].update(totalPages=2, pageUrls=urls)
        trace["observations"] = []
        split = max(1, len(rows) // 2)
        for page, indices in enumerate((range(split), range(split, len(rows))), 1):
            observation = fixture_observation(page * 1000, urls[page - 1], indices)
            observation.update(pageNumber=page, pageEnd={"reached": True, "detail": "Synthetic full page end"})
            trace["observations"].append(observation)
    return make_runtime_evidence(trace, source, run_id, rows, f"{date}T12:00:00+08:00", run_date=date)



def regroup_positions(ledger: dict, cluster_id: str, positions: list[tuple[str, int]]) -> dict:
    """Move existing synthetic items into a cluster without changing the source surface."""
    wanted = set(positions)
    picked = [
        item for cluster in ledger["clusters"] for item in cluster["items"]
        if (item["source"], item["display_order"]) in wanted
    ]
    if len(picked) != len(wanted):
        raise ValueError("fixture positions must exist exactly once")
    for source_id, position in positions:
        remove_source_position(ledger, source_id, position)
    cluster = {"id": cluster_id, "normalized_title": cluster_id, "items": picked}
    ledger["clusters"].append(cluster)
    return cluster
