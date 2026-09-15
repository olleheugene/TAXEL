"""
Headless test runner - an interactive text menu.

Imports core/ and modules/ only, never ui_qt/, so it runs on machines with no Qt
and no display: build servers, a station reached over SSH, a bare container.
Discovery, execution and the serial session come from the same core the GUI uses,
so a module behaves identically in both.

    python3 cli_runner.py
    NRF_LANG=ko python3 cli_runner.py

See README.md for the menu walk-through.
"""

import argparse
import datetime
import getpass
import json
import os
import platform
import sys
from typing import Any, Dict, List, Optional

# Where the traceability log lives. Same folder the GUI writes to, so an
# interactive recipe run and a GUI run end up in one history.
TRACE_DIR_NAME = "trace"

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from cards.base_card import BaseCard, BaseTestModule

# Uses the same core as the GUI. Discovery and execution logic is deliberately
# absent from this file so the two cannot diverge.
from core import ci_report
from core.dependencies import check_dependencies
from core.language import language
from core.recipe import Recipe, RecipeStep, import_recipe
from core.registry import discover_cards, get_card, discover_modules, get_module
from core.runner import (
    instrument_scope,
    register_ppk_target,
    register_serial_target,
    run_card,
    run_module,
)
from core.schema import default_values, normalize_schema
from core.sequence import SequenceContext, count_step_executions, run_sequence_batch
from core.trace import TraceLog

# Same convention as build_binaries.py: the language comes from the environment,
# because a text menu has nowhere to put a language selector.
language.set_language(os.environ.get("NRF_LANG", "en"))

LOADED_CARDS: Dict[str, BaseCard] = {}
LOADED_MODULES = LOADED_CARDS

# Mock is off by default, matching the GUI: the runner measures real hardware
# unless simulation is asked for explicitly.
USE_MOCK = False


def load_cards():
    """Discover and load every test card under cards/ and modules/ (via core.registry)."""
    global LOADED_CARDS
    LOADED_CARDS.clear()
    LOADED_CARDS.update(
        discover_cards(
            BASE_DIR,
            on_error=lambda path, exc: print(language.tr("cli_load_failed", path=path, error=exc)),
        )
    )


load_modules = load_cards


def setup_global_serial_target(port: str = "", baudrate: int = 0) -> None:
    """Configure serial_registry with global DUT port and baudrate from args, layout, or auto-detect."""
    selected_port = str(port or "").strip()
    selected_baud = int(baudrate) if baudrate > 0 else 0

    if not selected_port or selected_baud <= 0:
        layout_path = os.path.join(BASE_DIR, "dashboard_layout.json")
        if os.path.exists(layout_path):
            try:
                with open(layout_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    dev_settings = data.get("device_settings", {})
                    if not selected_port and dev_settings.get("serial_port"):
                        selected_port = str(dev_settings["serial_port"]).strip()
                    if selected_baud <= 0 and dev_settings.get("serial_baudrate"):
                        selected_baud = int(dev_settings["serial_baudrate"])
            except Exception:
                pass

    if selected_baud <= 0:
        selected_baud = 115200

    if not selected_port:
        from cards.serial_session import serial_registry
        avail = serial_registry.available_ports()
        if avail:
            selected_port = avail[0]

    if selected_port:
        from cards.serial_session import serial_registry
        serial_registry.set_default(port=selected_port, baudrate=selected_baud)


def apply_serial_target(modules, port: str = "", baudrate: int = 0) -> None:
    """
    Register the devices as the sequence defaults.
    DUT Serial is globally configured; PPK2 is owned by its config card if present.
    """
    setup_global_serial_target(port=port, baudrate=baudrate)
    for inst in modules:
        if inst.module_type == "config" and getattr(inst, "needs_ppk", False):
            register_ppk_target(default_values(inst))
            break


def module_name(module: BaseTestModule) -> str:
    """
    Localized module name.

    Names and descriptions live in modules/<id>/language/<lang>.json, not as
    attributes, so they have to be read the same way the GUI reads them.
    Falling back to module_id keeps a module with no language file listable.
    """
    return module.get_localized("name", language.current_lang) or module.module_id


def module_description(module: BaseTestModule) -> str:
    return module.get_localized("description", language.current_lang)


def print_banner():
    print("=" * 60)
    print(" " + language.tr("cli_banner"))
    print("=" * 60)


def print_result_card(mod_name: str, result: Dict[str, Any], stream=None):
    """
    :param stream: where to write. Defaults to stdout for the interactive menu;
                   batch mode passes stderr when the report owns stdout.
    """
    out = stream or sys.stdout
    status = result["result"]
    exec_time = result["execution_time_sec"]
    summary = result["summary_text"]

    status_icon = "✅ [PASS]" if status == "PASS" else "❌ [FAIL]"

    print("\n" + "-" * 50, file=out)
    print(" " + language.tr("cli_card_item", name=mod_name), file=out)
    print(" " + language.tr("cli_card_time", sec=f"{exec_time:.2f}"), file=out)
    print(" " + language.tr("cli_card_result", icon=status_icon, summary=summary), file=out)
    print("-" * 50, file=out)

    metrics = result["details"].get("metrics", {})
    if metrics:
        print(" " + language.tr("cli_card_metrics"), file=out)
        for k, v in metrics.items():
            print(f"   • {k:20s}: {v}", file=out)

    logs = result["details"].get("logs", [])
    if logs:
        print("\n " + language.tr("cli_card_logs"), file=out)
        for line in logs:
            print(f"   {line}", file=out)
    print("=" * 50, file=out)


def prompt_criteria(module: BaseTestModule) -> Dict[str, Any]:
    """
    Prompt using the schema directly. Because types and permitted values are
    declared, the CLI can present the same constraints as the GUI.
    """
    values: Dict[str, Any] = {}
    for spec in normalize_schema(module, lang=language.current_lang, include_framework_fields=False):
        hint = f" {spec.unit}" if spec.unit else ""
        if spec.type == "enum" and spec.options:
            hint += f" [{', '.join(str(o) for o in spec.options)}]"
        elif spec.type == "number":
            bounds = []
            if spec.minimum is not None:
                bounds.append(f">={spec.minimum}")
            if spec.maximum is not None:
                bounds.append(f"<={spec.maximum}")
            if bounds:
                hint += f" [{' '.join(bounds)}]"
        elif spec.type == "bool":
            hint += " [y/n]"

        raw = input(language.tr("cli_prompt_field", label=spec.label, value=spec.value, hint=hint)).strip()
        if not raw:
            values[spec.key] = spec.value
            continue

        if spec.type == "bool":
            values[spec.key] = raw.lower() in ("y", "yes", "1", "true", "on")
        elif spec.type == "number":
            try:
                num = float(raw)
                values[spec.key] = int(num) if num.is_integer() else num
            except ValueError:
                print(language.tr("cli_not_a_number", value=spec.value))
                values[spec.key] = spec.value
        else:
            values[spec.key] = raw
    return values


def mock_state_text() -> str:
    return language.tr("cli_mock_on") if USE_MOCK else language.tr("cli_mock_off")


# =====================================================================
# Batch mode - non-interactive, for CI
#
# Everything below assembles a Recipe and hands it to core.sequence, the same
# entry point the GUI's Run All uses. Nothing re-implements execution, so a step
# that passes in CI passes on the bench for the same reason.
# =====================================================================

class Emitter:
    """
    Routes human-readable output away from a machine-readable stdout.

    With `--json -` the report goes to stdout, so progress lines and errors must
    not: a single stray line makes the whole document unparseable. Errors go to
    stderr regardless, which is what a CI log viewer expects anyway.
    """

    def __init__(self, json_to_stdout: bool = False, quiet: bool = False):
        self.stream = sys.stderr if json_to_stdout else sys.stdout
        self.quiet = quiet

    def info(self, message: str) -> None:
        """Progress. Suppressed by --quiet."""
        if not self.quiet:
            print(message, file=self.stream)

    def notice(self, message: str) -> None:
        """Summary and warnings. Shown even with --quiet."""
        print(message, file=self.stream)

    def error(self, message: str) -> None:
        print(message, file=sys.stderr)


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli_runner.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="nRF test runner. No arguments starts the interactive menu.",
        epilog=(
            "exit codes:\n"
            "  0  every pass passed\n"
            "  1  a step or pass failed, or the sequence was cancelled\n"
            "  2  usage error: unknown module, invalid recipe, fingerprint mismatch\n"
            "\n"
            "how to choose what runs:\n"
            "  one module   --run MODULE_ID\n"
            "  a sequence   --recipe FILE      (exported from the GUI)\n"
            "  smoke test   --all --mock       (simulation only)\n"
            "\n"
            "examples:\n"
            "  cli_runner.py --list --json\n"
            "  cli_runner.py --all --mock --junit out/results.xml\n"
            "  cli_runner.py --run dtm_runner --set dtm_runner.tx_channel=39\n"
            "  cli_runner.py --recipe line1.recipe.json --expect-fingerprint 0406b48e --repeat 5\n"
        ),
    )

    what = parser.add_argument_group("what to run")
    what.add_argument("--list", action="store_true",
                      help="list installed modules and exit")
    what.add_argument("--all", action="store_true",
                      help="run every installed module once with default criteria. "
                           "Requires --mock: on real hardware 'whatever happens to be "
                           "installed' is not a test definition - use --recipe")
    what.add_argument("--run", metavar="MODULE_ID", action="append", default=[],
                      help="run this one module. Not repeatable - a sequence comes "
                           "from --recipe, so that what ran is recorded and "
                           "fingerprinted")
    what.add_argument("--recipe", metavar="FILE",
                      help="run a recipe exported from the GUI")

    how = parser.add_argument_group("how to run")
    how.add_argument("--set", metavar="MODULE_ID.KEY=VALUE", action="append", default=[],
                     help="override one criteria value; repeatable")
    how.add_argument("--mock", action="store_true",
                     help="simulate instead of measuring (default: measure real hardware)")
    how.add_argument("--repeat", type=int, default=1, metavar="N",
                     help="run the whole sequence N times (default 1)")
    how.add_argument("--stop-on-fail", action="store_true",
                     help="abort the pass at the first failing step")
    how.add_argument("--stop-batch-on-fail", action="store_true",
                     help="stop repeating once a pass fails")
    how.add_argument("--timeout", type=float, metavar="SEC",
                     help="override every step's timeout")
    how.add_argument("--skip-dep-check", action="store_true",
                     help="run even if a module's prerequisite is missing from the "
                          "sequence (for a module that carries its own port)")

    integrity = parser.add_argument_group("recipe integrity")
    integrity.add_argument("--expect-fingerprint", metavar="HEX", default="",
                           help="fail with exit 2 unless the recipe matches this fingerprint "
                                "(prefix match allowed)")
    integrity.add_argument("--no-verify-artifacts", action="store_true",
                           help="skip hashing of referenced firmware files on import")

    trace_group = parser.add_argument_group("traceability")
    trace_group.add_argument("--dut-serial", default="", metavar="S")
    trace_group.add_argument("--station", default="", metavar="S")
    trace_group.add_argument("--operator", default="", metavar="S")
    trace_group.add_argument("--trace-dir", metavar="DIR",
                             help="append runs to DIR/runs.jsonl and runs.csv")

    dev_group = parser.add_argument_group("hardware devices")
    dev_group.add_argument("--port", "-p", metavar="PORT", default="",
                           help="DUT serial port (e.g. /dev/cu.usbmodem... or COM3). "
                                "Defaults to saved device setting in dashboard_layout.json or auto-detection.")
    dev_group.add_argument("--baudrate", "-b", type=int, default=0, metavar="BAUD",
                           help="DUT serial baud rate (default: 115200 or saved device setting)")

    out = parser.add_argument_group("output")
    out.add_argument("--json", metavar="PATH", nargs="?", const="-",
                     help="write the JSON report (PATH or - for stdout)")
    out.add_argument("--junit", metavar="PATH",
                     help="write a JUnit XML report for the CI dashboard")
    out.add_argument("--quiet", action="store_true",
                     help="suppress per-step cards; the summary is still printed")

    return parser


def coerce_value(spec, raw: str) -> Any:
    """
    Turn a command-line string into the type the schema declares.

    Without this every override would arrive as a string and a numeric threshold
    comparison would fail in a way that looks like a hardware problem.
    """
    if spec is None:
        return raw
    if spec.type == "bool":
        return raw.strip().lower() in ("1", "y", "yes", "true", "on")
    if spec.type == "number":
        num = float(raw)
        return int(num) if num.is_integer() else num
    if spec.type == "enum" and spec.options:
        # Match against the declared options so --set phy_mode=2 hits the int 2.
        for option in spec.options:
            if str(option) == raw:
                return option
    return raw


def parse_overrides(raw_sets: List[str], modules: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Parse --set MODULE_ID.KEY=VALUE into {module_id: {key: value}}.

    Unknown module ids and unknown keys are rejected rather than ignored: a typo
    in a CI script must not silently run the test with its default threshold.
    """
    result: Dict[str, Dict[str, Any]] = {}
    for item in raw_sets:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            raise ValueError(language.tr("cli_set_malformed", item=item))
        target, raw_value = item.split("=", 1)
        module_id, key = target.split(".", 1)

        module = get_module(modules, module_id)
        if module is None:
            raise ValueError(language.tr("cli_set_unknown_module", module=module_id))

        specs = {s.key: s for s in normalize_schema(module, lang="en",
                                                   include_framework_fields=True)}
        if key not in specs:
            raise ValueError(language.tr("cli_set_unknown_key", module=module_id, field=key,
                                         fields=", ".join(sorted(specs))))
        try:
            value = coerce_value(specs[key], raw_value)
        except ValueError:
            raise ValueError(language.tr("cli_set_bad_value", value=raw_value, field=key,
                                         type=specs[key].type))
        result.setdefault(module.module_id, {})[key] = value
    return result


def build_recipe(
    module_ids: List[str],
    modules: Dict[str, Any],
    overrides: Dict[str, Dict[str, Any]],
    timeout: Optional[float] = None,
) -> Recipe:
    """Assemble an ad-hoc recipe from --run / --all, defaults plus overrides."""
    steps = []
    for module_id in module_ids:
        module = get_module(modules, module_id)
        criteria = default_values(module)
        criteria.update(overrides.get(module.module_id, {}))
        steps.append(RecipeStep(
            module_id=module.module_id,
            criteria=criteria,
            timeout_sec=timeout if timeout is not None else 0.0,
        ))
    return Recipe(name="cli", steps=steps)


def resolve_run_targets(args, modules: Dict[str, Any]) -> List[str]:
    """
    Module ids to run, in order. Raises ValueError naming every unknown id.

    Only two shapes are allowed, because a test definition has to be something
    that was written down:

      --run MODULE_ID   exactly one module
      --recipe FILE     a sequence, recorded and fingerprinted

    An ad-hoc multi-module sequence is deliberately not offered. "Whatever
    modules happen to be installed on this station, in whatever order" reads
    like a test plan but is not one: install a module and the plan silently
    changes. --all keeps that convenience for simulation, where nothing is
    measured and the point is only to check that every module loads.
    """
    if args.all:
        if not args.mock:
            raise ValueError(language.tr("cli_all_requires_mock"))
        return sorted(
            modules,
            key=lambda mid: (0 if modules[mid].module_type == "config" else 1, mid),
        )

    if len(args.run) > 1:
        raise ValueError(language.tr("cli_run_single_only",
                                     modules=", ".join(args.run),
                                     count=len(args.run)))

    unknown = [mid for mid in args.run if get_module(modules, mid) is None]
    if unknown:
        raise ValueError(language.tr("cli_unknown_modules",
                                     modules=", ".join(unknown),
                                     known=", ".join(sorted(modules))))
    target_mod = get_module(modules, args.run[0])
    if not args.mock and not getattr(target_mod, "is_ready", True):
        raise ValueError(
            language.tr("cli_card_wip_requires_mock",
                        card=target_mod.module_id,
                        name=module_name(target_mod))
        )
    return [target_mod.module_id]


def load_recipe_for_ci(args, modules: Dict[str, Any], out: "Emitter") -> Recipe:
    """
    Load and validate a recipe file.

    Validation is not optional here. In the GUI a warning can be read and judged;
    in CI an unnoticed warning becomes a shipped board, so anything that would
    change what runs stops the run with exit 2.
    """
    recipe, report = import_recipe(args.recipe, modules,
                                   verify_artifacts=not args.no_verify_artifacts)
    if recipe is None or not report.ok:
        raise ValueError(language.tr("cli_recipe_invalid", path=args.recipe,
                                     detail=report.as_text()))
    if report.warnings:
        for warning in report.warnings:
            out.notice(language.tr("cli_recipe_warning", detail=warning))

    if args.expect_fingerprint:
        actual = recipe.fingerprint()
        expected = args.expect_fingerprint.strip().lower()
        if not actual.startswith(expected):
            raise ValueError(language.tr("cli_fingerprint_mismatch",
                                         expected=expected, actual=actual))
    return recipe


def check_sequence_dependencies(
    recipe: Recipe,
    modules: Dict[str, Any],
    single_module: bool = False,
) -> None:
    """
    Reject a sequence whose prerequisites are not in it, before anything runs.

    Without this the run still fails, but as a step FAIL - "no serial port
    configured" - which CI reports the same way as a bad board. It is not a bad
    board, it is a sequence missing its serial session owner, and the two need
    different people to look at them.

    The GUI performs this check when a card is dropped; doing it here keeps the
    two frontends honest about the same rule.

    :param single_module: changes only the advice given. In single-module mode
                          "add the prerequisite to the sequence" is impossible,
                          so the message points at --recipe or a port override
                          instead of suggesting something the user cannot do.
    """
    present = [step.module_id for step in recipe.steps if step.enabled]
    problems: List[str] = []
    for step in recipe.steps:
        if not step.enabled:
            continue
        module = get_module(modules, step.module_id)
        if module is None:
            continue
        # Pass the step's criteria so that a port override counts as satisfying
        # the serial prerequisite.
        report = check_dependencies(module, present, modules, step.criteria)
        if not report.ok:
            problems.append(f"{step.module_id}: {report.describe()}")
    if problems:
        key = "cli_deps_unmet_single" if single_module else "cli_deps_unmet"
        raise ValueError(language.tr(key, detail="\n".join(problems)))


def print_module_list(modules: Dict[str, Any], as_json: bool, stream=None) -> None:
    """--list is a query, so its answer is the payload and belongs on stdout."""
    out = stream or sys.stdout
    if as_json:
        payload = [
            {
                "module_id": mid,
                "version": getattr(module, "version", "1.0.0"),
                "is_ready": getattr(module, "is_ready", True),
                "name": module_name(module),
                "description": module_description(module),
                "module_type": module.module_type,
                "needs_serial": bool(getattr(module, "needs_serial", False)),
                "criteria": [
                    {
                        "key": spec.key,
                        "label": spec.label,
                        "default": spec.value,
                        "unit": spec.unit,
                        "type": spec.type,
                        "options": spec.options,
                        "minimum": spec.minimum,
                        "maximum": spec.maximum,
                    }
                    for spec in normalize_schema(module, lang="en",
                                                 include_framework_fields=False)
                ],
            }
            for mid, module in sorted(modules.items())
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=out)
        return

    for mid, module in sorted(modules.items()):
        caps = "needs_serial" if getattr(module, "needs_serial", False) else "-"
        ver = f"v{getattr(module, 'version', '1.0.0')}"
        status = "READY" if getattr(module, "is_ready", True) else "WIP"
        print(f"{mid:24s} [{ver:7s}] [{status:5s}] [{module.module_type}/{caps}]  {module_name(module)}", file=out)


def run_batch(args) -> int:
    """Non-interactive execution. Returns the process exit code."""
    global USE_MOCK
    USE_MOCK = args.mock
    out = Emitter(json_to_stdout=(args.json == "-"), quiet=args.quiet)

    load_modules()
    if not LOADED_MODULES:
        out.error(language.tr("cli_no_modules"))
        return ci_report.EXIT_USAGE

    if args.list:
        if args.json and args.json != "-":
            with open(args.json, "w", encoding="utf-8") as f:
                print_module_list(LOADED_MODULES, as_json=True, stream=f)
        else:
            print_module_list(LOADED_MODULES, as_json=bool(args.json))
        return ci_report.EXIT_OK

    try:
        overrides = parse_overrides(args.set, LOADED_MODULES)
        if args.recipe:
            recipe = load_recipe_for_ci(args, LOADED_MODULES, out)
            source = args.recipe
            if overrides:
                # A locked recipe is the production artefact; letting a flag edit
                # it would defeat the fingerprint it is checked against.
                if recipe.locked:
                    raise ValueError(language.tr("cli_set_locked_recipe"))
                for step in recipe.steps:
                    step.criteria.update(overrides.get(step.module_id, {}))
        else:
            targets = resolve_run_targets(args, LOADED_MODULES)
            if not targets:
                raise ValueError(language.tr("cli_nothing_to_run"))
            recipe = build_recipe(targets, LOADED_MODULES, overrides, args.timeout)
            source = "--all" if args.all else "--run"

        if not args.skip_dep_check:
            check_sequence_dependencies(
                recipe, LOADED_MODULES,
                single_module=bool(args.run) and not args.recipe,
            )
    except ValueError as e:
        out.error(language.tr("cli_batch_error", error=e))
        return ci_report.EXIT_USAGE

    if args.timeout is not None:
        for step in recipe.steps:
            step.timeout_sec = args.timeout

    trace_log = TraceLog(args.trace_dir) if args.trace_dir else None
    context = SequenceContext(
        dut_serial=args.dut_serial,
        mode="ci",
        station=args.station,
        operator=args.operator,
        use_mock=args.mock,
    )

    out.info(language.tr("cli_batch_start",
                         name=recipe.label,
                         steps=count_step_executions(recipe),
                         repeat=args.repeat,
                         mock=mock_state_text()))

    def on_step_finished(step, index, iteration, result):
        if not args.quiet:
            print_result_card(
                module_name(get_module(LOADED_MODULES, step.module_id)),
                result,
                stream=out.stream,
            )

    with instrument_scope():
        apply_serial_target(
            LOADED_MODULES.values(),
            port=getattr(args, "port", ""),
            baudrate=getattr(args, "baudrate", 0),
        )
        results = run_sequence_batch(
            recipe,
            LOADED_MODULES,
            context,
            repeat=args.repeat,
            now=lambda: datetime.datetime.now().isoformat(timespec="seconds"),
            trace_log=trace_log,
            on_step_finished=on_step_finished,
            stop_on_fail=args.stop_on_fail,
            stop_batch_on_fail=args.stop_batch_on_fail,
        )

    summary = ci_report.summarize(results)
    out.notice(language.tr("cli_batch_summary",
                      runs=summary["runs_total"],
                      runs_passed=summary["runs_passed"],
                      steps=summary["steps_total"],
                      steps_passed=summary["steps_passed"],
                      sec=f"{summary['duration_sec']:.2f}"))

    if args.json:
        ci_report.write_json(args.json, ci_report.build_json(
            results, recipe=recipe, mock=args.mock, source=source))
    if args.junit:
        ci_report.write_junit(args.junit, ci_report.build_junit(
            results, suite_name=recipe.name or "automate_test", mock=args.mock))

    return ci_report.exit_code(results)


def run_recipe_interactive() -> None:
    """
    Load a recipe file and run it from the interactive menu.

    This is the menu's only way to run a sequence, matching the rule the batch
    mode enforces: a sequence has to be something that was written down. It goes
    through the same core.sequence path as the GUI and the --recipe flag, and it
    appends to the same trace/ log the GUI writes, so an interactive run is as
    traceable as a production one.
    """
    raw = input("\n" + language.tr("cli_recipe_prompt")).strip()
    # Quoting a dragged-in path is common, and a stray quote reads as "file not
    # found" which sends people looking in the wrong place.
    path = raw.strip('"').strip("'")
    if not path:
        print(language.tr("cli_recipe_cancelled"))
        return
    if not os.path.exists(path):
        print(language.tr("cli_recipe_not_found", path=path))
        return

    recipe, report = import_recipe(path, LOADED_MODULES)
    if recipe is None or not report.ok:
        print("\n" + language.tr("cli_recipe_invalid", path=path,
                                 detail=report.as_text()))
        return
    for warning in report.warnings:
        print(language.tr("cli_recipe_warning", detail=warning))

    try:
        check_sequence_dependencies(recipe, LOADED_MODULES)
    except ValueError as e:
        print("\n" + language.tr("cli_batch_error", error=e))
        return

    repeat_raw = input(language.tr("cli_recipe_repeat_prompt")).strip()
    try:
        repeat = max(1, int(repeat_raw)) if repeat_raw else 1
    except ValueError:
        print(language.tr("cli_not_a_number", value=1))
        repeat = 1

    dut_serial = input(language.tr("cli_recipe_dut_prompt")).strip()

    print("\n" + language.tr("cli_recipe_loaded",
                             name=recipe.label,
                             fingerprint=recipe.short_fingerprint,
                             steps=count_step_executions(recipe),
                             repeat=repeat,
                             mock=mock_state_text()))

    trace_log = TraceLog(os.path.join(BASE_DIR, TRACE_DIR_NAME))
    context = SequenceContext(
        dut_serial=dut_serial,
        mode="cli",
        station=platform.node(),
        operator=getpass.getuser(),
        use_mock=USE_MOCK,
    )

    def on_step_finished(step, index, iteration, result):
        module = get_module(LOADED_MODULES, step.module_id)
        print_result_card(module_name(module) if module else step.module_id, result)

    with instrument_scope():
        apply_serial_target(LOADED_MODULES.values())
        results = run_sequence_batch(
            recipe,
            LOADED_MODULES,
            context,
            repeat=repeat,
            now=lambda: datetime.datetime.now().isoformat(timespec="seconds"),
            trace_log=trace_log,
            on_step_finished=on_step_finished,
        )

    summary = ci_report.summarize(results)
    print(language.tr("cli_batch_summary",
                      runs=summary["runs_total"],
                      runs_passed=summary["runs_passed"],
                      steps=summary["steps_total"],
                      steps_passed=summary["steps_passed"],
                      sec=f"{summary['duration_sec']:.2f}"))
    print(language.tr("cli_recipe_traced", path=trace_log.jsonl_path))


def main():
    global USE_MOCK
    load_modules()
    print_banner()
    print(language.tr("cli_modules_loaded", count=len(LOADED_MODULES)) + "\n")

    if not LOADED_MODULES:
        print(language.tr("cli_no_modules"))
        return

    module_list = list(LOADED_MODULES.values())

    while True:
        # Ordered by what a real run should use: a recipe first, then a single
        # module. "Run every installed module" comes after them because it is a
        # simulation convenience, not a test definition.
        print("\n" + language.tr("cli_menu_header"))
        print(language.tr("cli_menu_recipe"))
        print(language.tr("cli_menu_run_one"))
        print(language.tr("cli_menu_run_all"))
        print(language.tr("cli_menu_list"))
        print(language.tr("cli_menu_mock", state=mock_state_text()))
        print(language.tr("cli_menu_quit"))

        choice = input("\n" + language.tr("cli_prompt_menu")).strip()

        if choice == "1":
            run_recipe_interactive()

        elif choice == "2":
            print("\n" + language.tr("cli_select_header"))
            for idx, instance in enumerate(module_list, 1):
                print(f" {idx}. {module_name(instance)} ({instance.module_id})")

            sel = input("\n" + language.tr("cli_prompt_select")).strip()
            if sel.isdigit() and 1 <= int(sel) <= len(module_list):
                target_mod = module_list[int(sel) - 1]

                print("\n" + language.tr("cli_criteria_header", name=module_name(target_mod)))
                current_criteria = prompt_criteria(target_mod)

                print("\n" + language.tr("cli_running_one", name=module_name(target_mod)))
                with instrument_scope():
                    apply_serial_target(module_list)
                    outcome = run_module(target_mod, current_criteria, use_mock=USE_MOCK)
                print_result_card(module_name(target_mod), outcome.result)
            else:
                print(language.tr("cli_bad_select"))

        elif choice == "3":
            # Same rule as --all in batch mode: "every module installed here" is
            # a simulation convenience, not a test definition. On real hardware
            # the sequence must come from a recipe (menu item 1), which records
            # what ran.
            if not USE_MOCK:
                print("\n" + language.tr("cli_menu_all_requires_mock"))
                continue
            print("\n" + language.tr("cli_running_all"))
            # Wrap the whole sequence in one serial-session scope.
            with instrument_scope():
                apply_serial_target(module_list)
                for instance in module_list:
                    outcome = run_module(instance, default_values(instance), use_mock=USE_MOCK)
                    print_result_card(module_name(instance), outcome.result)

        elif choice == "4":
            print("\n" + language.tr("cli_spec_header"))
            for instance in module_list:
                caps = "needs_serial" if getattr(instance, "needs_serial", False) else "-"
                print(f"\n• {module_name(instance)} ({instance.module_id})  [{instance.module_type} / {caps}]")
                print(language.tr("cli_spec_desc", desc=module_description(instance)))
                print(language.tr("cli_spec_criteria"))
                for spec in normalize_schema(instance, lang=language.current_lang, include_framework_fields=False):
                    extra = ""
                    if spec.type == "enum" and spec.options:
                        extra = f"  options={spec.options}"
                    elif spec.type == "number" and (spec.minimum is not None or spec.maximum is not None):
                        extra = f"  range=[{spec.minimum}, {spec.maximum}]"
                    elif spec.type in ("file", "dir") and spec.accept:
                        extra = f"  accept={spec.accept}"
                    print(f"   - {spec.key}: {spec.label} = {spec.value} {spec.unit} <{spec.type}>{extra}")

        elif choice == "5":
            USE_MOCK = not USE_MOCK
            print("\n" + language.tr("cli_mock_toggled", state=mock_state_text()))

        elif choice == "0":
            print("\n" + language.tr("cli_quit"))
            break
        else:
            print(language.tr("cli_bad_menu"))


def entry() -> int:
    """
    Dispatch: bare invocation keeps the interactive menu, any flag means batch.

    Keeping one file for both is deliberate - module discovery, the serial
    session and the execution path stay shared, so the menu cannot drift away
    from what CI runs.
    """
    args = build_argparser().parse_args()
    if args.list or args.all or args.run or args.recipe:
        return run_batch(args)

    # Flags that only make sense with a target were passed on their own.
    if any([args.set, args.json, args.junit, args.trace_dir, args.mock,
            args.dut_serial, args.station, args.operator, args.quiet,
            args.repeat != 1, args.timeout is not None,
            args.stop_on_fail, args.stop_batch_on_fail, args.skip_dep_check,
            args.expect_fingerprint, args.no_verify_artifacts]):
        print(language.tr("cli_needs_target"), file=sys.stderr)
        return ci_report.EXIT_USAGE

    main()
    return ci_report.EXIT_OK


if __name__ == "__main__":
    sys.exit(entry())
