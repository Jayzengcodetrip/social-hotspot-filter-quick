/**
 * Pure collection guards. No browser tool API, navigation, network or disk I/O.
 * These functions validate observations, not the truth of browser actions.
 * Feed them current-run, directly observed data through the documented browser
 * capability. Retain observations as evidence outside the installed skill.
 */

function httpUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

function documentKey(value) {
  const url = httpUrl(value);
  if (!url) return null;
  url.hash = "";
  return url.href;
}

/** Accepted redirects must be directly observed, never guessed or a mirror. */
export function checkDocumentContext(context = {}) {
  const { targetUrl, topLevelUrl, documentUrl, isMainDocument } = context;
  const target = documentKey(targetUrl);
  const top = documentKey(topLevelUrl);
  const actual = documentKey(documentUrl);
  if (!target || !top || !actual || typeof isMainDocument !== "boolean") {
    return { ok: false, code: "unknown_document_context" };
  }
  if (!isMainDocument || actual !== top) {
    return { ok: false, code: "wrong_document_scope", target, top, actual };
  }
  const targetHost = httpUrl(target).hostname;
  const sourceFamily = ["bjnews.com.cn", "caixin.com", "jiemian.com"]
    .find(domain => targetHost === domain || targetHost.endsWith(`.${domain}`));
  const actualHost = httpUrl(actual).hostname;
  const sameSource = sourceFamily
    ? actualHost === sourceFamily || actualHost.endsWith(`.${sourceFamily}`)
    : actualHost === targetHost;
  if (!sameSource) return { ok: false, code: "redirect_outside_source_family", target, top, actual };
  const accepted = [target, ...(context.acceptedMainUrls ?? []).map(documentKey)];
  if (!accepted.includes(actual)) {
    return { ok: false, code: "unexpected_main_document", target, top, actual };
  }
  return { ok: true, code: "main_document_verified", target, top, actual };
}

const DEFAULTS = Object.freeze({
  minRoundIntervalMs: 1500,
  quietWindowMs: 1500,
  finalRecheckDelayMs: 12000,
  requiredNoNewRounds: 2,
  geometryTolerancePx: 2,
});

export function createHomepageState(options = {}) {
  const config = { ...DEFAULTS, ...options };
  for (const key of ["minRoundIntervalMs", "quietWindowMs", "finalRecheckDelayMs"]) {
    if (!Number.isFinite(config[key]) || config[key] < DEFAULTS[key]) {
      throw new Error(`${key} cannot be below the conservative default ${DEFAULTS[key]}`);
    }
  }
  if (!Number.isInteger(config.requiredNoNewRounds) || config.requiredNoNewRounds < 2) {
    throw new Error("requiredNoNewRounds must be an integer >= 2");
  }
  if (!Number.isFinite(config.geometryTolerancePx) || config.geometryTolerancePx < 0 || config.geometryTolerancePx > 10) {
    throw new Error("geometryTolerancePx must be between 0 and 10");
  }
  return {
    config, seenItems: [], orderedItems: [], sourceTarget: null, lastTimestampMs: null, lastChangeAtMs: null,
    lastGeometry: null, lastAcceptedRoundAtMs: null, lastRoundStartedAtMs: null,
    processedRoundIds: [], noNewItemRounds: 0, awaitingFinalSinceMs: null,
    maxObservedLoadDelayMs: 0, requiredFinalDelayMs: config.finalRecheckDelayMs,
    complete: false, pageHeightStable: false, reason: "awaiting_first_observation",
  };
}

function resetRounds(state, reason) {
  state.noNewItemRounds = 0;
  state.awaitingFinalSinceMs = null;
  state.lastAcceptedRoundAtMs = null;
  state.complete = false;
  state.reason = reason;
  return state;
}

function geometryValid(g) {
  return g && [g.pageHeight, g.scrollY, g.viewportHeight].every(Number.isFinite)
    && g.pageHeight > 0 && g.viewportHeight > 0 && g.scrollY >= 0;
}

function itemKey(item) { return JSON.stringify([item.title, item.url]); }

function observedItemValid(item) {
  // A missing/bad href is retained for later link repair, not hidden from coverage.
  return typeof item?.title === "string" && Boolean(item.title.trim())
    && Object.hasOwn(item, "url") && (item.url === null || typeof item.url === "string");
}

/** Explicit page order, never first-discovery order. Segment metadata must come
 * from actual ordered virtual-list reads; it is not permission to guess gaps. */
export function verifyObservedOrder(evidence, items, now, currentItems = items) {
  if (!evidence || evidence.observedAtMs !== now || !Array.isArray(evidence.items)
      || !evidence.items.length || evidence.items.some(item => !observedItemValid(item))
      || typeof evidence.detail !== "string" || !evidence.detail.trim()) {
    throw new Error("final_page_order_evidence_missing");
  }
  if (evidence.basis === "segmented_virtual_order") {
    if (!Array.isArray(evidence.segments) || !evidence.segments.length) throw new Error("order_segments_missing");
    let next = 1;
    for (const segment of evidence.segments) {
      if (segment.startOrder !== next || !Number.isInteger(segment.count) || segment.count < 1
          || !Number.isFinite(segment.observedAtMs) || segment.observedAtMs < 0 || segment.observedAtMs > now
          || typeof segment.detail !== "string" || !segment.detail.trim()) throw new Error("order_segment_gap_or_missing_evidence");
      next += segment.count;
    }
    if (next !== evidence.items.length + 1) throw new Error("order_segments_do_not_cover_rows");
  } else if (evidence.basis !== "full_dom_order") throw new Error("unknown_final_order_basis");
  else if (JSON.stringify(evidence.items.map(itemKey)) !== JSON.stringify(currentItems.map(itemKey))) {
    throw new Error("full_dom_order_must_match_final_observed_dom_items");
  }
  const expected = new Set(items.map(itemKey));
  const actual = new Set(evidence.items.map(itemKey));
  if (expected.size !== actual.size || [...expected].some(key => !actual.has(key))) {
    throw new Error("final_page_order_does_not_cover_observed_items");
  }
  return evidence.items.map(item => ({ title: item.title, url: item.url }));
}

/**
 * observation = {
 *   timestampMs, phase: 'sample'|'scroll_round'|'final_recheck',
 *   context: {targetUrl, topLevelUrl, documentUrl, isMainDocument},
 *   geometry: {pageHeight, scrollY, viewportHeight},
 *   footerReached: boolean, loading: boolean, lastRelevantMutationAtMs,
 *   readinessEvidence: {observedAtMs, pendingState:'none', detail,
 *     basis:'explicit_candidate_load_complete'|'observed_candidate_surface_settled'},
 *   observedLoadDelayMs?, // measured request/trigger-to-content delay, if known
 *   unresolvedCount: integer, items: [{title: exactText, url: observedUrl}],
 *   roundId?, roundStartedAtMs?  // required for each real scroll/load round
 * }
 * Use a single monotonic clock. A round is an actual scroll/load action followed
 * by a quiet readiness observation, not several instant DOM reads. Geometry and
 * mutation times must refer to the verified main candidate surface. If this
 * evidence cannot be obtained, leave the source incomplete; do not invent it.
 * loading:false alone is not positive readiness evidence. Use the current
 * candidate surface's actual load-complete/settled observations; an absent
 * spinner, document.readyState or elapsed timer alone is insufficient. Unknown
 * or pending loading remains incomplete. A final delay is only an extra guard,
 * never proof that arbitrary delayed work cannot arrive. It is at least 12s
 * (the observed 145-to-later-growth gap was approximately 11s), and grows to
 * 1.25 times the largest measured load delay in this source collection.
 * Current observations may repeat earlier items; accumulation never drops them.
 * The final delayed recheck must again read items, main geometry and readiness.
 */
export function observeHomepage(previous, observation) {
  // Serialized state is a convenience, not permission to weaken the guards.
  createHomepageState(previous.config);
  const state = structuredClone(previous);
  state.complete = false;
  const o = observation ?? {};
  const now = o.timestampMs;
  if (!Number.isFinite(now) || now < 0 || (state.lastTimestampMs !== null && now <= state.lastTimestampMs)) {
    return resetRounds(state, "invalid_or_nonmonotonic_timestamp");
  }
  state.lastTimestampMs = now;
  const scope = checkDocumentContext(o.context);
  if (!scope.ok) return resetRounds(state, scope.code);
  if (state.sourceTarget !== null && state.sourceTarget !== scope.target) {
    return resetRounds(state, "source_context_changed");
  }
  state.sourceTarget = scope.target;
  if (!geometryValid(o.geometry)) return resetRounds(state, "unknown_main_geometry");
  if (!Array.isArray(o.items) || !Number.isInteger(o.unresolvedCount) || o.unresolvedCount < 0) {
    return resetRounds(state, "unknown_item_coverage");
  }
  if (!Number.isFinite(o.lastRelevantMutationAtMs) || o.lastRelevantMutationAtMs < 0 || o.lastRelevantMutationAtMs > now) {
    return resetRounds(state, "unknown_readiness_timing");
  }
  const itemMap = new Map(state.seenItems.map(item => [JSON.stringify([item.title, item.url]), item]));
  let added = 0;
  let malformed = 0;
  for (const item of o.items) {
    if (!observedItemValid(item)) {
      malformed += 1;
      continue;
    }
    const key = JSON.stringify([item.title, item.url]);
    if (!itemMap.has(key)) {
      itemMap.set(key, { title: item.title, url: item.url });
      added += 1;
    }
  }
  state.seenItems = [...itemMap.values()];
  state.newItemCount = added;
  const oldGeometry = state.lastGeometry;
  const heightStable = Boolean(oldGeometry)
    && Math.abs(o.geometry.pageHeight - oldGeometry.pageHeight) <= state.config.geometryTolerancePx
    && Math.abs(o.geometry.viewportHeight - oldGeometry.viewportHeight) <= state.config.geometryTolerancePx;
  state.pageHeightStable = heightStable;
  state.lastGeometry = { ...o.geometry };
  if (added || !heightStable) {
    state.lastChangeAtMs = now;
    resetRounds(state, added ? "new_items_observed" : "main_geometry_changed");
  }
  if (malformed || o.unresolvedCount !== 0) return resetRounds(state, "unresolved_candidate_items");
  if (!state.seenItems.length) return resetRounds(state, "empty_candidate_surface");
  if (o.loading !== false) return resetRounds(state, "loading_or_readiness_unknown");
  const evidence = o.readinessEvidence;
  if (!evidence || evidence.observedAtMs !== now || evidence.pendingState !== "none"
      || !["explicit_candidate_load_complete", "observed_candidate_surface_settled"].includes(evidence.basis)
      || typeof evidence.detail !== "string" || !evidence.detail.trim()) {
    return resetRounds(state, "positive_readiness_evidence_missing");
  }
  if (o.observedLoadDelayMs !== undefined) {
    if (!Number.isFinite(o.observedLoadDelayMs) || o.observedLoadDelayMs < 0) {
      return resetRounds(state, "invalid_observed_load_delay");
    }
    state.maxObservedLoadDelayMs = Math.max(state.maxObservedLoadDelayMs, o.observedLoadDelayMs);
  }
  state.requiredFinalDelayMs = Math.max(state.config.finalRecheckDelayMs, Math.ceil(state.maxObservedLoadDelayMs * 1.25));
  // Record valid measured loading delays from top/middle observations as well.
  const atBottom = o.geometry.scrollY + o.geometry.viewportHeight
    >= o.geometry.pageHeight - state.config.geometryTolerancePx;
  if (o.footerReached !== true || !atBottom) return resetRounds(state, "main_footer_not_proved");
  const quietSince = Math.max(state.lastChangeAtMs ?? now, o.lastRelevantMutationAtMs);
  if (now - quietSince < state.config.quietWindowMs) {
    return resetRounds(state, "candidate_surface_not_quiet");
  }
  if (o.phase === "sample") {
    state.reason = "sample_does_not_count_as_scroll_round";
    return state;
  }
  if (o.phase === "final_recheck") {
    if (state.awaitingFinalSinceMs === null || state.noNewItemRounds < state.config.requiredNoNewRounds) {
      state.reason = "stable_scroll_rounds_missing";
    } else if (now - state.awaitingFinalSinceMs < state.requiredFinalDelayMs) {
      state.reason = "final_recheck_too_early";
    } else {
      try {
        state.orderedItems = verifyObservedOrder(o.orderEvidence, state.seenItems, now, o.items);
        state.complete = true;
        state.reason = "footer_stable_after_delayed_recheck";
      } catch (error) {
        state.orderedItems = [];
        state.reason = error.message;
      }
    }
    return state;
  }
  if (o.phase !== "scroll_round") return resetRounds(state, "unknown_observation_phase");
  if (typeof o.roundId !== "string" || !o.roundId || !Number.isFinite(o.roundStartedAtMs)
      || o.roundStartedAtMs < 0 || o.roundStartedAtMs > now) {
    return resetRounds(state, "missing_scroll_round_evidence");
  }
  if (state.processedRoundIds.includes(o.roundId)) {
    state.reason = "repeated_round_does_not_count";
    return state;
  }
  if (now - o.roundStartedAtMs < state.config.minRoundIntervalMs
      || (state.lastAcceptedRoundAtMs !== null && o.roundStartedAtMs < state.lastAcceptedRoundAtMs)
      || (state.lastRoundStartedAtMs !== null && o.roundStartedAtMs <= state.lastRoundStartedAtMs)) {
    state.reason = "scroll_round_not_genuinely_spaced";
    return state;
  }
  state.processedRoundIds.push(o.roundId);
  state.lastRoundStartedAtMs = o.roundStartedAtMs;
  state.lastAcceptedRoundAtMs = now;
  state.noNewItemRounds += 1;
  if (state.noNewItemRounds >= state.config.requiredNoNewRounds) {
    state.awaitingFinalSinceMs ??= now;
    state.reason = "awaiting_delayed_final_recheck";
  } else {
    state.reason = "awaiting_next_spaced_scroll_round";
  }
  return state;
}

/**
 * Select from DOM-observed candidate metadata, not a hardcoded site scraper.
 * candidate = {cardId, text, rawHref, resolvedUrl, displayOrder,
 *   role:'primary_title'|'related_title'|'summary'|'image_alt'|'unknown',
 *   pairingVerified:true, articleLinkVerified:true}
 * `text` must already be the exact observed headline node text. Do not feed
 * container text containing a decorative ordinal and ask the model to rewrite it.
 * Site classes such as .pin_tips do not alone determine a candidate's role:
 * a distinct linked related headline is retained; an excerpt is not promoted.
 *
 * Bounded DOM recipe from the September 4 run (reconfirm on the current page):
 * - Inspect the real news-card container and enumerate all same-card article
 *   anchors together; do not reduce the page to .pin_demo matches only.
 * - A verified headline in .pin_demo is a primary_title. A heading's decorative
 *   .num child is not headline text; select the observed headline text node(s),
 *   preserving their characters, instead of stripping digits with a regex.
 * - .pin_tips can be a summary or the sole related_title for another URL. Use
 *   the same-card structure/link association to establish which; otherwise use
 *   role:'unknown'. Do not classify solely by CSS class or text length.
 * - An h3/li news headline outside those classes remains in scope. Verify its
 *   same-row article anchor. Unclassified cards and image-only/alt-only evidence
 *   go to unresolved rather than disappearing from discovered-card accounting.
 * This is not a universal or live-tested selector adapter. The browser-specific
 * extraction wrapper must be based on its currently observed DOM and supported
 * read-only API; this module never supplies or invents a browser frame selector.
 */
export function selectBeijingHeadlines(candidates) {
  if (!Array.isArray(candidates)) throw new Error("candidates must be an array");
  const groups = new Map();
  const unresolved = [];
  const duplicates = [];
  const rank = { primary_title: 2, related_title: 1 };
  for (let index = 0; index < candidates.length; index += 1) {
    const candidate = { ...candidates[index], inputIndex: index };
    if (typeof candidate.cardId !== "string" || !candidate.cardId
      || typeof candidate.text !== "string" || !candidate.text.trim()
      || !Number.isInteger(candidate.displayOrder) || candidate.displayOrder < 1
      || !httpUrl(candidate.resolvedUrl)
      || candidate.pairingVerified !== true || candidate.articleLinkVerified !== true) {
      unresolved.push({ reason: "missing_same_card_title_link_evidence", candidate });
      continue;
    }
    const url = httpUrl(candidate.resolvedUrl);
    if (url.hostname !== "bjnews.com.cn" && !url.hostname.endsWith(".bjnews.com.cn")) {
      unresolved.push({ reason: "unexpected_article_host", candidate });
      continue;
    }
    if (url.pathname === "/" || url.pathname === "") {
      unresolved.push({ reason: "homepage_is_not_article_link", candidate });
      continue;
    }
    // Hashes address the same article; preserve the original resolved URL below.
    url.hash = "";
    const group = groups.get(url.href) ?? [];
    group.push(candidate);
    groups.set(url.href, group);
  }
  const rows = [];
  for (const [articleKey, group] of groups) {
    const unknown = group.filter(c => !Object.hasOwn(rank, c.role) && c.role !== "summary");
    for (const candidate of unknown) unresolved.push({ reason: "headline_role_unresolved", articleKey, candidate });
    const titles = group.filter(c => Object.hasOwn(rank, c.role));
    if (!titles.length) {
      for (const candidate of group.filter(c => c.role === "summary")) {
        unresolved.push({ reason: "summary_without_observed_headline", articleKey, candidate });
      }
      continue;
    }
    const bestRank = Math.max(...titles.map(c => rank[c.role]));
    const best = titles.filter(c => rank[c.role] === bestRank);
    if (new Set(best.map(c => c.text)).size !== 1) {
      unresolved.push({ reason: "conflicting_exact_headlines", articleKey, candidates: group });
      continue;
    }
    const winner = [...best].sort((a, b) => a.displayOrder - b.displayOrder || a.inputIndex - b.inputIndex)[0];
    rows.push({ title: winner.text, url: winner.resolvedUrl, raw_href: winner.rawHref ?? null,
      card_id: winner.cardId, observed_order: Math.min(...titles.map(c => c.displayOrder)),
      evidence_indices: group.map(c => c.inputIndex) });
    for (const candidate of group.filter(c => c.inputIndex !== winner.inputIndex && !unknown.includes(c))) {
      duplicates.push({ reason: candidate.role === "summary" ? "excerpt_not_headline" : "same_article_secondary_rendering", articleKey, candidate });
    }
  }
  rows.sort((a, b) => a.observed_order - b.observed_order);
  return { rows, unresolved, duplicates, candidateCount: candidates.length, ready: unresolved.length === 0 };
}

export const SOURCE_TARGETS = Object.freeze({
  weibo: "https://s.weibo.com/top/summary", zhihu: "https://www.zhihu.com/hot",
  bilibili: "https://www.bilibili.com/blackboard/activity-trending-topic.html?navhide=1&plat_id=124",
  baidu: "https://top.baidu.com/board?tab=realtime", douyin: "https://www.iesdouyin.com/share/billboard/?id=0",
  jiemian: "https://www.jiemian.com/", bjnews: "https://www.bjnews.com.cn/", caixin: "https://www.caixin.com/",
});
const FIXED_COUNTS = Object.freeze({ weibo: 50, zhihu: 30, bilibili: 30, baidu: 50, douyin: 50 });

/** Replay only current observed transcript. No claimed saved completion state is
 * accepted. Compact itemIndices refer to the unchanged raw capture row array. */
export function replaySourceTrace(trace, rows, runDate) {
  if (!trace || trace.trace_schema !== 1 || !Array.isArray(rows) || !rows.length
      || rows.some(item => !observedItemValid(item))) throw new Error("invalid_trace_or_raw_rows");
  const source = trace.source;
  const expected = FIXED_COUNTS[source];
  const observations = trace.observations;
  if (!Array.isArray(observations) || !observations.length) throw new Error("source_trace_observations_missing");
  const allIndices = rows.map((_, index) => index);
  const indicesToItems = indices => {
    if (!Array.isArray(indices) || indices.some(index => !Number.isInteger(index) || index < 0 || index >= rows.length)
        || new Set(indices).size !== indices.length) throw new Error("invalid_observed_item_indices");
    return indices.map(index => ({ title: rows[index].title, url: rows[index].url }));
  };
  const hydrate = observation => {
    if (Object.hasOwn(observation, "items")) throw new Error("trace_requires_indices_not_retyped_items");
    const result = { ...observation, items: indicesToItems(observation.itemIndices) };
    if (observation.orderEvidence) {
      if (Object.hasOwn(observation.orderEvidence, "items")) throw new Error("trace_order_requires_indices");
      result.orderEvidence = { ...observation.orderEvidence, items: indicesToItems(observation.orderEvidence.itemIndices) };
    }
    return result;
  };
  let prior = -1;
  const seen = new Set();
  for (const observation of observations) {
    if (!Number.isFinite(observation.timestampMs) || observation.timestampMs <= prior) throw new Error("trace_timestamps_not_monotonic");
    prior = observation.timestampMs;
    indicesToItems(observation.itemIndices);
    observation.itemIndices.forEach(index => seen.add(index));
  }
  const requireCoverage = indices => {
    if (JSON.stringify(indices) !== JSON.stringify(allIndices) || seen.size !== rows.length) {
      throw new Error("final_observed_order_must_match_complete_raw_capture_order");
    }
  };
  const requireScope = (o, target = SOURCE_TARGETS[source]) => {
    if (!target || documentKey(o.context?.targetUrl) !== documentKey(target)) throw new Error("trace_target_is_not_fixed_source_surface");
    const scope = checkDocumentContext(o.context);
    if (!scope.ok) throw new Error(scope.code);
    // A directly observed homepage-document redirect is distinct from a section.
    if (["jiemian", "bjnews", "caixin"].includes(source) && httpUrl(scope.actual).pathname !== "/") {
      const homeDocument = /^\/(?:index|default|home)(?:\.(?:html?|shtml|php|aspx?))?\/?$/i.test(httpUrl(scope.actual).pathname);
      if (!homeDocument || typeof o.context.homepageIdentityEvidence !== "string" || !o.context.homepageIdentityEvidence.trim()) {
        throw new Error("news_source_must_be_current_homepage_not_section");
      }
    }
  };
  const ready = o => {
    const evidence = o.readinessEvidence;
    if (o.loading !== false || o.unresolvedCount !== 0 || !evidence || evidence.observedAtMs !== o.timestampMs
        || evidence.pendingState !== "none" || typeof evidence.detail !== "string" || !evidence.detail.trim()
        || !["explicit_candidate_load_complete", "observed_candidate_surface_settled"].includes(evidence.basis)) {
      throw new Error("source_positive_readiness_or_resolved_coverage_missing");
    }
  };
  const summary = { discovered_count: rows.length, end_reached: true, last_item_title: rows.at(-1).title };
  if (["jiemian", "bjnews", "caixin"].includes(source)) {
    if (trace.kind !== "homepage") throw new Error("homepage_trace_kind_required");
    let state = createHomepageState(trace.config ?? {});
    for (const observation of observations) {
      requireScope(observation);
      state = observeHomepage(state, hydrate(observation));
    }
    if (!state.complete) throw new Error(`homepage_incomplete: ${state.reason}`);
    requireCoverage(observations.at(-1).orderEvidence?.itemIndices);
    return { ...summary, scope: "当前完整首页", completion_method: "homepage_footer_stable",
      footer_reached: true, page_height_stable: state.pageHeightStable, no_new_item_rounds: state.noNewItemRounds,
      end_evidence: `原始首页观察轨迹已重放：页脚连续${state.noNewItemRounds}轮无新增，最终复查间隔至少${state.requiredFinalDelayMs}毫秒；最终页面顺序已核对` };
  }
  if (expected) {
    if (trace.kind !== "fixed_hotlist" || rows.length !== expected) throw new Error("fixed_hotlist_count_or_trace_kind_mismatch");
    for (const observation of observations) requireScope(observation);
    const last = observations.at(-1);
    ready(last);
    if (last.listEnd?.reached !== true || typeof last.listEnd.detail !== "string" || !last.listEnd.detail.trim()) {
      throw new Error("hotlist_end_observation_missing");
    }
    verifyObservedOrder(hydrate(last).orderEvidence, rows, last.timestampMs, hydrate(last).items);
    requireCoverage(last.orderEvidence?.itemIndices);
    return { ...summary, scope: "当前完整热榜", completion_method: "fixed_count", expected_item_count: expected,
      end_evidence: `原始热榜末尾已观察，${expected}条及最终页面顺序已核对：${last.listEnd.detail}` };
  }
  if (source !== "people" || trace.kind !== "current_issue") throw new Error("unknown_source_trace_kind");
  const issue = trace.issue;
  if (!issue || issue.date !== runDate || !Number.isInteger(issue.totalPages) || issue.totalPages < 1
      || !Array.isArray(issue.pageUrls) || issue.pageUrls.length !== issue.totalPages
      || new Set(issue.pageUrls).size !== issue.totalPages || observations.length !== issue.totalPages
      || typeof issue.indexEvidence !== "string" || !issue.indexEvidence.trim()) throw new Error("current_issue_index_evidence_missing");
  const issueUrl = url => httpUrl(url)?.hostname === "paper.people.com.cn";
  if (!issueUrl(issue.indexContext?.targetUrl) || !checkDocumentContext(issue.indexContext).ok) throw new Error("current_issue_index_scope_invalid");
  const pageOrder = [];
  for (let index = 0; index < observations.length; index += 1) {
    const observation = observations[index];
    if (!issueUrl(issue.pageUrls[index]) || observation.pageNumber !== index + 1) throw new Error("current_issue_page_order_or_source_invalid");
    requireScope(observation, issue.pageUrls[index]);
    ready(observation);
    if (observation.pageEnd?.reached !== true || typeof observation.pageEnd.detail !== "string" || !observation.pageEnd.detail.trim()) throw new Error("issue_page_end_evidence_missing");
    if (observation.itemIndices.length) verifyObservedOrder(hydrate(observation).orderEvidence, indicesToItems(observation.itemIndices), observation.timestampMs);
    else if (typeof observation.emptyPageEvidence !== "string" || !observation.emptyPageEvidence.trim()) throw new Error("empty_issue_page_requires_observed_evidence");
    pageOrder.push(...(observation.orderEvidence?.itemIndices ?? []));
  }
  requireCoverage(pageOrder);
  return { ...summary, scope: "当期全部报纸版面", completion_method: "current_issue_all_pages", issue_date: issue.date,
    issue_total_pages: issue.totalPages, issue_read_pages: observations.length,
    end_evidence: `当前日期版面目录及全部${issue.totalPages}版逐版原始标题已核对：${issue.indexEvidence}` };
}
