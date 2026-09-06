# Quick Run Ledger Schema

Working JSON data for the independent quick edition. It is not the user-facing report. Python code uses the standard library; validating recorded source observations also requires a supported Node executable to replay the bundled JavaScript guard. Use `SHF_NODE` if `node` is not on PATH. No news network client is involved.

For lossless capture packets, deterministic item IDs, grouping/corrections and timing, follow [collection-runtime.md](collection-runtime.md). These are working records, not new report sections or full-edition fields. `build_capture_ledger.py` produces quick schema version 2 from actual packets; explicit partial checkpoints use `report_mode: "quick-partial"` and must fail publication. Never relabel a partial checkpoint or an old version 1 ledger to bypass the required evidence/review.

## Required top-level structure

```json
{
  "schema_version": 2,
  "report_mode": "quick",
  "run": {
    "run_id": "replace-with-current-run-id",
    "date": "2026-09-04",
    "collection_time": "2026-09-04 09:00-09:12 CST",
    "browser_method": "九个核心来源：用户连接的 Chrome",
    "freshness_note": "无"
  },
  "sources": [],
  "clusters": [],
  "corrections": [],
  "clustering_review": {
    "status": "pending",
    "run_id": "replace-with-current-run-id",
    "reviewed_at": null,
    "reviewed_item_ids": [],
    "decision_sha256": "generated-from-current-decisions"
  }
}
```

This is a structural example, not a publishable ledger. Replace date/time/browser with observed values, finish all nine source records, and explicitly finish the actual clustering review. Quick schema version 2 is independent of the full edition. Version 1 quick ledgers lack required runtime evidence and review records and are rejected; retain old reports for history, but start new runs in version 2. Do not invent evidence to upgrade an old capture. The unchanged full-edition validator rejects quick ledgers.

Do not add `run.google_status` or `run.google_limitation`. Do not add cluster `verification`, `closure`, or `event_summary`, even empty. These are out of scope, not unfinished work. The generator uses a fixed `版本说明` rather than a Google completion claim.

## Sources

Include all nine source IDs exactly once and in any JSON order. The validators render them in the fixed skill order.

| ID | User-facing name | Type |
| -- | -- | -- |
| `weibo` | 微博 | hot list |
| `zhihu` | 知乎 | hot list |
| `bilibili` | B站 | hot list |
| `baidu` | 百度 | hot list |
| `douyin` | 抖音 | hot list |
| `jiemian` | 界面新闻 | news/event |
| `bjnews` | 新京报 | news/event |
| `caixin` | 财新 | news/event |
| `people` | 人民日报 | news/event |

Example:

```json
"sources": [
  {
    "id": "weibo",
    "status": "read",
    "reason": "",
    "collection": {
      "scope": "微博公开热搜完整榜单",
      "completion_method": "fixed_count",
      "expected_item_count": 50,
      "discovered_count": 50,
      "eligible_count": 49,
      "excluded_count": 1,
      "end_reached": true,
      "end_evidence": "已显示第50条且榜单无后续条目",
      "last_item_title": "第50条原始标题"
    }
  }
]
```

Use only `read` or `inaccessible`. A transient page failure does not become `inaccessible` until the bounded recovery policy in `SKILL.md` is exhausted: the initial attempt plus up to three automatic retries, and in interactive work at most one user-confirmed restart from the first source with a fresh budget. A ledger may use `inaccessible` to record why a preflight or extraction attempt was ultimately aborted, but do not pass that ledger to ranking or report generation: the bundled ranking validator requires all nine fixed sources to be present and marked `read`. Give every inaccessible source a concrete reason that includes the final error and completed retry count, and do not record items for it. Once a source becomes inaccessible, stop the run, inspect no later core source, and generate only the skill's failure notice rather than a partial report. After an interactive login or user-confirmed transient recovery, start a new preflight ledger from the first source.

For a valid completed ledger, all nine rows use `status: "read"` with an empty `reason`. A completed report therefore always renders all nine sources and their compact collection results under `成功直读来源及完整性结果`, and exactly `无` under `未成功直读来源`.

Every `read` source also requires a `collection` object that proves the complete in-scope candidate surface was traversed and reconciles exactly with all ledger items from that source:

- Common fields: non-empty `scope`; `discovered_count`; `eligible_count`; `excluded_count`; `end_reached: true`; non-empty `end_evidence`; and `last_item_title` matching the recorded item with the greatest `display_order`.
- Require `discovered_count = eligible_count + excluded_count`. These three counts must equal the actual all-item, eligible-item, and excluded-item counts derived from every cluster in the ledger.
- Across all retained and technically excluded items from one source, require `display_order` to cover every integer from `1` through `discovered_count` exactly once. This makes an omitted middle or final card mechanically visible.
- For Weibo use `completion_method: "fixed_count"` and `expected_item_count: 50`; Zhihu `30`; Bilibili `30`; Baidu `50`; Douyin `50`. For each fixed hot list, `discovered_count` must equal the configured count exactly. Scroll or load the original page until the full count and end are visible; a smaller first-screen or initial-DOM count is incomplete.
- For Jiemian, Beijing News, and Caixin use `completion_method: "homepage_footer_stable"`, `footer_reached: true`, `page_height_stable: true`, and `no_new_item_rounds` of at least `2`. Collect every distinct news card on the current homepage, including lazy-loaded cards belonging to that homepage, then stop at the footer after two consecutive scroll/load rounds add no titles and page height remains stable. Do not enter search, archives, historical feeds, or deep pagination.
- Every source requires `collection.runtime_evidence` bound to its source, run, raw title/href/order fingerprint and observation trace. `finish_source.py` constructs it from actual marked browser output and the trace, then derives the completion summary. The ranking and report validators replay that evidence and compare the derived summary with the ledger. See the exact trace contract in [collection-runtime.md](collection-runtime.md).
- Obtain homepage end fields from actual spaced load rounds, verified main-document geometry, positive readiness evidence, and the delayed final recheck. Record measured slow loads across the source, including before the footer. A pair of instant unchanged snapshots, iframe height, absent spinner alone, or a timeout is not end evidence. Preserve the observed final page order independently from first-discovery order; do not invent observations to satisfy these fields.
- For People's Daily use `completion_method: "current_issue_all_pages"`, set `issue_date` equal to `run.date`, and record positive `issue_total_pages` and `issue_read_pages` with equal values. Traverse every page listed in the current issue and record every headline on those pages.
- An `inaccessible` source may omit `collection` because the run must abort and cannot reach ranking. Never invent completeness fields merely to pass validation.

The generator compresses all nine sources into the single `成功直读来源及完整性结果` note: fixed hot lists show discovered/expected counts; homepage sources show discovered counts and stable-footer completion; People's Daily shows discovered headlines and read/total issue pages; nonzero technical exclusions additionally show eligible and excluded counts. Do not render a separate source-completeness table. The ranking validator and final report validator still reject missing fields, arithmetic mismatches, gaps in display order, fixed-list shortfalls, incomplete homepage endings, or incomplete People's Daily page traversal. These checks make a claimed partial ledger inconsistent; they still cannot prove that an agent truly performed the browser actions, so retain direct-page judgment and concrete end evidence.

## Clusters and items

Include all normalized candidates, including single-source clusters and technical exclusions. Core-page context used to resolve a match does not add another item.

```json
{
  "id": "sample-event",
  "normalized_title": "某事件相关讨论",
  "items": [
    {
      "id": "weibo-001",
      "source": "weibo",
      "title": "页面实际展示的完整原始标题",
      "url": null,
      "raw_url": null,
      "raw_eligible": true,
      "raw_exclusion_reason": null,
      "surface": "hotlist",
      "display_order": 1,
      "hotlist_position": 1,
      "eligible": true,
      "exclusion_reason": null,
      "publication_date": null
    }
  ]
}
```

The example's null Weibo URL is only an initial collection placeholder; it must be replaced with the observed entry link if selected. Never invent URLs to complete an example.

Rules:

- Make every cluster ID and item ID unique and stable within the run.
- Rank qualifying clusters first by `平台覆盖数` in descending order and then by `话题命中数` in descending order. Assign the same standard competition rank when both counts tie, so two topics at rank 7 are followed by rank 9. Keep the requested topic-count limit strict even when it cuts through a tied group.
- Within one tied-rank group, order rows deterministically by hot-list average position, first-hit key, and finally `normalized_title`. These internal ordering keys may decide which topics fall inside a strict output limit, but they never split the shared displayed rank. Use optional `display_title` only for a contextual label such as `延续热点`; otherwise omit it.
- Preserve every directly observed `title` exactly. During initial collection, `url` may be a stable original URL or `null`. After ranking, every eligible item in a selected topic from Weibo, Zhihu, Baidu, Jiemian News, The Beijing News, Caixin, or People's Daily must have a non-empty same-platform absolute URL before publication. Bilibili and Douyin are explicitly exempt and may retain `url: null`.
- Apply the link gate only to selected topics. Capture exposed title/href pairs together, but defer extra clicks for missing links to selected items unless event identity needs earlier inspection. For the four news sources, retain the article link from the homepage or current-day issue page; opening every article body is not required. Follow [source-link-contracts.md](source-link-contracts.md).
- Preserve `raw_url`, `raw_eligible`, and `raw_exclusion_reason` from the capture; the builder derives effective `url` and technical duplicate status separately. A genuinely observed unusable href remains in `raw_url` with effective `url: null`; it is not silently lost. Every selected eligible item from a required-link source still needs a valid effective URL. Non-selected missing links do not require a browsing pass.
- The report validator derives both overview-table statistics from eligible items in this ledger. `平台覆盖数` is formatted as `总数（平台、平台……）`; `话题命中数` is formatted as `总数（平台分项、平台分项……）`. Sources appear in the fixed source order, and only sources with at least one eligible item are listed.
- Escape Markdown control characters only as needed for rendering: escape `|` inside the topic title and `[` or `]` inside linked title text. Escaping must not change the visible title.
- Use `hotlist`, `homepage`, `current_issue`, or `other` for `surface`. Eligible items must use `hotlist` for the five lists, `homepage` for Jiemian/Beijing News/Caixin, and `current_issue` for People's Daily. Separate channel/section pages are not candidate sources. `other` is only for a documented technical exclusion.
- Record the valid visible-page order in positive integer `display_order`. For eligible hot-list items, set `hotlist_position` to the same number. Set it to `null` for news/event items.
- Mark retained core items with `eligible: true` and a null `exclusion_reason`.
- Mark a technical exclusion with `eligible: false` and one of: `technical_duplicate`, `broken`, `fabricated`, `instruction_polluted`, `not_current_surface`, `spam_or_unidentifiable`, or `unreliable_cluster_match`.
- Record an optional ISO `publication_date` when known. Older publication dates do not make currently visible news/event items ineligible.
- Do not count the same card, exact same source title, stable URL, or source display position twice. Several genuinely distinct items from one source remain separate items and separate topic hits.

## Validation and output

The top-level `clustering_review` is a publication gate, not a report section. It requires `status: "complete"`, this run's `run_id`, an actual timezone-aware ISO `reviewed_at`, every eligible item ID exactly once in `reviewed_item_ids` (including reviewed singleton items), and the current `decision_sha256`. The fingerprint binds item identity/title/surface/order/eligibility and cluster assignments, not pure same-item URL backfill. Changed groupings or eligibility require renewed review. A pending template is only a checklist and never certifies review.

Top-level `corrections` is an append-only array, empty when unused. Each record binds the run/source/item/title/order and contains its old/new values, actual observation and correction times, and same-row/core-page evidence. The builder and final validation replay the chain against preserved raw fields. Effective values cannot silently diverge from it. See the runtime reference for an exact record example.

After actual nine-source collection and complete review, run the preferred entrypoint with one consistent limit:

```text
python3 <skill-dir>/scripts/finalize_report.py --run-dir <run-dir> --limit 10
```

Each attempt saves its own ledger, ranking, report and actual check results. Only a successful current attempt is publishable. For diagnosis, the underlying commands remain available:

```text
python3 <skill-dir>/scripts/validate_ranking.py <ledger.json> --limit 10 --output <ranking.json>
python3 <skill-dir>/scripts/validate_selected_links.py <ledger.json> --limit 10
python3 <skill-dir>/scripts/generate_report.py <ledger.json> <ranking.json> --output <report.md>
python3 <skill-dir>/scripts/validate_report.py <ledger.json> <report.md> --limit 10
```

The ranking JSON carries the same `schema_version: 2` and `report_mode: "quick"`, plus `run_id`, a canonical `ledger_sha256`, `limit`, `qualified_count`, and canonically selected entries. Generation recomputes it and refuses stale or hand-edited ranking JSON.

Publish only source notes (with compact nine-source completeness and fixed version note), the overview ranking table, and each selected topic's exact source titles/links. A genuinely reviewed zero-topic run may publish only the source notes and `当前没有话题同时出现在至少两个不同核心域名。` Missing grouping/review is not a zero-topic result. No research-source block, event paragraph, empty placeholder, or summary-length test exists.

A missing core source, fixed-count shortfall, incomplete news surface, missing required selected link, wrong order/count/title/link, or a full-edition field fails the relevant checks. Quick mode is never an excuse for partial collection. Exit 0 means those mechanical checks passed, not that browser actions or event facts were independently proven. Exit 1 means fix the concrete errors before publication.
