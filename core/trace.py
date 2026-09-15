"""
Traceability records - an append-only run log and yield statistics.

In production software the claim "this board passed" has to be verifiable, so
every run records:

  DUT serial / timestamps / recipe name, version and fingerprint / mode / station
  per-step verdicts, measurements and durations / the full serial traffic

Written in two forms:
  runs.jsonl  the source of truth. One run per line. Append-only - never edited.
  runs.csv    a summary for MES or spreadsheet aggregation. One run per line.

JSONL is the source of truth because if the program dies mid-write, every
preceding line is still intact.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import csv
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional

from core.language import language

TRACE_SCHEMA_VERSION = 1

CSV_COLUMNS = [
    "run_id",
    "run_index",
    "runs_total",
    "started_at",
    "finished_at",
    "duration_sec",
    "result",
    "dut_serial",
    "recipe_name",
    "recipe_version",
    "recipe_fingerprint",
    "mode",
    "station",
    "operator",
    "mock",
    "steps_total",
    "steps_passed",
    "steps_failed",
    "first_failure",
]


def new_run_id() -> str:
    return uuid.uuid4().hex


@dataclass
class StepRecord:
    """The record of one step execution."""

    step_id: str
    module_id: str
    result: str
    # Step repeat iteration. A single execution is (1, 1).
    iteration: int = 1
    iterations_total: int = 1
    execution_time_sec: float = 0.0
    summary_text: str = ""
    criteria: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "module_id": self.module_id,
            "result": self.result,
            "iteration": self.iteration,
            "iterations_total": self.iterations_total,
            "execution_time_sec": round(float(self.execution_time_sec or 0.0), 3),
            "summary_text": self.summary_text,
            "criteria": self.criteria,
            "metrics": self.metrics,
            "logs": self.logs,
            "error": self.error,
        }


@dataclass
class RunRecord:
    """The record of one whole sequence run."""

    run_id: str
    started_at: str
    # Sequence repeat index: which pass this is in a repeated test.
    run_index: int = 1
    runs_total: int = 1
    finished_at: str = ""
    duration_sec: float = 0.0
    result: str = "INCOMPLETE"
    dut_serial: str = ""
    recipe_name: str = ""
    recipe_version: str = ""
    recipe_fingerprint: str = ""
    mode: str = ""
    station: str = ""
    operator: str = ""
    mock: bool = True
    steps: List[StepRecord] = field(default_factory=list)
    serial_traffic: List[str] = field(default_factory=list)
    cancelled: bool = False
    schema_version: int = TRACE_SCHEMA_VERSION

    # ---------------------------------------------------------------- summary
    @property
    def steps_total(self) -> int:
        return len(self.steps)

    @property
    def steps_passed(self) -> int:
        return sum(1 for s in self.steps if s.result == "PASS")

    @property
    def steps_failed(self) -> int:
        return sum(1 for s in self.steps if s.result != "PASS")

    @property
    def first_failure(self) -> str:
        for s in self.steps:
            if s.result != "PASS":
                return f"{s.module_id}: {s.summary_text}"[:200]
        return ""

    def compute_result(self) -> str:
        """One failed step fails the whole sequence."""
        if self.cancelled:
            return "CANCELLED"
        if not self.steps:
            return "INCOMPLETE"
        return "PASS" if self.steps_failed == 0 else "FAIL"

    # ------------------------------------------------------------ serialisation
    def to_dict(self, include_traffic: bool = True) -> Dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "run_index": self.run_index,
            "runs_total": self.runs_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(float(self.duration_sec or 0.0), 3),
            "result": self.result,
            "dut": {"serial": self.dut_serial},
            "recipe": {
                "name": self.recipe_name,
                "version": self.recipe_version,
                "fingerprint": self.recipe_fingerprint,
            },
            "context": {
                "mode": self.mode,
                "station": self.station,
                "operator": self.operator,
                "mock": self.mock,
            },
            "summary": {
                "steps_total": self.steps_total,
                "steps_passed": self.steps_passed,
                "steps_failed": self.steps_failed,
                "first_failure": self.first_failure,
            },
            "steps": [s.to_dict() for s in self.steps],
            "cancelled": self.cancelled,
        }
        if include_traffic:
            data["serial_traffic"] = self.serial_traffic
        return data

    def to_csv_row(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_index": self.run_index,
            "runs_total": self.runs_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(float(self.duration_sec or 0.0), 3),
            "result": self.result,
            "dut_serial": self.dut_serial,
            "recipe_name": self.recipe_name,
            "recipe_version": self.recipe_version,
            "recipe_fingerprint": self.recipe_fingerprint,
            "mode": self.mode,
            "station": self.station,
            "operator": self.operator,
            "mock": self.mock,
            "steps_total": self.steps_total,
            "steps_passed": self.steps_passed,
            "steps_failed": self.steps_failed,
            "first_failure": self.first_failure,
        }


class TraceLog:
    """
    Append-only record store.

    Existing lines are never modified. Correcting a record by appending a new
    one, rather than editing or deleting the old one, is the basic rule of
    traceability.
    """

    def __init__(self, directory: str, jsonl_name: str = "runs.jsonl", csv_name: str = "runs.csv"):
        self.directory = directory
        self.jsonl_path = os.path.join(directory, jsonl_name)
        self.csv_path = os.path.join(directory, csv_name)

    def _ensure_dir(self) -> None:
        os.makedirs(self.directory, exist_ok=True)

    def append(self, record: RunRecord, include_traffic: bool = True) -> None:
        """Record one run. Failures are raised so the caller finds out."""
        self._ensure_dir()

        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(include_traffic=include_traffic), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())  # the last record must survive a power cut

        write_header = not os.path.exists(self.csv_path) or os.path.getsize(self.csv_path) == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(record.to_csv_row())
            f.flush()

    def iter_runs(self) -> Iterator[Dict[str, Any]]:
        """Read records in order. Corrupt lines are skipped, but reported."""
        if not os.path.exists(self.jsonl_path):
            return
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(language.tr("log_trace_corrupt", location=f"{self.jsonl_path}:{lineno}"))

    def stats(self, recipe_fingerprint: str = "", since: str = "") -> "YieldStats":
        return YieldStats.from_runs(
            self.iter_runs(), recipe_fingerprint=recipe_fingerprint, since=since
        )


@dataclass
class YieldStats:
    """Yield statistics, shown on the operator screen."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    cancelled: int = 0
    # Failures per module - shows which item is holding the line up.
    failures_by_module: Dict[str, int] = field(default_factory=dict)

    @property
    def yield_pct(self) -> float:
        judged = self.passed + self.failed
        return (self.passed / judged * 100.0) if judged else 0.0

    @property
    def top_failure(self) -> str:
        if not self.failures_by_module:
            return ""
        module_id, count = max(self.failures_by_module.items(), key=lambda kv: kv[1])
        return f"{module_id} ({count})"

    def register(self, record: RunRecord) -> None:
        """Update the in-memory tally right after a run, so the log does not
        have to be re-read."""
        self.total += 1
        if record.cancelled:
            self.cancelled += 1
        elif record.result == "PASS":
            self.passed += 1
        else:
            self.failed += 1
            for step in record.steps:
                if step.result != "PASS":
                    self.failures_by_module[step.module_id] = (
                        self.failures_by_module.get(step.module_id, 0) + 1
                    )

    @classmethod
    def from_runs(
        cls,
        runs: Iterable[Dict[str, Any]],
        recipe_fingerprint: str = "",
        since: str = "",
    ) -> "YieldStats":
        stats = cls()
        for run in runs:
            if recipe_fingerprint and (run.get("recipe", {}) or {}).get("fingerprint") != recipe_fingerprint:
                continue
            if since and str(run.get("started_at", "")) < since:
                continue

            stats.total += 1
            if run.get("cancelled"):
                stats.cancelled += 1
                continue
            if run.get("result") == "PASS":
                stats.passed += 1
                continue

            stats.failed += 1
            for step in run.get("steps", []) or []:
                if step.get("result") != "PASS":
                    mid = str(step.get("module_id", "?"))
                    stats.failures_by_module[mid] = stats.failures_by_module.get(mid, 0) + 1
        return stats
