"""
QThread adapter that runs a sequence in the background.

run_all_cards used to start one card, then busy-wait on
QApplication.processEvents() plus time.sleep(0.02) before moving to the next.
That left the UI thread holding the sequence, so the screen was sluggish while
running and headless verification was impossible.

Now core.sequence runs on a worker thread and only progress travels back as
signals. The execution rules live in the core; this file just relays them.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import datetime
from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal

from core.recipe import Recipe, RecipeStep
from core.sequence import (
    SequenceContext,
    SequenceResult,
    count_step_executions,
    run_sequence_batch,
)
from core.trace import TraceLog


class SequenceRunnerThread(QThread):
    """
    Run a recipe and report step and pass progress via signals.

    Repetition has two layers:
      step repeat     - iteration / iterations_total on step_started
      sequence repeat - run_index / runs_total on run_started / run_finished
    """

    # step_id, step_index, iteration, iterations_total
    step_started = Signal(str, int, int, int)
    step_log = Signal(str, str)                  # step_id, log line
    # step_id, iteration, result
    step_finished = Signal(str, int, dict)
    # For the progress bar: completed step executions, total per pass
    progress_changed = Signal(int, int)
    run_started = Signal(int, int)                # run_index, runs_total (0 = infinite)
    run_finished = Signal(object, int, int)       # SequenceResult, run_index, runs_total
    batch_finished = Signal(list)                 # List[SequenceResult]
    batch_failed = Signal(str)

    def __init__(
        self,
        recipe: Recipe,
        modules: Dict[str, Any],
        context: SequenceContext,
        trace_log: Optional[TraceLog] = None,
        stop_on_fail: bool = False,
        sequence_repeat: int = 1,
        stop_batch_on_fail: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("SequenceRunnerThread")
        self.recipe = recipe
        self.modules = modules
        self.context = context
        self.trace_log = trace_log
        self.stop_on_fail = stop_on_fail
        self.sequence_repeat = sequence_repeat
        self.stop_batch_on_fail = stop_batch_on_fail

        self.steps_per_run = count_step_executions(recipe)
        self._completed_in_run = 0
        self._cancelled = False

    # -------------------------------------------------------------- control
    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    # -------------------------------------------------------------- execution
    def run(self) -> None:
        try:
            results = run_sequence_batch(
                self.recipe,
                self.modules,
                self.context,
                repeat=self.sequence_repeat,
                now=lambda: datetime.datetime.now().isoformat(timespec="seconds"),
                trace_log=self.trace_log,
                on_run_started=self._on_run_started,
                on_run_finished=self._on_run_finished,
                on_step_started=self._on_step_started,
                on_step_finished=self._on_step_finished,
                on_log=self._on_log,
                is_cancelled=lambda: self._cancelled,
                stop_on_fail=self.stop_on_fail,
                stop_batch_on_fail=self.stop_batch_on_fail,
            )
            self.batch_finished.emit(results)
        except Exception as e:
            self.batch_failed.emit(f"{type(e).__name__}: {e}")

    # ------------------------------------------------------ callbacks -> signals
    def _on_run_started(self, index: int, total: int) -> None:
        self._completed_in_run = 0
        self.progress_changed.emit(0, self.steps_per_run)
        self.run_started.emit(index, total)

    def _on_run_finished(self, result: SequenceResult, index: int, total: int) -> None:
        self.run_finished.emit(result, index, total)

    def _on_step_started(self, step: RecipeStep, index: int, iteration: int, iterations: int) -> None:
        self.step_started.emit(step.step_id, index, iteration, iterations)

    def _on_step_finished(self, step: RecipeStep, index: int, iteration: int, result: Dict[str, Any]) -> None:
        self._completed_in_run += 1
        self.step_finished.emit(step.step_id, iteration, result)
        self.progress_changed.emit(self._completed_in_run, self.steps_per_run)

    def _on_log(self, step: RecipeStep, message: str) -> None:
        self.step_log.emit(step.step_id, message)
