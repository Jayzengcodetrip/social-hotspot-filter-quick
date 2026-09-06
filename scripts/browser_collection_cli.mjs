#!/usr/bin/env node
/**
 * JSON-file bridge for browser runtimes that cannot import local modules.
 * Current browser observations must be persisted through the environment's
 * supported structured-output path first, without hand-retyping raw headlines.
 * This command does not browse, wait, infer readiness or fetch stored rankings.
 * Save outputs in the current run folder, never the installed skill directory.
 *
 * node browser_collection_cli.mjs context context.json --output scope.json
 * node browser_collection_cli.mjs headlines candidates.json --output cards.json
 * node browser_collection_cli.mjs init --output state.json
 * node browser_collection_cli.mjs init --config config.json --output state.json
 * node browser_collection_cli.mjs observe observation.json --state state.json --output state-next.json
 */
import { readFileSync, writeFileSync, realpathSync, existsSync } from "node:fs";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { checkDocumentContext, createHomepageState, observeHomepage, selectBeijingHeadlines, replaySourceTrace } from "./browser_collection.mjs";

const args = process.argv.slice(2);
const command = args.shift();
const HELP = `Pure browser-observation guards; no navigation, waiting, network, or readiness inference.
Usage:
  node browser_collection_cli.mjs context context.json [--output fresh-scope.json]
  node browser_collection_cli.mjs headlines candidates.json [--output fresh-cards.json]
  node browser_collection_cli.mjs init [--config config.json] [--output state-000.json]
  node browser_collection_cli.mjs observe observation.json --state state-000.json [--output state-001.json]
  node browser_collection_cli.mjs replay trace-and-rows.json  # '-' reads JSON stdin
  node browser_collection_cli.mjs --help

JSON shapes:
  context: {targetUrl,topLevelUrl,documentUrl,isMainDocument,acceptedMainUrls?}
    Accepted URLs must be directly observed redirects within the original source family.
  candidates: [{cardId,text,rawHref,resolvedUrl,displayOrder,role,pairingVerified:true,articleLinkVerified:true}]
    role: primary_title | related_title | summary | image_alt | unknown
    Output rows preserve sparse DOM observed_order; the ledger builder assigns contiguous display_order.
  config: {minRoundIntervalMs?,quietWindowMs?,finalRecheckDelayMs?,requiredNoNewRounds?,geometryTolerancePx?}
    Defaults: 1500ms,1500ms,12000ms,2,2px; timing safety minima cannot be lowered.
  observation: {timestampMs,phase,context,geometry:{pageHeight,scrollY,viewportHeight},
    footerReached,loading,lastRelevantMutationAtMs,unresolvedCount,items:[{title,url}],
    readinessEvidence:{observedAtMs,pendingState:'none',basis,detail},
    observedLoadDelayMs?,roundId?,roundStartedAtMs?}
    final_recheck additionally requires orderEvidence:{observedAtMs,basis,detail,items:[{title,url}],segments?}.
    basis is full_dom_order or segmented_virtual_order; never discovery order.
  replay: {trace:{trace_schema:1,run_id,source,captured_at,kind,observations:[...]},rows,run_date}
    trace observations/orderEvidence use itemIndices into unchanged raw rows, not retyped items.
    kind: fixed_hotlist | homepage | current_issue. Replay ignores saved state and derives completion.
    phase: sample | scroll_round | final_recheck
    basis: explicit_candidate_load_complete | observed_candidate_surface_settled
    round fields are required for real scroll/load rounds; timestamps use one monotonic clock.
    Unknown/pending loading is incomplete; loading:false or an elapsed timer alone is not evidence.

Outputs: --output requires a fresh file outside all skill roots and cannot overwrite inputs.
With --output stdout contains only a compact result summary. Without it stdout contains full JSON.
Preserve numbered observation/state files in the current run folder, never the installed Skill.
`;
if (["--help", "-h", "help"].includes(command) && args.length === 0) {
  process.stdout.write(HELP);
  process.exit(0);
}
const values = {};
let input;
try {
  while (args.length) {
    const arg = args.shift();
    if (["--config", "--state", "--output"].includes(arg)) {
      if (!args.length || args[0].startsWith("--") || Object.hasOwn(values, arg)) throw new Error(`Missing or repeated ${arg}`);
      values[arg] = args.shift();
    } else if (arg.startsWith("--") || input !== undefined) {
      throw new Error(`Unexpected argument: ${arg}`);
    } else input = arg;
  }
  const read = path => JSON.parse(readFileSync(path === "-" ? 0 : path, "utf8"));
  let result;
  if (command === "init") {
    if (input !== undefined || values["--state"]) throw new Error("init accepts --config and --output only");
    result = createHomepageState(values["--config"] ? read(values["--config"]) : {});
  } else {
    if (!input || values["--config"]) throw new Error(`${command} requires an input JSON file and does not accept --config`);
    if (command === "context") result = checkDocumentContext(read(input));
    else if (command === "headlines") result = selectBeijingHeadlines(read(input));
    else if (command === "observe") {
      if (!values["--state"]) throw new Error("observe requires --state");
      result = observeHomepage(read(values["--state"]), read(input));
    } else if (command === "replay") {
      const value = read(input);
      result = replaySourceTrace(value.trace, value.rows, value.run_date);
    } else throw new Error("Use one of: context, headlines, init, observe, replay");
    if (command !== "observe" && values["--state"]) throw new Error(`${command} does not accept --state`);
  }
  const output = JSON.stringify(result, null, 2) + "\n";
  if (values["--output"]) {
    const requested = resolve(values["--output"]);
    const destination = join(realpathSync(dirname(requested)), basename(requested));
    const skillRoot = realpathSync(fileURLToPath(new URL("../", import.meta.url)));
    const relativeToSkill = relative(skillRoot, destination);
    if (relativeToSkill === "" || (!relativeToSkill.startsWith(`..${sep}`) && relativeToSkill !== "..")) {
      throw new Error("Output must be outside the Skill directory");
    }
    // Also protect another installed copy, resolving symlinked parent paths.
    for (let ancestor = dirname(destination); ; ancestor = dirname(ancestor)) {
      if (existsSync(join(ancestor, "SKILL.md"))) throw new Error("Output must be outside all Skill directories");
      if (dirname(ancestor) === ancestor) break;
    }
    for (const source of [input, values["--state"], values["--config"]].filter(value => value && value !== "-")) {
      if (realpathSync(source) === destination) throw new Error("Output cannot overwrite an input or state file");
    }
    writeFileSync(destination, output, { encoding: "utf8", flag: "wx" });
    const summary = { output: destination };
    if (command === "context") Object.assign(summary, { ok: result.ok, code: result.code });
    else if (command === "headlines") Object.assign(summary, { ready: result.ready, row_count: result.rows.length, unresolved_count: result.unresolved.length });
    else if (command === "replay") Object.assign(summary, result);
    else Object.assign(summary, { complete: result.complete, reason: result.reason, item_count: result.seenItems.length, no_new_rounds: result.noNewItemRounds });
    process.stdout.write(JSON.stringify(summary) + "\n");
  } else process.stdout.write(output);
} catch (error) {
  process.stderr.write(`browser_collection_cli: ${error.message}\n`);
  process.exitCode = 1;
}
