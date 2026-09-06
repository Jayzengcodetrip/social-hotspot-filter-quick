import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, rmSync, mkdirSync, symlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { checkDocumentContext, createHomepageState, observeHomepage, selectBeijingHeadlines, replaySourceTrace } from "./browser_collection.mjs";

const context = { targetUrl: "https://www.caixin.com/", topLevelUrl: "https://www.caixin.com/", documentUrl: "https://www.caixin.com/", isMainDocument: true };
const items = count => Array.from({ length: count }, (_, i) => ({ title: `原始标题${i + 1}`, url: `https://www.caixin.com/article/${i + 1}.html` }));
function observation(timestampMs, overrides = {}) {
  return { timestampMs, phase: "sample", context, geometry: { pageHeight: 10000, scrollY: 9000, viewportHeight: 1000 },
    footerReached: true, loading: false, lastRelevantMutationAtMs: 0,
    readinessEvidence: { observedAtMs: timestampMs, pendingState: "none", basis: "explicit_candidate_load_complete", detail: "Fixture: all requested candidate batches completed and no pending batch remains" },
    unresolvedCount: 0, items: items(145),
    orderEvidence: { observedAtMs: timestampMs, basis: "full_dom_order", detail: "Fixture final DOM order", items: overrides.items ?? items(145) }, ...overrides };
}
function round(timestampMs, id, overrides = {}) {
  return observation(timestampMs, { phase: "scroll_round", roundId: id, roundStartedAtMs: timestampMs - 1600, ...overrides });
}

test("Caixin chart iframe fails before geometry is trusted", () => {
  assert.equal(checkDocumentContext(context).ok, true);
  const iframe = { ...context, documentUrl: "https://cxdata.caixin.com/index/dataChartGroup", isMainDocument: false };
  assert.equal(checkDocumentContext(iframe).code, "wrong_document_scope");
  const state = observeHomepage(createHomepageState(), observation(0, { context: iframe, geometry: { pageHeight: 555, scrollY: 0, viewportHeight: 555 } }));
  assert.equal(state.complete, false);
  assert.equal(state.seenItems.length, 0);
  assert.equal(state.lastGeometry, null);
});

test("unknown scope and unexpected same-domain sections cannot prove homepage", () => {
  assert.equal(checkDocumentContext({ ...context, isMainDocument: undefined }).ok, false);
  assert.equal(checkDocumentContext({ ...context, topLevelUrl: "https://www.caixin.com/other/", documentUrl: "https://www.caixin.com/other/" }).ok, false);
});

test("an accepted redirect cannot substitute a different source domain", () => {
  const foreign = "https://example.org/";
  assert.equal(checkDocumentContext({ ...context, topLevelUrl: foreign, documentUrl: foreign, acceptedMainUrls: [foreign] }).code, "redirect_outside_source_family");
  const caixinRoot = "https://caixin.com/";
  assert.equal(checkDocumentContext({ ...context, topLevelUrl: caixinRoot, documentUrl: caixinRoot, acceptedMainUrls: [caixinRoot] }).ok, true);
  const impersonator = "https://caixin.com.example.org/";
  assert.equal(checkDocumentContext({ ...context, topLevelUrl: impersonator, documentUrl: impersonator, acceptedMainUrls: [impersonator] }).ok, false);
});

test("two instant stable reads at 145 do not complete; late growth resets", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(800, "rapid1", { roundStartedAtMs: 100 }));
  state = observeHomepage(state, round(1580, "rapid2", { roundStartedAtMs: 900 }));
  assert.equal(state.complete, false);
  assert.equal(state.noNewItemRounds, 0);
  state = observeHomepage(state, round(3200, "slow1"));
  assert.equal(state.noNewItemRounds, 1);
  state = observeHomepage(state, round(5000, "slow2"));
  assert.equal(state.complete, false);
  state = observeHomepage(state, observation(6500, { phase: "final_recheck" }));
  assert.equal(state.complete, false);
  state = observeHomepage(state, observation(7000, { phase: "final_recheck", items: items(213), geometry: { pageHeight: 13000, scrollY: 12000, viewportHeight: 1000 } }));
  assert.equal(state.seenItems.length, 213);
  assert.equal(state.noNewItemRounds, 0);
  assert.equal(state.complete, false);
});

test("two genuinely spaced quiet rounds plus delayed final check complete", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "r1"));
  state = observeHomepage(state, round(3800, "r2"));
  assert.equal(state.noNewItemRounds, 2);
  assert.equal(state.complete, false);
  state = observeHomepage(state, observation(15800, { phase: "final_recheck" }));
  assert.equal(state.complete, true);
  assert.equal(state.reason, "footer_stable_after_delayed_recheck");
  const later = observeHomepage(state, observation(19000, { items: items(146) }));
  assert.equal(later.complete, false);
  assert.equal(later.noNewItemRounds, 0);
});

test("same round, overlapping rounds and polls never manufacture two rounds", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "r1"));
  state = observeHomepage(state, round(4000, "r1"));
  assert.equal(state.noNewItemRounds, 1);
  state = observeHomepage(state, round(4100, "r2", { roundStartedAtMs: 1500 }));
  assert.equal(state.noNewItemRounds, 1);
  state = observeHomepage(state, observation(20000));
  assert.equal(state.noNewItemRounds, 1);
  assert.equal(state.complete, false);
});

test("genuine quiet rounds still do not complete before the observed 11s late growth", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "r1"));
  state = observeHomepage(state, round(3800, "r2"));
  state = observeHomepage(state, observation(5800, { phase: "final_recheck" }));
  assert.equal(state.complete, false);
  assert.equal(state.reason, "final_recheck_too_early");
  state = observeHomepage(state, observation(11000, { phase: "final_recheck", items: items(213), lastRelevantMutationAtMs: 11000 }));
  assert.equal(state.seenItems.length, 213);
  assert.equal(state.noNewItemRounds, 0);
  assert.equal(state.complete, false);
});

test("measured slow load extends final guard; loading:false without positive evidence fails", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "r1", { observedLoadDelayMs: 20000 }));
  state = observeHomepage(state, round(3800, "r2"));
  assert.equal(state.requiredFinalDelayMs, 25000);
  state = observeHomepage(state, observation(15800, { phase: "final_recheck" }));
  assert.equal(state.complete, false);
  state = observeHomepage(state, observation(28800, { phase: "final_recheck" }));
  assert.equal(state.complete, true);
  state = observeHomepage(state, observation(30000, { phase: "final_recheck", readinessEvidence: undefined }));
  assert.equal(state.complete, false);
  assert.equal(state.reason, "positive_readiness_evidence_missing");
});

test("a slow load observed above the footer still extends the delayed final check", () => {
  let state = observeHomepage(createHomepageState(), observation(0, {
    footerReached: false, geometry: { pageHeight: 10000, scrollY: 0, viewportHeight: 1000 }, observedLoadDelayMs: 20000,
  }));
  assert.equal(state.maxObservedLoadDelayMs, 20000);
  state = observeHomepage(state, round(2000, "r1"));
  state = observeHomepage(state, round(3800, "r2"));
  state = observeHomepage(state, observation(15800, { phase: "final_recheck" }));
  assert.equal(state.complete, false);
  assert.equal(state.reason, "final_recheck_too_early");
  state = observeHomepage(state, observation(28800, { phase: "final_recheck" }));
  assert.equal(state.complete, true);
});

test("first-seen B then A never replaces explicit final page order A then B", () => {
  const a = { title: "A", url: null }, b = { title: "B", url: "javascript:void(0)" };
  let state = observeHomepage(createHomepageState(), observation(0, { items: [b] }));
  state = observeHomepage(state, observation(2000, { items: [a, b] }));
  state = observeHomepage(state, round(4000, "r1", { items: [a, b] }));
  state = observeHomepage(state, round(5800, "r2", { items: [a, b] }));
  state = observeHomepage(state, observation(17800, { phase: "final_recheck", items: [a, b] }));
  assert.equal(state.complete, true);
  assert.deepEqual(state.seenItems.map(item => item.title), ["B", "A"]);
  assert.deepEqual(state.orderedItems.map(item => item.title), ["A", "B"]);
  state = observeHomepage(state, observation(19000, { phase: "final_recheck", items: [a, b], orderEvidence: undefined }));
  assert.equal(state.complete, false);
  assert.equal(state.reason, "final_page_order_evidence_missing");
});

test("omitted earlier virtual rows and segmented-order gaps block completion", () => {
  for (const orderEvidence of [
    { observedAtMs: 15800, basis: "full_dom_order", detail: "Fixture", items: items(144) },
    { observedAtMs: 15800, basis: "segmented_virtual_order", detail: "Fixture", items: items(145), segments: [{ startOrder: 2, count: 145, observedAtMs: 15000, detail: "Fixture" }] },
  ]) {
    let state = observeHomepage(createHomepageState(), observation(0));
    state = observeHomepage(state, round(2000, "r1"));
    state = observeHomepage(state, round(3800, "r2"));
    state = observeHomepage(state, observation(15800, { phase: "final_recheck", orderEvidence }));
    assert.equal(state.complete, false);
  }
});

test("footer alone, iframe geometry, unknown loading and timeouts never complete", () => {
  for (const override of [
    { geometry: { pageHeight: 10000, scrollY: 2000, viewportHeight: 1000 } },
    { geometry: undefined }, { loading: undefined }, { loading: true },
    { lastRelevantMutationAtMs: undefined }, { unresolvedCount: 1 }, { readinessEvidence: undefined },
  ]) {
    let state = observeHomepage(createHomepageState(), observation(0));
    state = observeHomepage(state, round(2000, "r1", override));
    state = observeHomepage(state, round(4000, "r2", override));
    state = observeHomepage(state, observation(1000000, { phase: "final_recheck", ...override }));
    assert.equal(state.complete, false);
  }
});

test("actual mutations and viewport changes invalidate an otherwise quiet end", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "r1"));
  state = observeHomepage(state, round(3800, "r2"));
  state = observeHomepage(state, observation(5800, { phase: "final_recheck", lastRelevantMutationAtMs: 5700 }));
  assert.equal(state.complete, false);
  assert.equal(state.noNewItemRounds, 0);
  state = observeHomepage(state, round(8000, "r3", { geometry: { pageHeight: 10000, scrollY: 9200, viewportHeight: 800 } }));
  assert.equal(state.pageHeightStable, false);
  assert.equal(state.noNewItemRounds, 0);
});

test("config cannot remove two-round, delay or quiet safety requirements", () => {
  for (const options of [{ requiredNoNewRounds: 1 }, { finalRecheckDelayMs: 0 }, { quietWindowMs: 0 }, { minRoundIntervalMs: 0 }]) {
    assert.throws(() => createHomepageState(options));
  }
  const tamperedState = createHomepageState();
  tamperedState.config.finalRecheckDelayMs = 0;
  assert.throws(() => observeHomepage(tamperedState, observation(0)));
});

test("state cannot accidentally accumulate or complete a different source", () => {
  let state = observeHomepage(createHomepageState(), observation(0));
  state = observeHomepage(state, round(2000, "wrong-source", { context: {
    targetUrl: "https://www.bjnews.com.cn/", topLevelUrl: "https://www.bjnews.com.cn/",
    documentUrl: "https://www.bjnews.com.cn/", isMainDocument: true,
  }, items: [{ title: "另一站的标题", url: "https://www.bjnews.com.cn/detail/1.html" }] }));
  assert.equal(state.reason, "source_context_changed");
  assert.equal(state.seenItems.length, 145);
  assert.equal(state.complete, false);
});

const bj = (text, role, suffix = "1", displayOrder = 1) => ({ cardId: `card-${suffix}`, text, role,
  resolvedUrl: `https://www.bjnews.com.cn/detail/${suffix}.html`, rawHref: `/detail/${suffix}.html`, displayOrder,
  pairingVerified: true, articleLinkVerified: true });

test("primary headline wins over same-article excerpt without rewriting text", () => {
  const result = selectBeijingHeadlines([bj("摘要文字不是标题", "summary"), bj(" 原始标题：标点不改！ ", "primary_title")]);
  assert.equal(result.ready, true);
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0].title, " 原始标题：标点不改！ ");
  assert.equal(result.duplicates[0].reason, "excerpt_not_headline");
});

test("distinct related linked headline survives even if its class resembled tips", () => {
  const result = selectBeijingHeadlines([bj("主标题", "primary_title"), { ...bj("关联新闻标题", "related_title", "2", 2), observedClass: "pin_tips" }]);
  assert.deepEqual(result.rows.map(row => row.title), ["主标题", "关联新闻标题"]);
  assert.equal(result.ready, true);
});

test("sparse DOM order is audit metadata, not a sparse ledger display_order", () => {
  const result = selectBeijingHeadlines([bj("标题三", "primary_title", "3", 7), bj("标题一", "primary_title", "1", 1)]);
  assert.deepEqual(result.rows.map(row => row.observed_order), [1, 7]);
  assert.equal(Object.hasOwn(result.rows[0], "display_order"), false);
});

test("unknown title, summary-only and conflicting primary texts stay unresolved", () => {
  for (const candidates of [[bj("可能是标题", "unknown")], [bj("只有摘要", "summary")],
    [bj("标题甲", "primary_title"), bj("标题乙", "primary_title")]]) {
    const result = selectBeijingHeadlines(candidates);
    assert.equal(result.ready, false);
    assert.equal(result.rows.length, 0);
    assert.ok(result.unresolved.length > 0);
  }
});

test("unknown candidate beside a valid title remains visible in exception queue", () => {
  const result = selectBeijingHeadlines([bj("标题", "primary_title"), bj("未识别内容", "unknown")]);
  assert.equal(result.rows.length, 1);
  assert.equal(result.ready, false);
  assert.equal(result.unresolved.length, 1);
});

test("no unverified title-link pairing or foreign destination is accepted", () => {
  const result = selectBeijingHeadlines([{ ...bj("标题", "primary_title"), pairingVerified: false },
    { ...bj("另一标题", "related_title", "2"), resolvedUrl: "https://example.org/article" }]);
  assert.equal(result.ready, false);
  assert.equal(result.rows.length, 0);
  assert.equal(result.unresolved.length, 2);
});

test("untrusted prototype-shaped role names are unresolved", () => {
  assert.equal(selectBeijingHeadlines([bj("标题", "toString")]).ready, false);
  assert.equal(selectBeijingHeadlines([bj("标题", "__proto__")]).rows.length, 0);
});

test("JSON CLI preserves same rows and persists guard state without browser module imports", () => {
  const directory = mkdtempSync(join(tmpdir(), "shf-quick-browser-"));
  try {
    const cli = fileURLToPath(new URL("./browser_collection_cli.mjs", import.meta.url));
    const input = join(directory, "input.json");
    const output = join(directory, "output.json");
    writeFileSync(input, JSON.stringify([bj("精确原始标题", "primary_title")]));
    let result = spawnSync(process.execPath, [cli, "headlines", input, "--output", output], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(readFileSync(output, "utf8")).rows[0].title, "精确原始标题");
    const progress = JSON.parse(result.stdout);
    assert.equal(progress.row_count, 1);
    assert.equal(Object.hasOwn(progress, "rows"), false);
    const statePath = join(directory, "state.json");
    result = spawnSync(process.execPath, [cli, "init", "--output", statePath], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    writeFileSync(input, JSON.stringify(observation(0)));
    const stateNext = join(directory, "state-001.json");
    result = spawnSync(process.execPath, [cli, "observe", input, "--state", statePath, "--output", stateNext], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(readFileSync(stateNext, "utf8")).seenItems.length, 145);
    assert.equal(JSON.parse(result.stdout).item_count, 145);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("CLI --help documents operations and JSON shapes with success status", () => {
  const cli = fileURLToPath(new URL("./browser_collection_cli.mjs", import.meta.url));
  const result = spawnSync(process.execPath, [cli, "--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  for (const text of ["context", "headlines", "init", "observe", "readinessEvidence", "observed_order"]) assert.ok(result.stdout.includes(text));
});

test("CLI refuses existing output, input aliases and any skill-root destination", () => {
  const directory = mkdtempSync(join(tmpdir(), "shf-quick-browser-safe-"));
  try {
    const cli = fileURLToPath(new URL("./browser_collection_cli.mjs", import.meta.url));
    const input = join(directory, "context.json");
    const existing = join(directory, "existing.json");
    writeFileSync(input, JSON.stringify(context));
    writeFileSync(existing, "keep exactly");
    const run = output => spawnSync(process.execPath, [cli, "context", input, "--output", output], { encoding: "utf8" });
    assert.notEqual(run(existing).status, 0);
    assert.equal(readFileSync(existing, "utf8"), "keep exactly");
    assert.notEqual(run(input).status, 0);
    const ownSkillOutput = fileURLToPath(new URL("../never-create-browser-output.json", import.meta.url));
    assert.notEqual(run(ownSkillOutput).status, 0);
    assert.equal(existsSync(ownSkillOutput), false);
    const otherSkill = join(directory, "other-skill");
    mkdirSync(otherSkill);
    writeFileSync(join(otherSkill, "SKILL.md"), "test fixture");
    const alias = join(directory, "skill-alias");
    symlinkSync(otherSkill, alias, "dir");
    assert.notEqual(run(join(alias, "no.json")).status, 0);
    assert.equal(existsSync(join(otherSkill, "no.json")), false);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
