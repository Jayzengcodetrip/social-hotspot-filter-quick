#!/usr/bin/env python3
"""Losslessly import this run's marked browser output; never fetch news or old captures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from validate_ranking import SOURCE_IDS

MARKER = "SHF_CAPTURE_V1 "


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def create_json(path, data):
    """Create atomically, never replace an existing capture or user file."""
    path = Path(path)
    if any((p / "SKILL.md").is_file() for p in [path.parent, *path.parents]):
        raise ValueError("run artifacts must be outside a Skill folder")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".capture-", delete=False) as stream:
            temp_name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.link(temp_name, path)
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def init_run(run_dir, run_id, session_log=None):
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be non-empty")
    manifest = {"capture_schema": 1, "run_id": run_id,
                "started_at": utc_now().isoformat(),
                "tokens": {sid: secrets.token_hex(16) for sid in SOURCE_IDS}}
    if session_log:
        log = Path(session_log).resolve(strict=True)
        stat = log.stat()
        if not log.is_file() or log.suffix != ".jsonl":
            raise ValueError("session-log must be the current thread's explicit JSONL file")
        manifest["session_log"] = {"path": str(log), "offset": stat.st_size,
                                   "device": stat.st_dev, "inode": stat.st_ino}
    create_json(Path(run_dir) / "capture-manifest.json", manifest)
    return manifest


def text_blocks(value):
    if isinstance(value, str):
        yield value
        # Some hosts serialize the content block list as a string.
        if value.lstrip().startswith("["):
            try:
                nested = json.loads(value)
            except json.JSONDecodeError:
                return
            if isinstance(nested, list):
                yield from text_blocks(nested)
    elif isinstance(value, list):
        for block in value:
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                if isinstance(block.get("text"), str):
                    yield block["text"]


def marked_envelopes(text):
    # A browser must explicitly emit one marker followed by one JSON object per line.
    # Do not inspect user messages, code, reasoning, or arbitrary JSON fragments.
    for line in text.splitlines():
        if line.startswith(MARKER):
            value = json.loads(line[len(MARKER):])
            if not isinstance(value, dict):
                raise ValueError("capture envelope must be an object")
            yield value


def session_envelopes(manifest):
    info = manifest.get("session_log")
    if not isinstance(info, dict):
        raise ValueError("no session log bound at init; use an actual browser-export file")
    log = Path(info["path"])
    stat = log.stat()
    if (stat.st_dev, stat.st_ino) != (info["device"], info["inode"]) or stat.st_size < info["offset"]:
        raise ValueError("bound session log was replaced or truncated; do not scan older logs")
    start = parse_time(manifest["started_at"])
    with log.open("rb") as stream:
        stream.seek(info["offset"])
        for raw_line in stream:
            if not raw_line.endswith(b"\n"):
                break  # current append is not complete; retry this bounded import later
            try:
                record = json.loads(raw_line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict) or record.get("type") != "response_item":
                continue
            payload = record.get("payload", {})
            if not isinstance(payload, dict) or payload.get("type") not in {
                "function_call_output", "custom_tool_call_output"
            }:
                continue
            try:
                timestamp = parse_time(record.get("timestamp"))
            except ValueError:
                continue
            if timestamp < start:
                continue
            for block in text_blocks(payload.get("output")):
                yield from marked_envelopes(block)


def assemble(manifest, source, envelopes):
    if source not in SOURCE_IDS:
        raise ValueError("unknown source")
    parts = {}
    expected_parts = None
    start = parse_time(manifest["started_at"])
    now = utc_now()
    for envelope in envelopes:
        if (envelope.get("run_id") != manifest["run_id"] or
                envelope.get("source") != source or
                envelope.get("token") != manifest["tokens"][source]):
            continue
        if type(envelope.get("capture_schema")) is not int or envelope["capture_schema"] != 1:
            raise ValueError("unsupported capture schema")
        index, total = envelope.get("part"), envelope.get("parts")
        if type(index) is not int or type(total) is not int or not 1 <= index <= total <= 10000:
            raise ValueError("invalid chunk index/count")
        if expected_parts is not None and expected_parts != total:
            raise ValueError("conflicting chunk counts")
        expected_parts = total
        timestamp = parse_time(envelope.get("captured_at"))
        if timestamp < start or timestamp > now + timedelta(seconds=60):
            raise ValueError("capture time is outside this run")
        rows = envelope.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("each capture chunk needs actual rows")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                raise ValueError("each row requires its exact title string")
            if "url" not in row or (row["url"] is not None and not isinstance(row["url"], str)):
                raise ValueError("each row requires its observed url or explicit null")
        if index in parts and parts[index] != envelope:
            raise ValueError("conflicting payloads for one chunk; start a fresh run/capture")
        parts[index] = envelope
    if expected_parts is None or set(parts) != set(range(1, expected_parts + 1)):
        raise ValueError("missing marked output/chunks; never fill gaps by model transcription")
    ordered = [parts[i] for i in range(1, expected_parts + 1)]
    rows = [row for part in ordered for row in part["rows"]]
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return ordered, rows, digest


def import_capture(run_dir, source, collection, export_file=None):
    root = Path(run_dir)
    manifest = load_json(root / "capture-manifest.json")
    if not isinstance(collection, dict):
        raise ValueError("collection must contain the actual observed completion evidence")
    if export_file:
        # Portable path: tool-created marked UTF-8 output, not model-retyped rows.
        envelopes = marked_envelopes(Path(export_file).read_text(encoding="utf-8"))
        transport = "browser_output_file"
    else:
        envelopes = session_envelopes(manifest)
        transport = "current_run_marked_tool_output"
    chunks, rows, digest = assemble(manifest, source, envelopes)
    packet = {"source": source, "run_id": manifest["run_id"], "rows": rows,
              "collection": collection,
              "capture": {"transport": transport, "sha256": digest,
                          "chunks": len(chunks), "captured_at": [p["captured_at"] for p in chunks]}}
    target = root / "captures" / (source + ".json")
    evidence = root / "evidence" / (source + ".json")
    if target.exists() or evidence.exists():
        raise ValueError("capture already exists; preserve it and use a fresh run directory")
    create_json(evidence, chunks)
    create_json(target, packet)
    return {"source": source, "rows": len(rows), "sha256": digest, "path": str(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("run_dir", type=Path)
    init.add_argument("--run-id", required=True)
    init.add_argument("--session-log", type=Path,
                      help="optional exact current-thread log; binds only new bytes after init")
    ingest = sub.add_parser("import")
    ingest.add_argument("run_dir", type=Path)
    ingest.add_argument("source", choices=SOURCE_IDS)
    ingest.add_argument("--collection", required=True, type=Path)
    ingest.add_argument("--export-file", type=Path,
                        help="tool-created marked output file; otherwise use bound current log")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_run(args.run_dir, args.run_id, args.session_log)
        else:
            result = import_capture(args.run_dir, args.source, load_json(args.collection), args.export_file)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("FAIL capture: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
