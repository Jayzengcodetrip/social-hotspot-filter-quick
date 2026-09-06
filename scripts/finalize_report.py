#!/usr/bin/env python3
"""Build and check one reviewed quick run, preserving every attempt separately.

Default inputs under --run-dir: run.json, captures/, groups.json (required, may be []),
review.json, corrections.json (optional), timing.jsonl (if initialized).
Success means a validated local artifact is ready, never that chat delivery happened.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import uuid

from build_capture_ledger import build_ledger, load_captures
from _core_link_rules import validate_selected_core_links
from generate_report import build_ranking_payload, generate_report
from validate_ranking import canonical_sha256, load_ledger
from validate_report import validate_report
import run_timing


SKILL_ROOT = Path(__file__).resolve().parent.parent


def now() -> str:
    return datetime.now().astimezone().isoformat()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    """Replace a small derived status file only after its complete bytes exist."""
    if path.is_symlink():
        raise ValueError(f"status path must not be a symlink: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name + ".", delete=False) as out:
            temporary = Path(out.name)
            out.write(json_bytes(value))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


class GateFailure(ValueError):
    def __init__(self, stage: str, errors: list[str]):
        self.stage, self.errors = stage, errors
        super().__init__("; ".join(errors))


@contextmanager
def run_lock(path: Path):
    """Use an OS lock released on process exit; a leftover filename is harmless."""
    if path.is_symlink():
        raise ValueError("finalization lock must not be a symlink")
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("another finalization holds this run's lock; wait for that active attempt") from exc
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("another finalization holds this run's lock; wait for that active attempt") from exc
            unlock = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            yield
        finally:
            unlock()


class LocalTiming:
    def __init__(self, path: Path | None, run_id: str, attempt_id: str):
        self.path, self.run_id, self.attempt_id = path, run_id, attempt_id
        self.owned: str | None = None
        if path is not None:
            self.path = run_timing.safe_path(str(path))
            events = run_timing.read_events(self.path)
            active, finished = run_timing.validate(events, run_id)
            if events[0]["clock"] != "live":
                raise ValueError("finalization requires a live timing log; omit fixture timing from this run")
            if active:
                raise ValueError(f"timing span {active['id']!r} is still active; end it at its actual boundary before finalization")
            if finished:
                raise ValueError("timing log is already finished; use this run's open timing log or start a new run")

    def event(self, kind: str, span_id: str, phase: str | None = None) -> None:
        event = {"version": 1, "run_id": self.run_id, "event": kind,
                 "at": now(), "clock": "live", "id": span_id}
        if kind == "start":
            event.update(phase=phase, source=None)
        run_timing.append_event(self.path, event)

    @contextmanager
    def span(self, phase: str):
        if self.path is None:
            yield
            return
        span_id = f"finalize-{self.attempt_id}-{phase}"
        self.event("start", span_id, phase)
        self.owned = span_id
        try:
            yield
        finally:
            # Close only the span this invocation opened. Never finish the run.
            self.event("end", span_id)
            self.owned = None


def finalize(
    run_dir: Path, *, limit: int = 10, run_path: Path | None = None,
    captures_path: Path | None = None, groups_path: Path | None = None,
    review_path: Path | None = None, corrections_path: Path | None = None,
    timing_path: Path | None = None,
) -> dict:
    """Return this attempt's actual result; a previous success is never returned."""
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")
    if any((ancestor / "SKILL.md").is_file() for ancestor in (run_dir, *run_dir.parents)):
        raise ValueError("keep run inputs and outputs outside the installed Skill")
    attempts = run_dir / "attempts"
    if attempts.is_symlink():
        raise ValueError("attempts directory must not be a symlink")
    attempts.mkdir(exist_ok=True)
    lock = run_lock(run_dir / ".finalize.lock")
    lock.__enter__()
    attempt_id = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    attempt = attempts / attempt_id
    result = {"version": 1, "attempt_id": attempt_id, "status": "running", "limit": limit,
              "started_at": now(), "run_id": None, "steps": [], "errors": [],
              "attempt_dir": str(attempt), "delivery_status": "not_recorded"}
    stage = "inputs"

    def step(name: str, operation):
        nonlocal stage
        stage = name
        record = {"stage": name, "status": "running", "started_at": now()}
        result["steps"].append(record)
        try:
            value = operation()
        except Exception as exc:
            record.update(status="failed", ended_at=now(), error=str(exc))
            raise
        record.update(status="passed", ended_at=now())
        return value

    def require(name: str, errors: list[str]) -> None:
        if errors:
            raise GateFailure(name, errors)

    try:
        attempt.mkdir()
        atomic_json(run_dir / "latest-attempt.json", result)
        run_input = run_path or run_dir / "run.json"
        capture_input = captures_path or run_dir / "captures"
        group_input = groups_path or run_dir / "groups.json"
        review_input = review_path or run_dir / "review.json"
        correction_input = corrections_path or run_dir / "corrections.json"
        timer_input = timing_path or run_dir / "timing.jsonl"
        if timing_path is not None and not timing_path.is_file():
            raise ValueError(f"requested timing log does not exist: {timing_path}")
        run = read_json(run_input)
        if not isinstance(run, dict):
            raise ValueError("run.json must contain an object")
        result["run_id"] = run.get("run_id")
        timer = LocalTiming(timer_input if timer_input.exists() else None, run.get("run_id"), attempt_id)
        result["timing"] = "integrated" if timer.path else "not_initialized"
        groups = read_json(group_input) if groups_path is not None or group_input.exists() else None
        review = read_json(review_input)
        corrections = read_json(correction_input) if corrections_path is not None or correction_input.exists() else None
        with timer.span("persist"):
            ledger = step("build_ledger", lambda: build_ledger(
                load_captures(capture_input), run, groups, review=review, corrections=corrections,
            ))
            ledger_path = attempt / "ledger.json"
            atomic_json(ledger_path, ledger)
            result["ledger_sha256"] = canonical_sha256(ledger)
            result["ledger_file_sha256"] = digest(ledger_path)
        with timer.span("validate"):
            def ranking_stage():
                errors, ranking, selected = build_ranking_payload(load_ledger(ledger_path), limit)
                require("ranking", errors)
                atomic_json(attempt / "ranking.json", ranking)
                result["ranking_file_sha256"] = digest(attempt / "ranking.json")
                return ranking, selected
            ranking, selected = step("ranking", ranking_stage)
            step("selected_links", lambda: require("selected_links", validate_selected_core_links(selected)))
            pending_report = attempt / "report.pending.md"
            def generate_stage():
                report, errors = generate_report(load_ledger(ledger_path), read_json(attempt / "ranking.json"))
                require("generate_report", errors)
                pending_report.write_text(report, encoding="utf-8")
            step("generate_report", generate_stage)
            def final_stage():
                # Independent final check reads the actual persisted bytes.
                require("final_validation", validate_report(load_ledger(ledger_path), pending_report.read_text(encoding="utf-8"), limit))
                if digest(ledger_path) != result["ledger_file_sha256"]:
                    raise ValueError("ledger changed during finalization; rerun from the reviewed inputs")
                if digest(attempt / "ranking.json") != result["ranking_file_sha256"]:
                    raise ValueError("ranking artifact changed during finalization; rerun from the reviewed inputs")
            step("final_validation", final_stage)
            report_path = attempt / "report.md"
            pending_report.rename(report_path)
            result.update(report=str(report_path), ranking=str(attempt / "ranking.json"),
                          ledger=str(ledger_path), report_sha256=digest(report_path), selected_count=len(selected))
        result.update(status="passed", ended_at=now())
        atomic_json(attempt / "validation-results.json", result)
        atomic_json(run_dir / "latest-attempt.json", result)
        atomic_json(run_dir / "latest-success.json", result)
    except Exception as exc:
        result.update(status="failed", ended_at=now(), failed_stage=getattr(exc, "stage", stage),
                      errors=getattr(exc, "errors", [str(exc)]))
        # Report paths are success outputs, never suggestions to publish after failure.
        for key in ("report", "ranking", "ledger", "report_sha256", "selected_count"):
            result.pop(key, None)
        if attempt.is_dir():
            atomic_json(attempt / "validation-results.json", result)
            atomic_json(run_dir / "latest-attempt.json", result)
    finally:
        lock.__exit__(None, None, None)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    for flag in ("run", "captures", "groups", "review", "corrections", "timing"):
        parser.add_argument("--" + flag, type=Path, help="override the conventional input path")
    args = parser.parse_args(argv)
    try:
        result = finalize(args.run_dir, limit=args.limit, run_path=args.run, captures_path=args.captures,
                          groups_path=args.groups, review_path=args.review, corrections_path=args.corrections,
                          timing_path=args.timing)
    except (OSError, ValueError) as exc:
        print(f"FAIL finalization: {exc}", file=sys.stderr)
        return 1
    if result["status"] != "passed":
        print(f"FAIL finalization at {result['failed_stage']}", file=sys.stderr)
        for error in result["errors"][:12]:
            print(f"- {error}", file=sys.stderr)
        if len(result["errors"]) > 12:
            print(f"- {len(result['errors']) - 12} additional errors in validation-results.json", file=sys.stderr)
        print(f"This attempt's results: {result['attempt_dir']}/validation-results.json", file=sys.stderr)
        return 1
    print(f"PASS quick report ready: {result['selected_count']} selected; limit={result['limit']}")
    print(f"Report: {result['report']}")
    print(f"Validation: {result['attempt_dir']}/validation-results.json")
    print("Delivery has not been recorded by this command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
