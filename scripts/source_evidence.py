"""Replay compact, run-bound source observations; never trust claimed completion.

No networking. All rows and observation times must originate in the selected
browser's actual current-run observations. Structural replay proves consistency,
not that the observer performed truthful browser actions.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def raw_rows_sha256(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("runtime evidence requires raw capture rows")
    pairs = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("title"), str) or not row["title"].strip():
            raise ValueError("raw row requires the exact nonempty title")
        if "url" not in row or (row["url"] is not None and not isinstance(row["url"], str)):
            raise ValueError("raw row requires the exact observed href or explicit null")
        pairs.append([row["title"], row["url"]])
    return hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError("runtime timestamps require timezone-aware ISO strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid runtime timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("runtime timestamp requires timezone")
    return parsed


@lru_cache(maxsize=128)
def _replay(payload, node, verifier_digest):
    executable = node or os.environ.get("SHF_NODE") or shutil.which("node")
    if not executable:
        raise ValueError("Node runtime unavailable: provide --node /supported/node or SHF_NODE; never skip source replay")
    cli = Path(__file__).with_name("browser_collection_cli.mjs")
    try:
        result = subprocess.run([executable, str(cli), "replay", "-"], input=payload,
                                text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"source replay could not run: {exc}") from exc
    if result.returncode:
        raise ValueError("source runtime replay failed: " + result.stderr.strip())
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("source replay returned invalid JSON") from exc


def verify_runtime_evidence(evidence, source, run_id, raw_rows, run_date=None, node=None):
    """Return derived collection fields or fail; raw_rows ordered by display_order.

    Returned discovered_count plus all non-count fields must match collection.
    eligible_count/excluded_count remain derived from the corrected ledger.
    """
    if not isinstance(evidence, dict) or type(evidence.get("evidence_schema")) is not int or evidence["evidence_schema"] != 1:
        raise ValueError("required source runtime evidence missing or unsupported")
    if not isinstance(run_id, str) or not run_id or evidence.get("run_id") != run_id or evidence.get("source") != source:
        raise ValueError("runtime evidence belongs to a different source or run")
    if evidence.get("raw_rows_sha256") != raw_rows_sha256(raw_rows):
        raise ValueError("runtime evidence raw-row fingerprint mismatch")
    trace = evidence.get("trace")
    if not isinstance(trace, dict) or trace.get("run_id") != run_id or trace.get("source") != source:
        raise ValueError("runtime trace belongs to a different source or run")
    start, captured = _time(evidence.get("capture_started_at")), _time(trace.get("captured_at"))
    if captured < start:
        raise ValueError("stale runtime trace predates capture initialization")
    observed_date = captured.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    if run_date is not None and observed_date != run_date:
        raise ValueError("runtime trace capture date does not match run.date in Asia/Shanghai")
    run_date = run_date or observed_date
    observations = trace.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("runtime trace observations missing")
    # The observation clock starts when this source collection begins. Its final
    # offset cannot exceed the entire run's actual elapsed capture interval.
    final_ms = observations[-1].get("timestampMs") if isinstance(observations[-1], dict) else None
    if isinstance(final_ms, bool) or not isinstance(final_ms, (int, float)) or final_ms < 0:
        raise ValueError("invalid final observation timestamp")
    if final_ms > (captured - start).total_seconds() * 1000 + 1000:
        raise ValueError("trace observation duration exceeds actual run capture interval")
    payload = json.dumps({"trace": trace, "rows": [{"title": row["title"], "url": row["url"]} for row in raw_rows],
                          "run_date": run_date}, ensure_ascii=False, separators=(",", ":"))
    # Return a copy; callers cannot corrupt a cached verified summary.
    scripts = Path(__file__).parent
    verifier_digest = hashlib.sha256((scripts / "browser_collection.mjs").read_bytes()
                                     + (scripts / "browser_collection_cli.mjs").read_bytes()).hexdigest()
    return dict(_replay(payload, node, verifier_digest))


def make_runtime_evidence(trace, source, run_id, raw_rows, capture_started_at, *, run_date=None, node=None):
    evidence = {"evidence_schema": 1, "source": source, "run_id": run_id,
                "capture_started_at": capture_started_at, "raw_rows_sha256": raw_rows_sha256(raw_rows), "trace": trace}
    summary = verify_runtime_evidence(evidence, source, run_id, raw_rows, run_date, node)
    return evidence, summary
