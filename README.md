# TAXEL (Test Acceleration Framework)

A test-automation framework for Nordic nRF devices. Test steps are **plug-in cards (modules)**: drop a folder under `cards/`, restart, and it appears in the UI — no framework code to edit.

> [!NOTE]
> **Disclaimer & Acknowledgement**
> - **Stability & Usage**: TAXEL was created to facilitate rapid development and streamline bench testing workflows. It does **not** guarantee commercial-grade stability or formal validation. Users are encouraged to evaluate it for development purposes, or fork and adapt the codebase to fit their specific requirements.
> - **AI-Assisted Development**: This project was designed and developed in active collaboration with AI coding assistants, including **Google Gemini** and **Anthropic Claude**.

It serves two jobs at once, which is why some of its design choices look stricter than a test script needs:

- **A production programming and test tool.** Recipes can be locked, every run is fingerprinted and appended to a traceability log, and an operator sees a
  deliberately reduced UI.
- **A development test bench.** The same modules, the same execution path, but with editable criteria, simulation mode, and a headless CLI for CI.

One core drives both a desktop GUI and a Qt-free CLI, so a step that passes in CI passes on the bench for the same reason.

---

## Documentation (Wiki)

Complete documentation is maintained in the [**GitHub Wiki**](https://github.com/olleheugene/taxel/wiki):

| Document | Korean / 한국어 | What it covers |
|---|---|---|
| **[Wiki Home](https://github.com/olleheugene/taxel/wiki)** | [한국어](https://github.com/olleheugene/taxel/wiki/Home-Ko) | Overview, key features, architecture, and complete wiki navigation index. |
| **[Getting Started](https://github.com/olleheugene/taxel/wiki/Getting-Started)** | [한국어](https://github.com/olleheugene/taxel/wiki/Getting-Started-Ko) | System requirements, Python virtual environment setup, dependencies installation, and first run. |
| **[GUI User Guide](https://github.com/olleheugene/taxel/wiki/GUI-User-Guide)** | [한국어](https://github.com/olleheugene/taxel/wiki/GUI-User-Guide-Ko) | Desktop UI layout, card palette and dashboard, global hardware connection toolbar, operator vs. engineer modes, test execution, and reports. |
| **[CLI User Guide](https://github.com/olleheugene/taxel/wiki/CLI-User-Guide)** | [한국어](https://github.com/olleheugene/taxel/wiki/CLI-User-Guide-Ko) | Interactive terminal menu, headless batch mode, command-line arguments, and CI/CD pipeline integration. |
| **[Recipe Management](https://github.com/olleheugene/taxel/wiki/Recipe-Management)** | [한국어](https://github.com/olleheugene/taxel/wiki/Recipe-Management-Ko) | Recipe file (`.recipe.json`) structure, SHA-256 content fingerprinting, export/import, and tamper-proof locking. |
| **[Standard Cards Reference](https://github.com/olleheugene/taxel/wiki/Standard-Cards-Reference)** | [한국어](https://github.com/olleheugene/taxel/wiki/Standard-Cards-Reference-Ko) | Specification and default criteria for the 11 built-in cards (PPK2 config, DTM RF tests, PPK power profiling, firmware flasher, GPIO test, etc.). |
| **[Card Development Guide](https://github.com/olleheugene/taxel/wiki/Module-Development-Guide)** | [한국어](https://github.com/olleheugene/taxel/wiki/Module-Development-Guide-Ko) | Complete technical reference manual for card authors: class contract, criteria schema, serial session sharing, scaffolding tool, result format, and binary packaging. |
| **[PPK2 API Reference](https://github.com/olleheugene/taxel/wiki/PPK2-API)** | — | Shared Power Profiler Kit II session, current measurement mechanics, `PPKStats` units, idle current, and DTM measurement sequences. |
| **[Library DTM API Reference](https://github.com/olleheugene/taxel/wiki/Library-DTM-API)** | — | Direct Test Mode (DTM) library in `library/`: 23-key setup dictionary, return shapes, and shared serial session borrowing. |

---

## How it is put together

```
                          ┌─ gui_app.py ────→ ui_qt/ ────→ PySide6
  core/  +  cards/   ─────┤
                          └─ cli_runner.py ──→ (no Qt)
```

| Layer | Responsibility | GUI | CLI |
|---|---|:--:|:--:|
| `core/` | card discovery, execution engine, recipes, traceability, internationalization | ✅ | ✅ |
| `cards/` | test and configuration cards, and the shared serial session | ✅ | ✅ |
| `library/` | shared device libraries (DTM), shippable as `.so`/`.pyd` | ✅ | ✅ |
| `ui_qt/` | **Qt widgets only** — settings dialogs, recipe dialogs, the worker thread | ✅ | ❌ |

`ui_qt/` is imported by `gui_app.py` alone. Importing `cli_runner.py` loads no PySide6 at all, so the CLI runs on machines with no Qt and no display: build
servers, a station reached over SSH, a bare container. It also means `ui_qt/` is the replaceable layer — a different frontend would reuse `core/` and `cards/`
unchanged.

See the [Card Development Guide](https://github.com/olleheugene/taxel/wiki/Module-Development-Guide) for the full directory layout.

---

## Install

Python 3.10 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Three runtime dependencies: **pyserial** for the serial sessions, **PySide6** and **matplotlib** for the desktop GUI. The CLI imports no Qt, so `pip install pyserial` alone is enough on a headless machine.

Compiling cards to `.so`/`.pyd` additionally needs `pip install -r requirements-build.txt` (cython, setuptools). See
[Binary distribution](#binary-distribution).

```bash
python3 gui_app.py        # desktop GUI
python3 cli_runner.py     # interactive text menu, no Qt required
```

Both read the same `cards/` folder and honour `NRF_LANG` (`en`, `ko`, `ja`, `zh`).

---

# The UI
![Main Window](https://github.com/olleheugene/taxel/wiki/resources/en_main_window.png)
## Menu bar

Everything that is configured once lives here. It used to sit on the top bar, which made the window too wide for a FullHD screen.

| Menu | Items |
|---|---|
| **Recipe** | Recipe Info / Lock · Import Recipe (`.recipe.json`) · Export Recipe (`.recipe.json`) |
| **Test** | Run All Tests · Reset Results · View Test Report · Reset Yield Counters · Open Trace Log Folder |
| **Settings** | Mock Simulation Mode · Auto-generate summary report after all tests · Stop sequence on failure · Stop repeats on failure · Reload Cards · Language (en / ko / ja / zh) |
| **Mode** | Switch Mode — Operator ⇄ Engineer |

## Top bar

Only what is watched and operated *while a run is in progress*.

| Element | Meaning |
|---|---|
| **Mode button** | Current mode. Click to switch; entering Engineer may ask for a passcode. |
| **Recipe name + fingerprint** | Click to open recipe info and locking. The short hex is the fingerprint — it changes whenever any step, criteria value, or referenced firmware file changes. |
| **DUT label** | The serial number of the unit under test. Operator mode requires one before running. |
| **🔁 repeat spin box** | How many times to repeat **the whole sequence**. `∞` (value 0) repeats until stopped, for burn-in. Per-step repeat is a separate setting inside each card. |
| **View Test Report** | The report for the last run. Also in the Test menu. |
| **Run All Tests** | Runs the sequence. Turns into a stop control while running. |
| **Yield counters** | Operator mode only: Total / PASS / FAIL / Yield for **today**, read from the traceability log. |

## Card palette (left)

Every test or configuration card found under `cards/`, config cards first. **Drag** a card onto the dashboard, or **double-click** it.

The search box filters on **name, description, tags and `card_id`**. Multiple words narrow the result (AND). Tags are matched in *every* installed language,
so an English UI still finds a card by a Korean keyword. The full description and tag list are in each item's tooltip.

Adding a card runs a **prerequisite check**: a card that needs the shared PPK2 session but has no PPK2 Interface Config card on the dashboard is refused with an explanation, and the framework offers to add the missing card.

The palette is hidden in Operator mode — an operator must not be able to change what gets tested.

## Test card dashboard (right)

One card per test step, executed top to bottom. Drag cards to reorder.

Each card shows three lines: the card name and its buttons, a status line (elapsed time, serial requirement, repeat count, dependency warning), and the result summary, colour-coded by verdict.

| Button | Action |
|---|---|
| ⬆ ⬇ | Move the step earlier or later |
| ❓ | The card's wiring guide and pass/fail criteria explanation |
| ⚙ | Settings, generated from the card's criteria schema |
| ▶ | Run this step alone |
| 🗑 | Remove the step |

Removing a card that others depend on warns first.

## Status bar

Progress is counted in **actual step executions**, so a step set to repeat three times counts as three. `Run 2/3` tracks whole-sequence repeats. On the right are the station and operator names; its tooltip is the path of the traceability log.

## Operator and Engineer modes

The two purposes of this project meet here. Policy lives in `core/modes.py`, and the UI is built from those flags rather than from scattered checks.

| | Operator | Engineer |
|---|:--:|:--:|
| Run tests | ✅ | ✅ |
| Import a recipe | ✅ | ✅ |
| Card palette shown | — | ✅ |
| Add / remove / reorder cards | — | ✅ |
| Edit criteria | — | ✅ |
| Edit or export a recipe | — | ✅ |
| Reload cards | — | ✅ |
| Toggle mock simulation | — | ✅ |
| Forced to real hardware | ✅ | — |
| DUT serial required | ✅ | — |
| Yield counters shown | ✅ | — |

Operator mode cannot silently ship simulated results: mock is forced off and the toggle is disabled. A **locked recipe** additionally freezes criteria even for the operator.

---

# Command line and CI

`cli_runner.py` with no arguments opens an interactive menu; with any argument it becomes non-interactive batch mode for CI.

```
[Menu]
 1. Run a recipe file (a recorded sequence)
 2. Run a single test
 3. Run every installed module (mock only)
 4. List modules and default criteria
 5. Toggle mock simulation (now: OFF - measuring real hardware)
 0. Quit
```

Item 1 asks for a `.recipe.json` path, a repeat count and a DUT serial, then runs the recipe through the same `core.sequence` path the GUI uses and **appends to the
same `trace/` log** — so an interactive run is as traceable as a production one. An invalid or tampered recipe is refused before anything runs.

Item 3 is the simulation convenience and refuses to run on real hardware, for the reason given under [Choosing what runs](#choosing-what-runs).

Batch mode assembles a recipe and hands it to the **same** `core.sequence` execution path the GUI's Run All uses, so recipes, per-step repeat, whole-sequence repeat and traceability all work there too.

## Exit codes

| Code | Meaning |
|:--:|---|
| `0` | Every pass passed |
| `1` | A step or pass failed, or the sequence was cancelled |
| `2` | Usage error — unknown module, invalid recipe, fingerprint mismatch, bad `--set`, missing prerequisite |

`2` is separate from `1` on purpose: *a test failed* and *the test never started* are different problems for different people.

## Choosing what runs

There are exactly three shapes, and the restriction is deliberate.

| Goal | Command |
|---|---|
| One module | `--run MODULE_ID` |
| **A sequence** | `--recipe FILE` |
| Smoke test, no hardware | `--all --mock` |

**`--all` requires `--mock`, and `--run` takes a single module.** On real hardware "every module installed on this station" is not a test definition: install a module and what runs changes silently, and nothing records what was executed. A sequence has to be something that was written down — a recipe, whose steps, order and criteria are fingerprinted and land in the trace record.

`--all --mock` keeps the convenience where it is harmless: nothing is measured, and the point is only to check that every module loads and its schema is valid. That is what the CI `mock` job does.

Recipes are created in the GUI (`Recipe → Export Recipe`); the CLI runs them but does not author them.

```bash
$ python3 cli_runner.py --all
❌ --all requires --mock.
   Use --recipe FILE for a sequence, or --run MODULE_ID for one module.

$ python3 cli_runner.py --run serial_log_monitor --run dtm_runner
❌ --run takes one module, but 2 were given.
   Export one from the GUI (Recipe → Export Recipe) and pass --recipe FILE.
```

## Common flags

```bash
--list [--json]                  # modules and their criteria schema
--run ID | --recipe FILE | --all --mock    # what to run (see above)
--port PORT  --baudrate BAUD     # DUT serial hardware settings (or -p / -b)
--set ID.KEY=VALUE               # override one criteria value, repeatable
--mock                           # simulate (default: measure real hardware)
--repeat N  --stop-on-fail  --timeout SEC
--expect-fingerprint HEX         # refuse to run if the recipe changed
--dut-serial S  --station S  --operator S  --trace-dir DIR
--json PATH|-   --junit PATH     # reports
```

`--help` lists them all. Errors always go to stderr, and with `--json -` the progress output moves to stderr too, so stdout stays parseable:

```bash
python3 cli_runner.py --all --mock --json - | jq .summary
```

## Reports

**JSON** carries the full record — every step, metric, log line and criteria value — in the same schema as `runs.jsonl`. Keys are fixed English regardless of `NRF_LANG`, because a report a script parses must not change shape with the UI language.

**JUnit XML** gives one `<testsuite>` per pass and one `<testcase>` per step execution, so GitHub Actions, GitLab CI and Jenkins name the failing step directly. Failures carry the summary, error and logs; DUT serial and recipe fingerprint are recorded as properties.

## Suggested CI layout

Split into two jobs, because they need different machines:

| Job | Runner | Purpose |
|---|---|---|
| `mock` | hosted | No hardware. Catches modules that fail to load, recipes naming a `module_id` that no longer exists, schema typos. **This is the one to gate pull requests on.** |
| `bench` | self-hosted | Real measurement with the DK and PPK2 attached. Cannot run on a hosted runner, so it must not be a required check. |

```bash
# mock job — needs only pyserial, since cli_runner.py never imports ui_qt
pip install pyserial
python3 cli_runner.py --list --json > modules.json
python3 cli_runner.py --all --mock --junit reports/junit.xml --json reports/report.json

# bench job — on the station, against the reviewed recipe
python3 cli_runner.py --recipe recipes/line1.recipe.json \
  --expect-fingerprint "$RECIPE_FINGERPRINT" \
  --dut-serial "ci-$BUILD_NUMBER" --station "$RUNNER_NAME" --operator ci \
  --stop-on-fail --trace-dir trace \
  --junit reports/junit.xml --json reports/report.json
```

`--expect-fingerprint` is the point of the bench job: it fails the build if the recipe changed at all — criteria, step order, or the hash of the firmware it points at. Keep the expected value in a CI variable so it only changes in a reviewed commit.

## Serial port override

Serial is a **shared session** by default: the top Hardware Connection toolbar (or `--port` CLI flag) opens the port and every `needs_serial` card inherits that handle. One open, one DTR/RTS assert, no mid-sequence DUT reset.

Sharing is keyed by **port**, not by sequence. A card may name its own port when it talks to a *different* device — a second CDC ACM, an instrument on its own port:

```bash
# in a sequence: set the override on one step of the recipe
python3 cli_runner.py --recipe recipes/line1.recipe.json \
  --set ppk_dtm_tx_power.port=/dev/cu.usbmodem-SECOND

# a single card pointed at its own port. This also satisfies the serial
# prerequisite, since it no longer borrows the shared session.
python3 cli_runner.py --run dtm_tx_test --set dtm_tx_test.port=/dev/cu.usbmodem-SECOND
```

Empty means inherit, and an empty override is not stored in the recipe, so existing recipe fingerprints are unaffected. In the GUI the override is a field in the card's settings, and an overridden card is badged `⚠️ Own port: …`.

Details are in the [Card Development Guide](https://github.com/olleheugene/taxel/wiki/Module-Development-Guide).

---

# Binary distribution

`library/` and `cards/` sources compile to `.so` (Linux/macOS) or `.pyd` (Windows) with Cython. Filenames carry an ABI tag, so binaries for several platforms coexist in one folder; each Python loads only its own and falls back to the `.py` source when none matches.

```bash
pip install -r requirements-build.txt             # cython, setuptools
python3 build_binaries.py                         # build everything
python3 build_binaries.py --targets cards         # or just cards
python3 build_binaries.py --targets library       # or shared libraries
python3 build_binaries.py --list                  # targets and current state
python3 build_binaries.py --check-stale            # exit 1 if sources are newer
python3 build_binaries.py --clean                  # remove binaries and .c files
python3 build_binaries.py --collect                # gather into dist/<platform>/
python3 build_binaries.py --strip-sources          # drop .py from the distribution
```

Cross-compilation is not possible — build on each target OS and Python version.
A stale binary shadows an edited source, so run `--check-stale` after changing card code. (`build_pyd_modules.py` is an old shim that forwards here.)

---

# Translations

All text comes from language files — **one file per language**, never hardcoded.

```
language/<lang>.json                application-wide text
cards/<id>/language/<lang>.json     one card's name, description, tags, criteria labels
```

Lookup falls back current language → `en` → the key name, so a missing translation degrades to English rather than crashing. Adding a card language file is enough to make that language appear in the Language menu.

```bash
python3 tools/check_language.py    # missing and untranslated keys
```

---

# Traceability

Every run appends to an append-only log, flushed with `os.fsync`:

```
trace/runs.jsonl    the full record, one JSON object per run
trace/runs.csv      the same runs flattened for a spreadsheet
```

Each record carries the run id, timestamps, verdict, DUT serial, recipe name, version and fingerprint, station, operator, whether mock was used, and every step with its criteria, metrics and logs. Yield counters in the top bar are computed from this log, scoped to today.
