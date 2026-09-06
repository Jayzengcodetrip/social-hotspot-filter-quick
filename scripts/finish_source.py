#!/usr/bin/env python3
"""Verify actual source observations and losslessly finish one source, once.

No browser, requests, sleeps, or model transcription. Raw rows are imported only
from marked current-run browser output; --trace is its compact observed transcript.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from capture_io import (assemble, create_json, load_json, marked_envelopes,
                        session_envelopes)
from source_evidence import make_runtime_evidence

SOURCE_IDS = ("weibo", "zhihu", "bilibili", "baidu", "douyin", "jiemian", "bjnews", "caixin", "people")


def finish_source(run_dir, source, trace, export_file=None, node=None):
    root = Path(run_dir)
    manifest = load_json(root / "capture-manifest.json")
    if not isinstance(trace, dict) or trace.get("token") != manifest["tokens"].get(source):
        raise ValueError("trace must carry this run's actual source capture token")
    envelopes = (marked_envelopes(Path(export_file).read_text(encoding="utf-8"))
                 if export_file else session_envelopes(manifest))
    chunks, rows, digest = assemble(manifest, source, envelopes)
    captured_times = {chunk["captured_at"] for chunk in chunks}
    if captured_times != {trace.get("captured_at")}:
        raise ValueError("trace final capture timestamp must match all marked row chunks")
    run_path = root / "run.json"
    run = load_json(run_path) if run_path.exists() else {}
    if run and run.get("run_id") != manifest["run_id"]:
        raise ValueError("run.json does not match capture manifest run_id")
    evidence, collection = make_runtime_evidence(trace, source, manifest["run_id"], rows,
                                                manifest["started_at"], run_date=run.get("date"), node=node)
    # Eligibility/technical duplicate counts are not guessed here. The ledger
    # builder derives them later, including evidence-backed corrections.
    collection["runtime_evidence"] = evidence
    packet = {"source": source, "run_id": manifest["run_id"], "rows": rows, "collection": collection,
              "capture": {"transport": "browser_output_file" if export_file else "current_run_marked_tool_output",
                          "sha256": digest, "chunks": len(chunks), "captured_at": [chunk["captured_at"] for chunk in chunks]}}
    target, raw_evidence = root / "captures" / f"{source}.json", root / "evidence" / f"{source}.json"
    # Validate both existing sides before writing either. A crash after the first
    # immutable write is resumable, but conflicting original data is never replaced.
    prior_complete = target.exists() and raw_evidence.exists()
    for path, expected in [(raw_evidence, chunks), (target, packet)]:
        if path.exists() and load_json(path) != expected:
            raise ValueError("source capture already exists with conflicting content; preserve raw data and use evidence-backed corrections")
    for path, expected in [(raw_evidence, chunks), (target, packet)]:
        if not path.exists():
            try:
                create_json(path, expected)
            except FileExistsError:
                if load_json(path) != expected:
                    raise ValueError("concurrent conflicting source capture preserved")
    return {"source": source, "rows": len(rows), "complete": True,
            "already_finished": prior_complete,
            "completion_method": collection["completion_method"], "last_item_title": rows[-1]["title"],
            "sha256": digest, "path": str(target), "next_action": "collect_next_source_or_review_grouping"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("source", choices=SOURCE_IDS)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--export-file", type=Path, help="actual marked browser output; otherwise bound current tool-output log")
    parser.add_argument("--node", help="supported Node executable; alternatively set SHF_NODE")
    args = parser.parse_args()
    try:
        print(json.dumps(finish_source(args.run_dir, args.source, load_json(args.trace), args.export_file, args.node), ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("FAIL finish source: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
