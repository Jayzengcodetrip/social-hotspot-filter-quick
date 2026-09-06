# Collection runtime: transfer once, inspect exceptions

Read before collecting. These helpers optimize execution, not source selection or evidence standards. Python helpers use the standard library; source-finish and publication checks replay the bundled JavaScript guards through Node. Check these runtimes once; if Node is not on PATH, set `SHF_NODE` to a supported executable (or use source-finish's `--node` option). They contain no network client, login bypass, or replacement news API. Use each helper's `--help` for exact command options.

## 1. Initialize once, before the nine-source preflight

Choose a new run directory outside any installed Skill. Keep captures, observed collection evidence, grouping decisions, timing, ledger, ranking, report and actual validation output there. Never use an earlier run's rows for a fresh report.

Write small `run.json` with the actual `run_id`, `date` (Asia/Shanghai), `collection_time`, `browser_method`, and `freshness_note`. Use the same ID for initialization and every input. Update only derived run notes as collection progresses; never change the run identity or relabel old captures.

```text
python3 <skill>/scripts/capture_io.py init <run-dir> --run-id <unique-id>
python3 <skill>/scripts/run_timing.py init <run-dir>/timing.jsonl --run-id <unique-id>
```

Use the browser tool's documented session and data export/file-writing facilities if available. Do not assume a generic `content.export` exists. If unavailable in Codex, initialization can additionally take `--session-log <exact-current-thread.jsonl>`: it binds that existing file's identity, current byte offset and initialization time. It reads only subsequently appended tool-result text bearing this run's source-specific capture token. Obtain the exact current thread path from host context; do not search through unrelated conversation contents. A changed/truncated file fails closed. This is transport of **the current direct browser output**, not reuse of archived news.

If the environment supplies neither supported export nor an accessible current-output log, disclose the transport limitation. Do not claim automatic transfer, probe unrelated private data, or adopt unsupported browser APIs. Other conforming browsers may supply the same marked UTF-8 output file.

Initialize timing with `--request-at` only when the host exposes the exact timezone-aware request timestamp. Otherwise the timer accurately starts at instrumentation initialization, not the earlier user request. These local setup commands can be batched; do not add a model round trip for every field.

## 2. Emit captured rows, not model-transcribed rows

After direct reading, the browser/host runtime must serialize its actual in-memory row array. Each row is `{title, url}` (optional explicit `eligible`, `exclusion_reason`, `publication_date`); `title` is the displayed original string, `url` is the observed href/resolved link or explicit null. Never reconstruct a URL from title/query text. Browser extraction must pair the title and href on the same row/card.

Emit one complete JSON envelope per text line, prefixed with `SHF_CAPTURE_V1 `. The manifest supplies `run_id` and the token for this source:

```javascript
// Use the selected tool's documented output primitive for this STRING.
// rows must already be the live browser result, not a newly typed array.
// Set capturedAt once when that array is captured; preserve it on re-emission.
"SHF_CAPTURE_V1 " + JSON.stringify({
  capture_schema: 1, run_id: runId, source: sourceId, token: sourceToken,
  part: 1, parts: 1, captured_at: capturedAt, rows
})
```

For output limits, emit numbered chunks from the **same captured array**, using one token, consistent total parts and original array order. Missing chunks fail import; identical repeated chunks are idempotent and conflicting ones fail. Do not truncate and fill gaps from memory. Emit all chunks in one permitted tool operation where practical. Browser APIs, output primitives and local-module access remain environment-specific; use only documented capabilities.

Preserve the compact observation trace described below beside the actual marked output. Close a completed source using one command; do not separately hand-fill completion booleans or move guard-state fields into a collection file:

```text
python3 <skill>/scripts/finish_source.py <run-dir> bjnews --trace <actual-trace.json> --export-file <tool-created-output.txt>
python3 <skill>/scripts/finish_source.py <run-dir> bjnews --trace <actual-trace.json>
```

The second command requires the current log bound during init. Source-finish checks source/run/token, capture times, complete chunks, observed page order, and the trace; it derives completeness fields and saves immutable `evidence/<source>.json` and `captures/<source>.json`. Its short result identifies the source, count, end title and packet path. Packets contain `{source, run_id, rows, collection, capture}`; `collection.runtime_evidence` carries the trace and raw-pair fingerprint for subsequent independent replay. Identical repeated calls are idempotent and may complete an interrupted half-write; differing existing content is preserved and rejected. Keep auxiliary JSON outside `captures/`. The old low-level importer remains available for transport diagnosis, but a raw packet without valid runtime evidence cannot pass publication. Never overwrite a raw packet to hide a correction.

### Observation trace contract

Serialize observations from the same current browser run through a supported output path. A trace has `trace_schema: 1`, `run_id`, `source`, this source's manifest `token`, `captured_at` equal to the final raw-array envelope timestamp, `kind`, and `observations`. The trace schema and capture-envelope schema remain 1; the resulting quick ledger schema is 2.

Use a single source-relative monotonic clock for millisecond fields: subtract the observed source-collection start from actual controller times. Preserve the actual clock origin locally. Do not use epoch milliseconds or browser-session uptime as a source-relative offset. Observation timestamps strictly increase, and the final offset must fit inside the actual run interval from manifest initialization to capture. Wall-clock fields are timezone-aware ISO strings.

Each trace observation uses zero-based `itemIndices` into the immutable raw row array instead of retyping titles and links. The final raw array must be in verified page order, not the order items first appeared during loading. Preserve original source order for stable `source-001` IDs assigned by the builder. Do not drop earlier observed cards or invent missing ones to match the final snapshot.

Common observation fields:

- `timestampMs`, observed `context: {targetUrl, topLevelUrl, documentUrl, isMainDocument}`, `itemIndices`, `loading`, and `unresolvedCount`. A directly observed same-source redirect can be recorded in `context.acceptedMainUrls`. A homepage redirect to an index/default/home document also needs non-empty `context.homepageIdentityEvidence`; this is not permission to use a channel/section page.
- Positive `readinessEvidence: {observedAtMs, pendingState: "none", basis, detail}`; `basis` is `explicit_candidate_load_complete` or `observed_candidate_surface_settled`. The detail describes the actual visible loading/completion evidence, not elapsed time alone.
- `orderEvidence: {observedAtMs, basis, detail, itemIndices}` when proving final order. Use `full_dom_order` only for an actual complete ordered snapshot. Use `segmented_virtual_order` only with actual ordered segments and `segments: [{startOrder, count, observedAtMs, detail}]`; one-based segment positions must cover the entire ordered array with no gaps. Never infer page order from discovery order.

For a full-DOM final check, `orderEvidence.itemIndices` must match that actual final observation's `itemIndices` in order. For homepage/hot-list completion this is the complete raw-array sequence `0..N-1`; for a newspaper page it is that page's segment. A virtual list needs actual segmented order evidence, not a fictional full snapshot.

Source-specific records:

- Five fixed lists use `kind: "fixed_hotlist"`. The final observation includes complete order evidence and `listEnd: {reached: true, detail}`. Preserve the actual full list, configured count, visible end, and Bilibili's numbered-only / Douyin's update-time observations under their existing rules.
- Three news homepages use `kind: "homepage"` and the real `sample`, `scroll_round`, and `final_recheck` observations accepted by the guard below. Preserve geometry, readiness, measured slow loads, round identity and timing. The final delayed check supplies complete page-order evidence. Their target is the fixed homepage, never a separately opened channel/section.
- People's Daily uses `kind: "current_issue"` with `issue: {date, totalPages, pageUrls, indexEvidence, indexContext}`. Record the actual current-day issue index and all page URLs in issue order. Keep one completed observation per page in the final trace, including `pageNumber` (one-based), page-specific context, that page's `itemIndices`, `pageEnd: {reached: true, detail}`, readiness and order evidence. A genuinely empty page requires `emptyPageEvidence`; do not treat an unread page as empty. Retain any intermediate page observations separately.

Runtime evidence checks binding, completeness, order and timing consistency. It cannot establish that a model supplied truthful browser observations. Never manufacture a trace, completion status, headline or timestamp to pass.

## 3. Homepage guards and site-specific handling

Use `browser_collection.mjs` in a documented local-module-capable host; otherwise use its adjacent JSON CLI through a permitted shell runtime. Never paste helper code into webpage content or invoke an undocumented browser API. The module's exports are:

- `checkDocumentContext(context)`: compare target, observed top-level and actual evaluated-document URLs plus positively established main-document scope. A redirect is acceptable only if directly observed at the original site.
- `createHomepageState(config)` and `observeHomepage(state, observation)`: retain the exact title/link set in `seenItems`, separately require observed final order in `orderedItems`, and evaluate real scroll rounds and delayed final recheck. Preserve each observation as evidence; constants are lower bounds, not a license to stop before slower data arrives. The direct module's observations use actual `items` arrays; the stored trace uses indices into the final raw array.
- `selectBeijingHeadlines(candidates)`: choose verified primary/related titles without letting a summary overwrite a title for the same link. It returns unresolved candidates separately. Unknowns must be inspected; do not drop them merely to get completion.

```text
node <skill>/scripts/browser_collection_cli.mjs --help
node <skill>/scripts/browser_collection_cli.mjs context <context.json> --output <run-dir>/scope.json
node <skill>/scripts/browser_collection_cli.mjs headlines <candidates.json> --output <run-dir>/headline-result.json
node <skill>/scripts/browser_collection_cli.mjs init --output <run-dir>/state-0.json
node <skill>/scripts/browser_collection_cli.mjs observe <observation.json> --state <run-dir>/state-0.json --output <run-dir>/state-1.json
```

Use a fresh numbered state output per observation, keeping prior evidence. The pure module is imported, not invoked as a CLI. Headline selection returns the original DOM order as audit metadata; the ledger builder assigns contiguous item display order from the resulting ordered row array.

The module comments and CLI help define the JSON shapes. Supply one actual clock, main-page geometry, candidate changes, footer state and positive content-readiness evidence. Record real loading delay when observable; final recheck waits at least the default 12 seconds and lengthens for observed slower loads. The helper **does not itself scroll or sleep**. The browser driver must perform the action, wait for the candidate surface to settle, then provide a new observation. Missing or pending readiness means incomplete. No finite quiet timer proves that arbitrary asynchronous work has completed; absent spinners and tool-call duration alone are insufficient evidence.

Readiness evidence concerns the observed candidate surface, not invisible global network traffic: describe the actual completed card/module render or loading-to-complete transition and current state. Use `pendingState: "none"` only when supported by those observations; do not claim all HTTP requests finished. Clock/mutation fields may come from the controller's actual successive observations; do not invent a timestamp or inject an observer through a read-only-only tool.

For each homepage: read and accumulate all current candidate cards; scroll/load; wait for valid readiness; compare new title/link set and main-page height; require two effective no-new rounds at the footer; then re-read after the delayed final check. Any growth or uncertain module resets completion. Never add archives, historical load-more feeds or deep pagination.

### Beijing News layout recipe

The observed homepage has primary title anchors under `.pin_demo`, associated titles and excerpts under `.pin_tips`, heading/list cards, image anchors, and decorative `.num` children in some headings. Inspect the current original DOM before applying these hints: selectors are observed-layout guidance, not a guarantee about future layouts.

Capture title/link/card association together. Prefer the verified primary title over an excerpt for the same URL; keep a separately linked related headline even if styled `.pin_tips`. Never exclude all `.pin_tips`, select the last anchor arbitrarily, or substitute image alt/body text for an unverified visible title. Strip only separately observed decorative elements from the title extraction, never edit meaningful title text. Unknown patterns remain unresolved. Do not use a past final count (such as 213) as a future target or stop at the first visible footer.

### Caixin scope recipe

Before interpreting empty extraction, compare the actual evaluated document URL with the observed news homepage. `cxdata.caixin.com/index/dataChartGroup` is a chart document, not homepage news. A valid main-page accessibility/DOM snapshot together with an empty chart read is a scope mismatch, not a network failure or reason to reload repeatedly.

Use an explicitly supported main-document selection path if the tool has one. Otherwise use its documented complete-page snapshot/accessibility route and preserve title/link pairs from the actual original page. Rebinding the same object without changed scope is not a new recovery strategy. Establish homepage geometry through a supported main-page observation; never use the chart's height. If the tool cannot provide enough evidence, follow existing source-recovery/stop rules rather than fabricate completion.

Include every in-scope news module and carousel title; do not discard all frames, because some embedded modules contain real news. Treat module coverage and ambiguous associations explicitly. Store large complete snapshots once; return compact deltas and relevant exceptions to the model during repeated checks.

## 4. Review by item ID, then finish the report

A `groups.json` file is a list of `{id, normalized_title, items: [item_id, ...]}` (optional `display_title`). IDs are deterministic `source-001`, etc., from verified page order. Unassigned rows remain singleton clusters in checkpoints, not automatically reviewed topics. Preserve every row, including technical exclusions and non-selected items. The builder does not decide whether two events are the same.

```text
python3 <skill>/scripts/build_capture_ledger.py --captures <run-dir>/captures --run <run-dir>/run.json --output <run-dir>/checkpoint.json --partial
python3 <skill>/scripts/build_capture_ledger.py --captures <run-dir>/captures --run <run-dir>/run.json --groups <run-dir>/groups.json --output <run-dir>/review-checkpoint.json --partial --review-template <run-dir>/review.json
python3 <skill>/scripts/finalize_report.py --run-dir <run-dir> --limit 10
```

`--partial` is solely for internal checkpoints and cannot be published. Create the review template after forming the intended groups; it contains the current decision fingerprint, eligible IDs, `status: "pending"` and `reviewed_at: null`. Inspect all eligible ID/title records, including those retained as singletons. Only after actual semantic review set `status: "complete"` and its real timezone-aware `reviewed_at`; the reviewed IDs must cover all eligible items exactly once. Review completion cannot predate the source captures it covers. If grouping or eligibility changes, rebuild a fresh template and review the changed decisions. A same-item observed URL backfill alone does not invalidate semantic review. The template creation refuses to overwrite an existing review file.

A full build requires all nine complete sources with replayable runtime evidence, an explicit groups file (an intentionally reviewed empty array is valid), and complete review. It derives counts, IDs and final titles from rows, not model arithmetic. Unknown/duplicate IDs, stale review, inconsistent evidence and bad selected links block publication. A no-topic report is valid only after review, never merely because groups were omitted.

The finalizer reads `run.json`, `captures/`, `groups.json`, `review.json`, optional `corrections.json`, and an initialized `timing.jsonl`. Its `--help` exposes path overrides and a consistent requested limit. It runs build, ranking, selected links, generation and independent final validation; failure stops the attempt with actionable errors. Every attempt lives under `attempts/<id>/` with actual `validation-results.json`. `latest-attempt.json` records the current outcome; `latest-success.json` changes only on success. Use the current successful report path, not a previous success after current failure. No manual Markdown edits.

### Append-only, observed corrections

For a missing or malformed link, retain the exact `raw_url` and use effective `url: null` until a usable original link is observed. This does not remove the candidate. Only selected required-link items need extra link backfill. Save `corrections.json` as an array of records and pass it to checkpoint builds with `--corrections`; the finalizer uses it automatically. Each record has this structure (replace placeholders only from actual observations):

```json
{
  "id": "unique-correction-id",
  "run_id": "current-run-id",
  "source": "weibo",
  "item_id": "weibo-001",
  "title": "exact captured title",
  "display_order": 1,
  "corrected_at": "actual ISO time with timezone",
  "before": {"url": null},
  "after": {"url": "observed original-entry URL"},
  "evidence": "specific same-row inspection and why this corrects the captured value",
  "observation": {
    "observed_at": "actual ISO time with timezone",
    "document_url": "observed same-source document URL",
    "observed_title": "exact captured title",
    "observed_url": "observed original-entry URL"
  }
}
```

`before` must match the original raw value or the previous correction, not an invented/normalized replacement. Keep earlier records and append subsequent corrections; timestamps cannot run backward. URL changes must match the same-row observation and cannot erase a link. Eligibility changes require paired `eligible`/`exclusion_reason` in both before and after, one of the existing technical reasons, and specific evidence. Title, source, item identity and order are immutable. The ledger preserves raw values and correction IDs, and final checks independently replay the chain. If a corrected link reveals a different event identity, repair grouping and renew review. Site-wide access failures still follow the original stop/restart rules.

## 5. Measure without guessing causes

Use `run_timing.py start ... --run-id ID --id UNIQUE --phase collect --source bjnews` and matching `end ... --id UNIQUE` at actual boundaries. End the current span before the next. Useful phases: prepare, preflight, readiness, collect, persist, cluster, core_read, validate, handoff. Record only meaningful boundaries, batching transitions with the associated work when possible.

End the active browser/cluster span at its actual boundary before finalization. The finalizer automatically times its own local persistence and validation when timing is initialized, and closes its own spans even on failure. It will not silently end an unrelated active span or mark the report delivered.

`run_timing.py report <timing.jsonl> --run-id ID` reports disjoint phase/source spans, gaps and open state. Phase/source totals are two views of the same intervals, not additive costs. Label waits `readiness`, not `network`, unless actual request timing supports that attribution. Only `finish --outcome delivered` after observable delivery, or `--outcome aborted` on actual abort; if final chat delivery cannot yet be observed, leave the timer open and report only the observed elapsed boundary. `--test-at` is explicitly fixture-only and must never label live news work.

## Regression boundary

`test_*` scripts and `*_test_fixtures.py` generate or use synthetic data for offline testing only. Never import their factories to fill live captures, source evidence or review completion. A passing synthetic exercise is not a live news run.

Run Python `unittest discover` and the Node test file after modifying helpers. Test lossless transfer including chunk gaps/conflicts, source/run isolation, title/link/order preservation, duplicate accounting, incomplete-source rejection, premature stability, wrong iframe, late growth, unresolved related headlines and partial-ledger rejection. Frozen input plus unchanged clustering must produce identical ranking/report. Live pages naturally change: equal historical counts are not the criterion. Do not claim end-to-end speedup until measured live.
