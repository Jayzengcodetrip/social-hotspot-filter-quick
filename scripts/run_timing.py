#!/usr/bin/env python3
"""Small, disjoint wall-clock timing sidecar; never a network profiler.

Keep the JSONL outside the installed skill. Mark boundaries when they occur;
never reconstruct missing durations. --request-at accepts only an observed host
request timestamp, not an estimate. Without it, elapsed starts at initialization.
--test-at is for deterministic fixtures only and is visibly recorded as such.

    run_timing.py init work/timing.jsonl --run-id morning
    run_timing.py start work/timing.jsonl --run-id morning --id bj-read --phase collect --source bjnews
    run_timing.py end work/timing.jsonl --run-id morning --id bj-read
    run_timing.py finish work/timing.jsonl --run-id morning --outcome delivered
    run_timing.py report work/timing.jsonl --run-id morning

One active span is allowed: end it before changing phase/source. Thus phase
totals are disjoint, and elapsed is always end minus origin, never span sums.
Use delivered only after actual delivery is observable; otherwise leave open.
Writes validate the complete event stream and atomically replace a locked file.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile


SOURCES = {"weibo", "zhihu", "bilibili", "baidu", "douyin", "jiemian", "bjnews", "caixin", "people"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SKILL_ROOT = Path(__file__).resolve().parent.parent


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return result


def name(value, label):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(f"invalid {label}; use a short ASCII identifier")


def validate(events, run_id):
    name(run_id, "run ID")
    if not events:
        raise ValueError("empty timing log")
    active, seen, finished, previous = None, set(), None, None
    clock = events[0].get("clock") if isinstance(events[0], dict) else None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError("each event must be an object")
        kind = event.get("event")
        extras = {"init": {"request_at"}, "start": {"id", "phase", "source"},
                  "end": {"id"}, "finish": {"outcome"}}
        if kind not in extras or set(event) != {"version", "run_id", "event", "at", "clock"} | extras[kind]:
            raise ValueError("invalid event fields")
        if type(event["version"]) is not int or event["version"] != 1 or event["run_id"] != run_id:
            raise ValueError("timing version or run ID mismatch")
        if clock not in {"live", "test"} or event["clock"] != clock:
            raise ValueError("live and test clocks cannot be mixed")
        current = timestamp(event["at"])
        if previous is not None and current < previous:
            raise ValueError("timestamps must not go backwards")
        previous = current
        if index == 0 and kind != "init":
            raise ValueError("first event must initialize the run")
        if finished is not None:
            raise ValueError("no events may follow finish")
        if kind == "init":
            if index != 0:
                raise ValueError("duplicate initialization")
            if event["request_at"] is not None and timestamp(event["request_at"]) > current:
                raise ValueError("request timestamp cannot follow initialization")
        elif kind == "start":
            name(event["id"], "span ID")
            name(event["phase"], "phase")
            if event["source"] is not None and event["source"] not in SOURCES:
                raise ValueError("unknown core source")
            if active is not None:
                raise ValueError("end the active span before starting another; overlaps are forbidden")
            if event["id"] in seen:
                raise ValueError("span IDs cannot be reused")
            seen.add(event["id"])
            active = event
        elif kind == "end":
            if active is None or event["id"] != active["id"]:
                raise ValueError("end must match the active span")
            active = None
        else:
            if event["outcome"] not in {"delivered", "aborted"}:
                raise ValueError("finish outcome must be delivered or aborted")
            if active is not None and event["outcome"] == "delivered":
                raise ValueError("cannot mark delivery with an open span")
            finished = event
    return active, finished


def read_events(path):
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        raise ValueError("timing log is truncated or lacks its final newline")
    return [json.loads(line) for line in text.splitlines()]


def safe_path(value):
    path = Path(value).absolute()
    if path.is_symlink():
        raise ValueError("timing path must not be a symlink")
    resolved = path.resolve()
    if resolved == SKILL_ROOT or SKILL_ROOT in resolved.parents:
        raise ValueError("store timing outside the installed skill directory")
    if path.suffix != ".jsonl" or (path.exists() and not path.is_file()):
        raise ValueError("timing path must be a regular .jsonl file")
    if not path.parent.is_dir():
        raise ValueError("create the run directory before initializing timing")
    return path


def append_event(path, event):
    """Fail-fast sibling lock plus atomic replacement; existing data stays intact on errors."""
    lock = path.with_name(path.name + ".lock")
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("timing log is locked; do not retry concurrently or remove an active lock") from exc
    temporary = None
    try:
        os.close(lock_fd)
        if path.is_symlink():
            raise ValueError("timing path must not be a symlink")
        if event["event"] == "init":
            if path.exists():
                raise ValueError("refusing to overwrite an existing timing log")
            events = []
        else:
            events = read_events(path)
        events.append(event)
        validate(events, event["run_id"])
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            for item in events:
                output.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def report(events, run_id, now=None):
    active, finished = validate(events, run_id)
    init = events[0]
    origin = timestamp(init["request_at"] or init["at"])
    boundary = timestamp(finished["at"]) if finished else (now or datetime.now().astimezone())
    if boundary.tzinfo is None or boundary.utcoffset() is None or boundary < timestamp(events[-1]["at"]):
        raise ValueError("report boundary must be timezone-aware and not before the latest event")
    spans, pending = [], None
    for event in events:
        if event["event"] == "start":
            pending = event
        elif event["event"] == "end":
            spans.append({**pending, "end": event["at"], "status": "complete"})
            pending = None
    if active:
        spans.append({**active, "end": boundary.isoformat(),
                      "status": "incomplete" if finished else "open"})
    by_phase, by_source = defaultdict(float), defaultdict(float)
    intervals, gaps, cursor = [], [], origin
    for span in spans:
        start, end = timestamp(span["at"]), timestamp(span["end"])
        if start > cursor:
            gaps.append({"start": cursor.isoformat(), "end": start.isoformat(),
                         "seconds": round((start - cursor).total_seconds(), 6)})
        seconds = (end - start).total_seconds()
        intervals.append({"id": span["id"], "phase": span["phase"], "source": span["source"],
                          "start": span["at"], "end": span["end"], "seconds": round(seconds, 6),
                          "status": span["status"]})
        by_phase[span["phase"]] += seconds
        by_source[span["source"] or "unassigned"] += seconds
        cursor = end
    if boundary > cursor:
        gaps.append({"start": cursor.isoformat(), "end": boundary.isoformat(),
                     "seconds": round((boundary - cursor).total_seconds(), 6)})
    elapsed = (boundary - origin).total_seconds()
    return {"run_id": run_id, "clock": init["clock"],
            "status": finished["outcome"] if finished else "open",
            "origin": "observed_request" if init["request_at"] else "instrumentation_start",
            "start": origin.isoformat(), "end": boundary.isoformat(),
            "elapsed_seconds": round(elapsed, 6),
            "request_to_delivery_seconds": round(elapsed, 6) if init["request_at"] and finished and finished["outcome"] == "delivered" else None,
            "phase_seconds": {key: round(value, 6) for key, value in sorted(by_phase.items())},
            "source_seconds": {key: round(value, 6) for key, value in sorted(by_source.items())},
            "intervals": intervals, "unattributed_intervals": gaps,
            "unattributed_seconds": round(sum(gap["seconds"] for gap in gaps), 6),
            "open_span_ids": [active["id"]] if active and not finished else [],
            "incomplete_span_ids": [active["id"]] if active and finished else [],
            "boundary_note": "Wall-clock attribution only; spans include execution gaps. No network cause is inferred. Phase and source totals are alternate views, not additive."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "start", "end", "finish", "report"):
        sub = commands.add_parser(command)
        sub.add_argument("path")
        sub.add_argument("--run-id", required=True)
        sub.add_argument("--test-at", help="TEST FIXTURES ONLY: aware ISO clock; marks the whole log as test")
        if command == "init":
            sub.add_argument("--request-at", help="Exact observed host request timestamp, with timezone; never estimate")
        elif command == "start":
            sub.add_argument("--id", required=True)
            sub.add_argument("--phase", required=True)
            sub.add_argument("--source", choices=sorted(SOURCES))
        elif command == "end":
            sub.add_argument("--id", required=True)
        elif command == "finish":
            sub.add_argument("--outcome", choices=("delivered", "aborted"), required=True)
    args = parser.parse_args(argv)
    try:
        path = safe_path(args.path)
        instant = timestamp(args.test_at) if args.test_at else datetime.now().astimezone()
        if args.command == "report":
            events = read_events(path)
            validate(events, args.run_id)
            if events[0]["clock"] != ("test" if args.test_at else "live"):
                raise ValueError("test logs require --test-at; live logs prohibit it")
            result = report(events, args.run_id, instant)
        else:
            event = {"version": 1, "run_id": args.run_id, "event": args.command,
                     "at": instant.isoformat(), "clock": "test" if args.test_at else "live"}
            if args.command == "init":
                event["request_at"] = args.request_at
            elif args.command == "start":
                event.update(id=args.id, phase=args.phase, source=args.source)
            elif args.command == "end":
                event["id"] = args.id
            else:
                event["outcome"] = args.outcome
            append_event(path, event)
            result = {"recorded": event["event"], "run_id": args.run_id, "at": event["at"]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
