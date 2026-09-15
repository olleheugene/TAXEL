"""
Sequence execution - run one pass of a recipe and record the trace.

The sequence loop used to live inside gui_app.run_all_cards, mixed in with UI
code. That left the CLI with no notion of a sequence and nowhere to attach trace
recording.

Two kinds of repetition are kept distinct:
  step repeat (RecipeStep.repeat)   - measure the same item several times in
                                      place, e.g. to check spread. The serial
                                      session and the DUT state are preserved.
  sequence repeat (run_sequence_batch) - test the DUT again from the beginning,
                                      e.g. burn-in. Each pass gets its own
                                      trace record.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional

from core.language import language
from core.recipe import Recipe, RecipeStep
from core.registry import get_module
from core.runner import instrument_scope, run_module
from core.trace import RunRecord, StepRecord, TraceLog, new_run_id

# Progress callbacks a frontend can subscribe to.
# (step, step_index, iteration, iterations_total)
StepStarted = Optional[Callable[[RecipeStep, int, int, int], None]]
# (step, step_index, iteration, result)
StepFinished = Optional[Callable[[RecipeStep, int, int, Dict[str, Any]], None]]
LogEmitted = Optional[Callable[[RecipeStep, str], None]]


@dataclass
class SequenceContext:
    """Context attached to a run. Goes into the trace record verbatim."""

    dut_serial: str = ""
    mode: str = ""
    station: str = ""
    operator: str = ""
    use_mock: bool = False       # simulate; off by default, see core.runner.run_module
    timestamp: str = ""          # start time, supplied by the caller
    finished_timestamp: str = "" # finish time
    run_index: int = 1
    runs_total: int = 1


@dataclass
class SequenceResult:
    record: RunRecord
    step_results: List[Dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def result(self) -> str:
        return self.record.result

    @property
    def passed(self) -> bool:
        return self.record.result == "PASS"


def count_step_executions(recipe: Recipe) -> int:
    """
    How many step executions one pass performs - the denominator for the
    progress bar. A step repeating 3 times counts as 3.
    """
    return sum(s.effective_repeat for s in recipe.steps if s.enabled)


def run_sequence(
    recipe: Recipe,
    modules: Dict[str, Any],
    context: SequenceContext,
    trace_log: Optional[TraceLog] = None,
    on_step_started: StepStarted = None,
    on_step_finished: StepFinished = None,
    on_log: LogEmitted = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    pump_events: Optional[Callable[[], None]] = None,
    stop_on_fail: bool = False,
    now: Optional[Callable[[], str]] = None,
) -> SequenceResult:
    """
    Run one pass of the recipe in order.

    :param pump_events:  callback a GUI can use to stay responsive (processEvents)
    :param stop_on_fail: abort on the first failure. Common in production.
    :param now:          callback yielding the finish timestamp; must be filled
                         in before the record is written
    """
    record = RunRecord(
        run_id=new_run_id(),
        started_at=context.timestamp,
        run_index=context.run_index,
        runs_total=context.runs_total,
        dut_serial=context.dut_serial,
        recipe_name=recipe.name,
        recipe_version=recipe.version,
        recipe_fingerprint=recipe.fingerprint(),
        mode=context.mode,
        station=context.station,
        operator=context.operator,
        mock=context.use_mock,
    )

    started_monotonic = time.time()
    step_results: List[Dict[str, Any]] = []
    cancelled = False
    sessions_seen: List[Any] = []
    aborted = False

    # The whole sequence is one instrument scope, covering the serial session
    # and the PPK2 alike. Ports are not opened and closed per card, so
    # re-asserting DTR/RTS never resets the DUT and a later step inherits the
    # state an earlier one established - and when the sequence ends everything
    # is released, which for the PPK2 also powers the DUT down.
    with instrument_scope():
        _register_session_owner(recipe, modules)

        for idx, step in enumerate(recipe.steps, 1):
            if aborted:
                break
            if pump_events:
                pump_events()
            if is_cancelled and is_cancelled():
                cancelled = True
                break
            if not step.enabled:
                continue

            # A stored id may be a former name, so resolve aliases too.
            module = get_module(modules, step.module_id)
            if module is None:
                # Recipe validation catches this, but do not let it pass
                # silently at runtime either.
                record.steps.append(
                    StepRecord(
                        step_id=step.step_id,
                        module_id=step.module_id,
                        result="FAIL",
                        summary_text=f"FAIL (module not installed: {step.module_id})",
                        error="module_not_found",
                    )
                )
                if stop_on_fail:
                    break
                continue

            repeats = step.effective_repeat

            for iteration in range(1, repeats + 1):
                if pump_events:
                    pump_events()
                if is_cancelled and is_cancelled():
                    cancelled = True
                    aborted = True
                    break

                if on_step_started:
                    on_step_started(step, idx, iteration, repeats)

                outcome = run_module(
                    module,
                    step.criteria,
                    use_mock=context.use_mock,
                    log_callback=(lambda msg, s=step: on_log(s, msg)) if on_log else None,
                    is_cancelled=is_cancelled,
                    timeout_sec=step.timeout_sec,
                )

                result = outcome.result or {}
                details = result.get("details", {}) or {}
                record.steps.append(
                    StepRecord(
                        step_id=step.step_id,
                        module_id=step.module_id,
                        result=str(result.get("result", "FAIL")),
                        iteration=iteration,
                        iterations_total=repeats,
                        execution_time_sec=float(result.get("execution_time_sec", 0.0) or 0.0),
                        summary_text=str(result.get("summary_text", "")),
                        criteria=step.public_criteria(),
                        metrics=dict(details.get("metrics", {}) or {}),
                        logs=list(details.get("logs", []) or []),
                        error=(
                            "timeout" if outcome.timed_out
                            else (type(outcome.error).__name__ if outcome.error else "")
                        ),
                    )
                )
                step_results.append(result)

                # Propagate DUT serial if acquired/validated by a step (e.g. dut_serialnumber_reader)
                extracted_serial = result.get("dut_serial") or (details.get("metrics", {}) or {}).get("dut_serial")
                if extracted_serial:
                    record.dut_serial = str(extracted_serial)
                    if hasattr(context, "dut_serial"):
                        context.dut_serial = str(extracted_serial)

                if outcome.session is not None and outcome.session not in sessions_seen:
                    sessions_seen.append(outcome.session)

                if on_step_finished:
                    on_step_finished(step, idx, iteration, result)

                if pump_events:
                    pump_events()

                if stop_on_fail and str(result.get("result")) != "PASS":
                    aborted = True
                    break

        # Collect the full traffic before the session closes.
        for session in sessions_seen:
            record.serial_traffic.extend(session.traffic)

    record.cancelled = cancelled or bool(is_cancelled and is_cancelled())
    # Fix the finish timestamp before the record is written.
    record.finished_at = (now() if now else "") or context.finished_timestamp
    record.duration_sec = time.time() - started_monotonic
    record.result = record.compute_result()

    if trace_log is not None:
        try:
            trace_log.append(record)
        except Exception as e:
            # Silently swallowing a write failure makes traceability worthless.
            print(language.tr("log_trace_save_failed", error=e))

    return SequenceResult(record=record, step_results=step_results, cancelled=record.cancelled)


def run_sequence_batch(
    recipe: Recipe,
    modules: Dict[str, Any],
    context: SequenceContext,
    repeat: int = 1,
    now: Optional[Callable[[], str]] = None,
    trace_log: Optional[TraceLog] = None,
    on_run_started: Optional[Callable[[int, int], None]] = None,
    on_run_finished: Optional[Callable[[SequenceResult, int, int], None]] = None,
    stop_batch_on_fail: bool = False,
    **kwargs,
) -> List[SequenceResult]:
    """
    Run the sequence several times (a repeated test).

    Each pass produces its own trace record. Since a pass tests the DUT again
    from the beginning, each one must stand as an independent verdict, and yield
    statistics are therefore counted per pass.

    :param repeat: number of passes. Zero or less repeats until cancelled (burn-in).
    :param stop_batch_on_fail: stop repeating once a pass fails.
    """
    is_cancelled = kwargs.get("is_cancelled")
    stamp = now or (lambda: context.timestamp)
    infinite = repeat <= 0
    total = 0 if infinite else repeat

    results: List[SequenceResult] = []
    index = 0

    while True:
        if is_cancelled and is_cancelled():
            break
        if not infinite and index >= repeat:
            break

        index += 1
        if on_run_started:
            on_run_started(index, total)

        iteration_context = replace(
            context,
            timestamp=stamp(),
            run_index=index,
            runs_total=total,
        )
        result = run_sequence(
            recipe, modules, iteration_context, trace_log=trace_log, now=now, **kwargs
        )
        results.append(result)

        if on_run_finished:
            on_run_finished(result, index, total)

        if result.cancelled:
            break
        if stop_batch_on_fail and not result.passed:
            break

    return results


def _register_session_owner(recipe: Recipe, modules: Dict[str, Any]) -> None:
    """
    Register the devices chosen by the config cards as the sequence defaults.

    One per shared instrument, so this walks the steps once and registers each
    kind it finds: the serial session owner and the PPK2 session owner are
    different cards, and a sequence measuring current over DTM has both.
    """
    from core.runner import register_ppk_target, register_serial_target

    registrars = (
        ("needs_serial", register_serial_target),
        ("needs_ppk", register_ppk_target),
    )
    done = set()
    for step in recipe.steps:
        module = get_module(modules, step.module_id)
        if module is None or module.module_type != "config":
            continue
        for capability, register in registrars:
            if capability in done or not getattr(module, capability, False):
                continue
            register(step.criteria)
            done.add(capability)
        if len(done) == len(registrars):
            return
