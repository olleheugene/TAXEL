"""
Machine-readable run reports for CI.

Two formats, both built from the same List[SequenceResult] the GUI gets:

  JSON  - the full record: every step, metric, log line and criteria value.
          Meant to be archived as a build artifact or parsed by a script.
  JUnit - the XML dialect GitHub Actions, GitLab CI and Jenkins already know how
          to display. One <testsuite> per sequence pass, one <testcase> per step
          execution, so a failing step shows up in the CI run summary by name.

Keys are deliberately English and fixed. Display text is translated, but a report
a script parses must not change shape when NRF_LANG changes.

Qt-free on purpose: the CLI is the primary consumer, but nothing here stops the
GUI from writing the same files.
"""

import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# Exit codes. Kept here so the CLI and any wrapper script agree on them.
EXIT_OK = 0
EXIT_TEST_FAILED = 1
EXIT_USAGE = 2


def _step_dicts(result) -> List[Dict[str, Any]]:
    """Step records of one pass, as plain dicts."""
    return [step.to_dict() for step in result.record.steps]


def summarize(results: List[Any]) -> Dict[str, Any]:
    """
    Counts over every pass.

    Steps are counted per execution, not per definition: a step with repeat=3
    contributes three entries, which is what a CI dashboard should show.
    """
    runs_passed = sum(1 for r in results if r.passed and not r.cancelled)
    steps = [s for r in results for s in r.record.steps]
    steps_passed = sum(1 for s in steps if s.result == "PASS")
    return {
        "runs_total": len(results),
        "runs_passed": runs_passed,
        "runs_failed": len(results) - runs_passed,
        "steps_total": len(steps),
        "steps_passed": steps_passed,
        "steps_failed": len(steps) - steps_passed,
        "duration_sec": round(sum(r.record.duration_sec or 0.0 for r in results), 3),
        "cancelled": any(r.cancelled for r in results),
    }


def exit_code(results: List[Any]) -> int:
    """EXIT_OK only when every pass passed and none was cancelled."""
    if not results:
        return EXIT_USAGE
    summary = summarize(results)
    if summary["cancelled"] or summary["runs_failed"]:
        return EXIT_TEST_FAILED
    return EXIT_OK


def build_json(
    results: List[Any],
    recipe: Optional[Any] = None,
    mock: bool = False,
    source: str = "",
) -> Dict[str, Any]:
    """
    The full report as a dict.

    :param source: where the recipe came from - a file path, or "--run"/"--all"
                   for a sequence assembled on the command line.
    """
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mock": mock,
        "source": source,
        "summary": summarize(results),
        "exit_code": exit_code(results),
        "runs": [],
    }
    if recipe is not None:
        payload["recipe"] = {
            "name": recipe.name,
            "version": recipe.version,
            "fingerprint": recipe.fingerprint(),
            "locked": bool(recipe.locked),
        }
    for result in results:
        run = result.record.to_dict(include_traffic=False)
        run["cancelled"] = result.cancelled
        payload["runs"].append(run)
    return payload


def write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write the report. '-' means stdout."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path == "-":
        print(text)
        return
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")


def build_junit(
    results: List[Any],
    suite_name: str = "automate_test",
    mock: bool = False,
) -> ET.ElementTree:
    """
    JUnit XML: one testsuite per pass, one testcase per step execution.

    A repeated step would collide on name, so the iteration is appended once it
    actually repeats - otherwise CI shows a single case and hides the rest.
    """
    summary = summarize(results)
    root = ET.Element("testsuites", {
        "name": suite_name,
        "tests": str(summary["steps_total"]),
        "failures": str(summary["steps_failed"]),
        "time": f"{summary['duration_sec']:.3f}",
    })

    for result in results:
        record = result.record
        steps = _step_dicts(result)
        failures = sum(1 for s in steps if s["result"] != "PASS")
        suite = ET.SubElement(root, "testsuite", {
            "name": f"{suite_name} run {record.run_index}/{record.runs_total or 1}",
            "tests": str(len(steps)),
            "failures": str(failures),
            "time": f"{record.duration_sec or 0.0:.3f}",
            "timestamp": record.started_at or "",
        })
        # Traceability that a CI log alone would not carry.
        props = ET.SubElement(suite, "properties")
        for key, value in (
            ("dut_serial", record.dut_serial),
            ("recipe_name", record.recipe_name),
            ("recipe_version", record.recipe_version),
            ("recipe_fingerprint", record.recipe_fingerprint),
            ("station", record.station),
            ("operator", record.operator),
            ("mock", str(mock).lower()),
        ):
            if value:
                ET.SubElement(props, "property", {"name": key, "value": str(value)})

        for step in steps:
            name = step.get("module_id", "?")
            iterations = step.get("iterations_total") or 1
            if iterations > 1:
                name = f"{name} [{step.get('iteration', 1)}/{iterations}]"
            case = ET.SubElement(suite, "testcase", {
                "name": name,
                "classname": f"{suite_name}.{step.get('module_id', '?')}",
                "time": f"{step.get('execution_time_sec') or 0.0:.3f}",
            })
            if step["result"] != "PASS":
                failure = ET.SubElement(case, "failure", {
                    "message": step.get("summary_text") or step["result"],
                    "type": step.get("error") and "Error" or "TestFailure",
                })
                failure.text = _failure_body(step)
            metrics = step.get("metrics") or {}
            if metrics:
                out = ET.SubElement(case, "system-out")
                out.text = "\n".join(f"{k}: {v}" for k, v in metrics.items())

        if result.cancelled:
            ET.SubElement(suite, "testcase", {
                "name": "sequence-cancelled",
                "classname": suite_name,
                "time": "0",
            }).append(ET.Element("failure", {
                "message": "sequence was cancelled before finishing",
                "type": "Cancelled",
            }))

    _indent(root)
    return ET.ElementTree(root)


def _failure_body(step: Dict[str, Any]) -> str:
    """Everything a maintainer needs to see straight from the CI failure panel."""
    parts = [f"result: {step['result']}", f"summary: {step.get('summary_text', '')}"]
    if step.get("error"):
        parts.append(f"error: {step['error']}")
    logs = step.get("logs") or []
    if logs:
        parts.append("logs:")
        parts.extend(f"  {line}" for line in logs)
    return "\n".join(parts)


def write_junit(path: str, tree: ET.ElementTree) -> None:
    _ensure_parent(path)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _indent(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print in place. ET.indent() only exists from 3.9, so do it here."""
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad
