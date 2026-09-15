# Card (Module) Development Guide

Reference for writing a test or config card for the **nRF DTM & Test Suite** framework,
and for compiling cards into binaries (`.so` / `.pyd`).

> **Terminology Note**: In the UI and architecture, components are unified under the term **Card** (e.g., Test Card, Config Card). For 100% backward compatibility, `BaseTestModule = BaseCard`, `module_id = card_id`, and `discover_modules = discover_cards`.
>
> **If you are an AI assistant working in this repository:** this document is the
> authoritative contract. Read sections 1 and 2, copy the template, or run `python3 tools/create_card.py <card_id>`. Use sections 4 to 7 as
> lookup tables. Section 9 lists rules whose violation produces silent wrong behaviour —
> check them before reporting a card finished.

---

## 1. Quick contract

A card is one Python file under `cards/<card_id>/` containing one class
that derives from `BaseCard` (or `BaseTestModule`).

**Required**

| # | Requirement | Detail |
|---|---|---|
| 1 | Location | `cards/<card_id>/` |
| 2 | Filename | `card_<card_id>.py`, `test_<card_id>.py`, `config_<card_id>.py`, or `<card_id>.py` |
| 3 | Class derives from `BaseCard` | Framework finds the subclass and instantiates it |
| 4 | `info["card_id"]` (or `module_id`) | Traceability identifier; feeds the recipe fingerprint |
| 5 | `run(self, criteria, use_mock=False) -> dict` | Return shape in section 6 |

**Never**

| Never | Because |
|---|---|
| `serial.Serial(...)` in a card | The port is exclusive and every open asserts DTR/RTS, resetting the DUT mid-sequence → section 7 |
| Return the same value from both `use_mock` branches | Ships a fake PASS from real-hardware mode → section 10.1 |
| Hardcode a display string | Text comes from language files (`language/en.json`, `ko.json`, `ja.json`) → section 9 |
| Declare `port` / `baudrate` / `step_repeat` in `default_criteria` | The framework already adds them → section 5.3 |
| Change `card_id` without `aliases` | Saved dashboards and recipes break → section 10.3 |

**Verify before finishing** → section 11.

---

## 2. Copy-paste template & Scaffold Tool

You can instantly scaffold a new card folder, code, and language files using the generator tool:

```bash
python3 tools/create_card.py <card_id> --category "Category Name"
# (or legacy alias: python3 tools/create_module.py <card_id>)
```

Or copy the comprehensive reference template directly from [`cards/_template/`](../cards/_template/).

Below is the minimal copy-paste template. Replace names and the measurement; the structure is
correct as written:

```python
# cards/my_custom_test/card_my_custom_test.py
"""
One-line statement of what this card measures and what makes it pass.
"""

import time
from typing import Any, Dict

from cards.base_card import BaseCard
from cards.serial_session import SerialSessionError


class MyCustomTestCard(BaseCard):
    info = {
        "card_id": "my_custom_test",         # required, and permanent (see section 10.3)
        "version": "1.0.0",                  # semantic version string (e.g. 1.0.0)
        "is_ready": True,                    # False = WIP/development (disabled in hardware mode, active in MOCK)
        "category": "Custom Category",
        "icon": "fa-microchip",              # FontAwesome name
        "emoji": "🔬",                       # optional; wins over the icon mapping
        "color": "#3b82f6",
        "tags": ["custom", "voltage", "example"],   # palette search (section 4.2)
    }

    # Shared resources requested from the framework (section 7).
    #   True  -> the open shared session arrives as criteria["_serial"] (configured via
    #            the top Hardware Connection toolbar or CLI)
    #   False -> no session is acquired
    # Set it to True only when the DUT VCOM is actually used; this template's
    # measurement does not use it.
    capabilities = {"needs_serial": False}

    # Optional: step time limit in seconds. The framework kills the step at this
    # point and records FAIL rather than hanging the sequence.
    timeout_sec = 60.0

    # Thresholds and configuration. The settings screen is generated from this
    # (section 5) - do not write any UI code.
    default_criteria = {
        "target_voltage_mv": {
            "label": "Target Voltage",
            "value": 3300.0,
            "unit": "mV",
            "type": "number",
            "min": 0.0,
            "max": 5500.0,
            "decimals": 1,
            "help": "Voltage applied to the pin under test.",
        },
        "max_deviation_pct": {
            "label": "Max Allowed Deviation",
            "value": 5.0,
            "unit": "%",
            "type": "number",
            "min": 0.0,
            "max": 100.0,
            "decimals": 2,
        },
    }

    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        start = time.time()
        logs: list = []

        # Live log line: shown in the GUI while running, and stored in the trace
        # record. The framework injects the callback; it may be absent.
        log_cb = criteria.get("_log_callback")

        def log(msg: str) -> None:
            logs.append(msg)
            if log_cb:
                try:
                    log_cb(msg)
                except Exception:
                    pass

        target = float(criteria.get("target_voltage_mv", 3300.0))
        limit = float(criteria.get("max_deviation_pct", 5.0))

        log(f"[INFO] Starting measurement (target={target}mV, limit={limit}%)")

        if use_mock:
            import random
            measured = target * (1.0 + random.uniform(-0.03, 0.03))
            log(f"  [MEASURE] (Mock) {measured:.1f} mV")
        else:
            # Real acquisition. If it is not implemented yet, raise - never fall
            # through to a value that looks like a measurement (section 10.1).
            raise NotImplementedError(
                "Real hardware acquisition is not implemented yet. "
                "Enable Mock Simulation Mode, or implement the measurement path."
            )

            # Reference for the real path:
            # session = self.get_serial(criteria)   # already open, do not close
            # session.reset_input_buffer()
            # session.write_line("measure")
            # for line in session.read_lines(timeout_sec=30.0, idle_timeout_sec=10.0):
            #     ...  # parse

        deviation = abs(measured - target) / target * 100.0 if target else 0.0
        passed = deviation <= limit
        log(f"[RESULT] {'PASS' if passed else 'FAIL'}: deviation {deviation:.2f}%")

        return {
            "result": "PASS" if passed else "FAIL",
            "execution_time_sec": round(time.time() - start, 2),
            "summary_text": (
                f"{'PASS' if passed else 'FAIL'} "
                f"({measured:.1f}mV, dev {deviation:.2f}% / limit {limit}%)"
            ),
            "details": {
                "logs": logs,
                "metrics": {
                    "Target Voltage": f"{target:.1f} mV",
                    "Measured Voltage": f"{measured:.1f} mV",
                    "Deviation": f"{deviation:.2f} %",
                    "Limit": f"<= {limit} %",
                },
            },
        }
```

Language files for it — see section 9 for the full key list:

```jsonc
// cards/my_custom_test/language/en.json
{
  "name": "My Custom Voltage Test",
  "description": "Applies a target voltage and checks the measured deviation.",
  "tags": ["custom", "voltage"],
  "criteria":      { "target_voltage_mv": "Target Voltage",
                     "max_deviation_pct": "Max Allowed Deviation" },
  "criteria_help": { "target_voltage_mv": "Voltage applied to the pin under test." }
}
```

```jsonc
// cards/my_custom_test/language/ko.json
{
  "name": "사용자 전압 시험",
  "description": "목표 전압을 인가하고 측정 편차를 검사합니다.",
  "tags": ["전압", "편차", "사용자"],
  "criteria":      { "target_voltage_mv": "목표 전압",
                     "max_deviation_pct": "최대 허용 편차" }
}
```

---

## 3. File layout and discovery

```text
cards/
├── base_card.py                    # framework contract - never compiled, do not edit
├── serial_session.py               # shared serial session - do not edit
└── my_custom_test/                 # one folder per card
    ├── card_my_custom_test.py      # the card implementation
    ├── language/
    │   ├── en.json
    │   ├── ko.json
    │   ├── ja.json
    │   └── zh.json
    └── setup_guide.png             # optional image referenced by help_image_path
```

Discovery rules, all of which must hold:

| Rule | Value |
|---|---|
| Scanned root | `cards/` (with fallback to `modules/`), walked recursively |
| Filename prefix | `card_`, `test_` or `config_` |
| Extensions | `.py`, `.so`, `.pyd` (ABI-tagged forms included) |
| Class | any subclass of `BaseCard` (or `BaseTestModule`) found in the file |
| Reload | `Settings → 🔄 Reload Cards`, or restart |

A binary is loaded in preference to a same-named source → section 12.3.

---

## 4. Class contract

### 4.1 Members a module may declare

| Member | Type | Default | Purpose |
|---|---|---|---|
| `info` | dict | `{}` | Metadata. Keys in section 4.2 |
| `default_criteria` | dict | `{}` | Threshold/config schema → section 5 |
| `capabilities` | dict | `{}` | `{"needs_serial": bool, "pre_serial_cmd": bool}` |
| `actions` | dict | absent | Settings-screen buttons → section 4.3 |
| `timeout_sec` | float | absent | Step time limit; framework enforces it |
| `requires_serial` | bool | `True` | Legacy fallback for `needs_serial`. Prefer `capabilities` |
| `requires_modules` | list[str] | absent | Modules that must precede this one → section 4.4 |
| `help_image_path` | str | `""` | Setup guide image shown in help |
| `run` | method | raises | The measurement → section 6 |

### 4.2 `info` keys

| Key | Required | Purpose |
|---|---|---|
| `card_id` (or `module_id`) | **yes** | Traceability identifier. Permanent → section 10.3 |
| `version` | no | Semantic version string, e.g. `"1.0.0"` (defaults to `"1.0.0"`). Displayed on card header, palette, and CLI `--list` |
| `is_ready` | no | Boolean (defaults to `True`). Set to `False` for WIP/in-development cards that need mock simulation mode to run |
| `status` | no | `"stable"` (default) or `"wip"`. If set to `"wip"`, `is_ready` evaluates to `False` |
| `card_type` (or `module_type`) | no | `"test"` (default) or `"config"`. Falls back to the `config_` filename prefix |
| `category` | no | Grouping label |
| `icon` | no | FontAwesome name, e.g. `fa-microchip` |
| `emoji` | no | Badge shown by the Qt UI; wins over the icon mapping |
| `color` | no | Accent colour, e.g. `#3b82f6` |
| `tags` | no | list[str], or a comma-separated string. Palette search |
| `aliases` | no | list[str] of former `module_id` values → section 10.3 |

Tags are matched **in every installed language at once**, so an English UI finds
a module by a Korean tag. Search also covers name, description and `module_id`;
several words narrow with AND. Tags appear in the palette tooltip, not on the item.

### 4.3 `actions` — settings-screen buttons

```python
actions = {
    "test_connection": {
        "label": "Test Serial Connection",   # fallback; language key action_test_connection wins
        "icon": "fa-bolt",                   # optional
        "method": "test_connection",         # optional, defaults to the dict key
        "confirm": "Really run this?",       # optional, asks first
    }
}

def test_connection(self, criteria: Dict[str, Any]) -> Dict[str, Any]:
    """Must return {"ok": bool, "message": str}. The frontend chooses how to show it."""
    ok, detail = serial_registry.probe(criteria.get("port"),
                                       int(criteria.get("baudrate", 115200)))
    return {"ok": ok, "message": detail}
```

An action whose method does not exist is skipped silently.

### 4.4 Dependencies

Two mechanisms:

**Derived automatically** — `capabilities = {"needs_ppk": True}` requires a
PPK2 session owner card (`config_ppk2_interface`). Serial sessions (`needs_serial: True`)
are provided globally via the top Hardware Connection toolbar or CLI options (`--port`, `--baudrate`).

**Declared explicitly** — `requires_modules = ["firmware_flasher"]` for what
derivation cannot express.

When the framework checks:

| Moment | Behaviour |
|---|---|
| Dragged / double-clicked from the palette | Refused with an explanation; offers to add the missing card |
| Prerequisite card deleted | Lists affected cards, asks for confirmation |
| Dashboard display | Unmet cards badged `⚠️ prerequisite missing` |
| Recipe import | Reported as a validation error |
| CLI batch run | Exit 2 *before* touching hardware |

A module naming its own `port` (section 7.4) does not require the owner card — it is not
borrowing the shared session.

---

## 5. `default_criteria` schema

The settings screen is generated from this. **Write no UI code.**

### 5.1 Field shape

```python
"key_name": {
    "label": "Human Label",     # fallback; language file wins
    "value": 3300.0,            # default AND type hint when "type" is omitted
    "unit": "mV",
    "type": "number",
    # ... type-specific keys below
}
```

### 5.2 Types and their keys

| `type` | Widget | Type-specific keys |
|---|---|---|
| `text` | single-line input | `placeholder`, `monospace` |
| `number` | numeric input (int/float chosen automatically) | `min`, `max`, `decimals`, `step` |
| `bool` | checkbox | — |
| `enum` | dropdown | `options` (**required**), `option_labels`, `editable` |
| `file` | path input + browse | `accept` (list of patterns), `dialog_title` |
| `dir` | directory picker | `dialog_title` |
| `port` | serial ports actually present + rescan | `editable` |
| `ppk_device` | PPK2 devices actually present + rescan | `editable` |

Accepted on any field: `label`, `value`, `unit`, `type`, `help`, `enabled_by`
(enabled only while the named bool field is on), `section`.

`option_labels` gives an `enum` readable entries while the **stored value stays
whatever the device needs**. A DTM packet type is the integer `3`; the list
should still say `3=CONSTANT_CARRIER`:

```python
"tx_pattern": {
    "label": "TX Pattern",
    "value": 3,
    "type": "enum",
    "options": [0, 1, 2, 3],
    "option_labels": ["0=PRBS9", "1=FOUR_ONE_FOUR_ZERO",
                      "2=ONE_ZERO", "3=CONSTANT_CARRIER"],
}
```

The label is display only - recipes, `--set` and `criteria[...]` all carry the
value from `options`. A list whose length does not match `options` is ignored
rather than applied positionally, so a mismatched declaration falls back to the
raw values instead of labelling them wrongly.

`help` is rendered as rich text: wrap a phrase in `**double asterisks**` and it
appears in bold. Keep the text short - it sits under the field in the settings
dialog, and the reasoning behind a setting belongs in the docs rather than in
the operator's way.

Do **not** write a number field's range into its help. The dialog prepends it
from `min`, `max` and `step` (`800-5000 mV, step 100`), so it cannot drift from
the limits the widget enforces and does not have to be repeated in every
language file.

A field whose value stops matching what the dialog opened with is marked - the
label turns amber with a dot and the input gets an amber border - and **clicking
that label puts the value back**. This is not decoration: a scroll wheel over a
spin box or a combo changes it without anyone meaning to, and the dialog would
otherwise save that silently. Nothing is needed from a module; it applies to
every field type.

`step` sets how far the up/down arrows move. Without it they move by 1, which on
a field spanning 800-5000 makes them useless - `config_ppk2_interface` sets
`"step": 100` for the supply voltage. Typing any value in range still works.

`section` draws a horizontal rule above the field, starting a new group, with
the string as its caption (`""` for a plain rule). Use it where one settings
screen covers two different things - `ppk_dtm_tx_power` separates the DTM
stimulus from the power measurement with `"section": "POWER PROFILING"`, because
the two halves fail for unrelated reasons and an operator reads them
separately.

Omit `type` and it is inferred from `value`: `bool` → checkbox, number → numeric,
`options` present → dropdown, otherwise text.

A `file` field is **hashed into the recipe fingerprint**, so replacing the
referenced firmware changes the fingerprint. Use `file` for firmware paths.

### 5.3 Fields the framework adds — do not declare these

| Key | Type | Default | Added to |
|---|---|---|---|
| `step_repeat` | number | `1` | every module |
| `send_serial_cmd` | bool | `False` | modules with `pre_serial_cmd` |
| `serial_cmd_text` | text | `""` | modules with `pre_serial_cmd` |
| `port` | port | `""` = inherit | modules with `needs_serial` |
| `baudrate` | number | `0` = inherit | modules with `needs_serial` |
| `ppk_port` | ppk_device | `""` = inherit | modules with `needs_ppk` |

Declaring one of these yourself replaces the framework version. That is correct
for a config module that really owns the port, and wrong everywhere else.

### 5.4 Keys the framework injects into `criteria` at run time

| Key | Type | Meaning |
|---|---|---|
| `_serial` | `SerialSession` | The open shared session. Read it via `self.get_serial(criteria)`, not directly |
| `_log_callback` | callable | `log_cb(str)` for live log lines. May be absent |

Keys starting with `_` are stripped from recipes and fingerprints.

---

## 6. `run()` contract

```python
def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
```

| Parameter | Meaning |
|---|---|
| `criteria` | Merged values: defaults, then the user's settings, then framework injections (section 5.4) |
| `use_mock` | `True` simulates. **Defaults to `False`** so a caller that forgets the argument measures rather than silently simulating |

### Return shape

```python
{
    "result": "PASS",                              # "PASS" or "FAIL" - nothing else
    "execution_time_sec": 1.25,                    # float
    "summary_text": "PASS (10.2 mV < 12.0 mV)",    # one line on the card
    "details": {
        "logs": ["[INFO] ..."],                    # list[str], stored in the trace record
        "metrics": {"Measured Voltage": "10.2 mV"},# dict[str, str] - include units
        "chart": {                                 # optional, Chart.js schema
            "labels": ["Pt 1", "Pt 2"],
            "datasets": [{"label": "Voltage", "data": [9.8, 10.1]}]
        }
    }
}
```

| Key | Consumed by |
|---|---|
| `result` | Sequence verdict, yield counters, CI exit code |
| `execution_time_sec` | Card, report |
| `summary_text` | Card. Best carries both the measurement and the limit |
| `details.logs` | Trace record (JSONL), CI report, JUnit failure body |
| `details.metrics` | Report metric table, JUnit `system-out` |
| `details.chart` | Optional; a web frontend renders it unchanged |

### Failure and errors

| Situation | What to do |
|---|---|
| Measurement out of limits | Return `"result": "FAIL"` |
| Real path not implemented | `raise NotImplementedError(...)` → section 10.1 |
| No serial session | Let `SerialSessionError` propagate |
| Anything else unexpected | Raise. The framework catches it and records FAIL with the exception text |

The framework never lets an exception crash the sequence — it becomes a FAIL step
with the message in `summary_text`.

---

### 6.5 Building a variant of an existing module

A narrower version of a module - a transmitter-only cut of a TX/RX test - is a
**subclass**, not a copy. `dtm_tx_test` and `dtm_rx_test` are each about 90 lines
and inherit `dtm_runner`'s DTM translation, handle-borrowing protocol, library
log capture and verdict formatting. Copying those would be two implementations
of one contract, and they would drift.

```python
from modules.dtm_runner.test_dtm_runner import DTMBinaryRunnerModule

class DTMTxTestModule(DTMBinaryRunnerModule):
    info = {"module_id": "dtm_tx_test", ...}      # its own id and name
    default_criteria = {...}                       # only the fields that apply

    def run(self, criteria, use_mock=False):
        forced = dict(criteria)
        forced["test_mode"] = "transmitter"        # fixed, not read
        return super().run(forced, use_mock=use_mock)
```

Two things make this work:

**Force the discriminator, do not default it.** Writing the mode into the
criteria copy means a recipe carrying an old `test_mode` cannot turn a TX card
into an RX test.

**Dropping a criteria key is safe.** The parent reads criteria with
`.get(key, default)`, so a field the variant does not expose falls back to the
parent's default rather than raising.

Discovery only registers classes a file **defines**, so the imported base class
is not registered a second time from the variant's file. That check compares
`__module__` against both the file stem and the loader's module name, because a
source module and a Cython-built one report it differently.

---

## 7. Shared serial session

### 7.1 Rules

A serial port allows exactly one handle. Modules opening it individually collide
over ownership, and every open asserts DTR/RTS, which **resets the DUT** —
destroying state an earlier step established (a settled clock, DTM mode already
entered). So the framework owns the port.

| Don't | Do |
|---|---|
| `serial.Serial(port, baud)` | `session = self.get_serial(criteria)` |
| `ser.dtr = True` / `ser.rts = True` | Done once on open; policy lives on the Serial Interface card |
| `session.close()` | Never. The session spans the sequence |
| Swallow `SerialSessionError` | Let it propagate → FAIL |

```python
class MyModule(BaseTestModule):
    capabilities = {"needs_serial": True}

    def run(self, criteria, use_mock=False):
        session = self.get_serial(criteria)   # already open

        session.reset_input_buffer()
        session.write_line("start")           # newline appended

        samples = []
        for line in session.read_lines(
            timeout_sec=30.0,                 # overall limit
            idle_timeout_sec=10.0,            # unresponsive DUT
            stop=lambda: len(samples) >= 10,  # finish as soon as the goal is met
        ):
            ...
```

### `SerialSession` API

| Member | Signature |
|---|---|
| `write_line` | `(text: str, newline: str = "\r\n") -> None` |
| `read_lines` | `(timeout_sec: float, idle_timeout_sec: float = None, stop: Callable[[], bool] = None, mock_interval_sec: float = 0.15) -> Iterator[str]` |
| `read_all_lines` | `(timeout_sec: float, idle_timeout_sec: float = None) -> List[str]` |
| `reset_input_buffer` | `() -> None` |
| `feed_mock_lines` | `(lines: List[str]) -> None` — seed responses in mock mode |
| `is_open` | property → bool |
| `raw_handle` | property → the pyserial object, for a library that insists on one |
| `note_external_use` / `sync_after_external_use` | `(who: str)` / `()` — bracket a borrow of `raw_handle` |
| `open` / `close` | Framework only. Do not call |

`self.get_serial(criteria, required=True)` raises `SerialSessionError` when no
session was injected; `required=False` returns `None`.

### 7.2 Cancellation

**Poll for cancellation inside anything that takes noticeable time.** Stop Tests
is cooperative: the framework checks it between steps, and inside a step only
the module can.

```python
def run(self, criteria, use_mock=False):
    for i in range(samples):
        if self.cancelled(criteria):
            log_msg("[CANCEL] Stopped by the operator.")
            break
        ...
```

For a primitive that already takes a stop callback, hand it `cancel_check`:

```python
session.read_lines(timeout_sec=t, stop=self.cancel_check(criteria))
ppk.measure(duration_sec=d, is_cancelled=self.cancel_check(criteria))
```

Return whatever partial result makes sense, or just return - the framework turns
a cancelled step into "Stopped by user" rather than a verdict. A module that
ignores this leaves the operator watching an elapsed timer climb with a dead
button: measured before the polls were added, a 30 s step ran to completion
after Stop; after, it ended in 2.1 s.

One thing cannot be interrupted: a call that blocks inside the compiled DTM
library (`runReceiverTest`, or a transmitter test with a non-zero `runtime`).
Those return when the library decides to. Keep such runtimes short.

### 7.3 A second serial device

A companion DTM transmitter is common enough that it lives in one place:
`cards/dtm_companion.py` declares the criteria block (`companion_criteria()`)
and drives the transmitter (`start()` / `stop()`). `dtm_rx_test` and
`ppk_dtm_rx_current` both use it - two receiver tests needing the same
transmitter is exactly when to factor it out rather than let two copies drift.


The shared session is the **DUT's**. A test needing another instrument on the
same host - a DTM receiver measurement needs a companion transmitter to give the
DUT something to receive - declares its own port criteria and acquires it with:

```python
companion = self.get_extra_serial(
    port, baudrate, use_mock=use_mock, log_callback=criteria.get("_log_callback")
)
if companion is None:      # empty port -> the extra device is optional
    ...
```

This goes through the registry, not pyserial, which keeps the three guarantees
the shared session has: one handle per port however many steps address it,
release through the sequence's instrument scope, and a mock of the same shape.

Never point it at the DUT's own port. Two handles on one port fight over the
stream, and for a receiver test it would mean measuring the DUT receiving its
own transmission. `ppk_dtm_rx_current` rejects that explicitly rather than
letting it produce a plausible number.

### 7.4 Port and baud rate override

The DUT has **one** connection, and it follows the caller. Modules resolving to
the same port and baud rate share the same handle — one open, one DTR assert.
The framework adds `port` and `baudrate` override fields (section 5.3) to every
`needs_serial` module.

An override that resolves to something else **retargets** that connection:

| Override | What happens |
|---|---|
| Same port, different baud rate | The port is closed and reopened at the requested rate |
| Different port | The previous DUT session is closed, the new port opened |
| Neither, but `required_baudrate` set | The port is reopened at the rate the module requires |
| Neither (empty / 0) | The shared target is used, moving the session back if a previous step retargeted it |

A module whose protocol only works at one rate declares it instead of hoping:

```python
class MyDTMModule(BaseTestModule):
    required_baudrate = 19200      # the framework retargets before run()
```

The framework applies it when the criteria carry no explicit `baudrate`
override, so a DUT serial connection left at its 115200 default no longer
breaks the module. DTM was warning about this after the fact and then failing
with `Received less data than expected`; an explicit override still wins over
`required_baudrate`.

Retargeting is self-correcting — the next module without an override pulls the
session back — so a module never has to undo anything. It does cost a DTR/RTS
assert, which resets some boards, so every move is logged as
`[SERIAL SESSION] Re-opening ...` or `... to open ... (port override)`.

Sessions from `get_extra_serial()` (section 7.3) are exempt: they carry
`role="extra"` and stay open alongside the DUT, which is what a companion
transmitter needs.

```
module criteria name a port  →  that port (dedicated session)
they do not                  →  the shared target from the session owner
both resolve to same port    →  the same session object
```

Use an override **only when the device on the other end differs** — a second CDC
ACM, an instrument with its own interface. Naming the same DUT VCOM in every
module gains nothing: the registry merges them, and you added places to typo.

- Empty overrides are not stored in recipes, so existing fingerprints are unchanged.
- A module with its own port does not need the config card (section 4.4).
- Overridden cards are badged `⚠️ Own port: …`; the log records
  `[SERIAL] <module>: <port> @ <baud> bps (override|shared session)`.

```bash
python3 cli_runner.py --run dtm_runner \
  --set dtm_runner.port=/dev/cu.usbmodem-SECOND --set dtm_runner.baudrate=19200

python3 cli_runner.py --recipe recipes/line1.recipe.json \
  --set ppk_dtm_tx_power.port=/dev/cu.usbmodem-SECOND
```

An override on a single module is also how to test a `needs_serial` module by
itself: `--run` takes one module, so the config card cannot accompany it.

---

## 8. Shared PPK2 session

A second shared instrument, with the same ownership rule. Declare
`capabilities = {"needs_ppk": True}` and the open session arrives as
`criteria["_ppk"]`, reachable via `self.get_ppk(criteria)`. The framework then
requires a PPK2 session owner card (`config_ppk2_interface`), derived
automatically, and adds a `ppk_port` override field.

A module using both instruments declares both flags:

```python
capabilities = {"needs_serial": True, "needs_ppk": True}
```

Full API, units and traps: **[PPK2_API.md](PPK2_API.md)**.

---

## 9. Localization

One file per language. Adding a file makes that language appear in the menu.

```text
language/<lang>.json                  framework-wide strings
cards/<card_id>/language/<lang>.json    this card's strings
```

### Module file keys

| Key | Type | Purpose |
|---|---|---|
| `name` | str | Module name in the palette and on cards |
| `description` | str | One-line description |
| `tags` | list[str] | Search keywords in this language |
| `help_text` | str | Help documentation text |
| `criteria` | dict[str, str] | Label per criteria key |
| `criteria_help` | dict[str, str] | Help text per criteria key |
| `action_<key>` | str | Label for an `actions` button |

Framework files map keys straight to text; `{name}` placeholders are filled by
`language.tr("key", name=...)`.

> `tr()`'s first parameter is named `key`, so **never pass `key=`** as a
> placeholder — it raises `TypeError`. Use `field=` or another name.

Fallback: current language → `en` → the key name itself (so a gap is visible).
A missing criteria label falls back to `label` in `default_criteria`.

Module text must not go in the framework files: copying a module folder has to
bring its translations along.

The older nested format (one `translations.json` holding every language) is still
read for already-deployed modules; per-language files win on conflict. New
modules ship per-language files only.

---

## 10. Hard rules

Violating these produces silently wrong behaviour, not an error.

### 9.1 An unimplemented measurement must fail loudly

```python
if not use_mock:
    raise NotImplementedError(
        "Real hardware acquisition is not implemented yet. Enable Mock Simulation Mode."
    )
```

If both `use_mock` branches return the same value, **real-hardware mode ships a
fake PASS**. This tool is used in production. A measurement that does not exist
must stop loudly.

### 9.2 Mock defaults to off

| Place | Default |
|---|---|
| GUI `Settings → Mock Simulation Mode` | unchecked |
| `BaseTestModule.run(use_mock=...)` | `False` |
| `core.runner.run_module(use_mock=...)` | `False` |
| `SequenceContext.use_mock` | `False` |
| CLI | `False` — menu item 5 toggles it |

Operator mode locks the toggle off entirely.

### 9.3 `module_id` is permanent; rename via `aliases`

`module_id` lives in saved dashboards, recipe steps and trace records, and feeds
the recipe fingerprint. Renaming without an alias empties saved dashboards and
makes recipes fail validation with "module not installed".

```python
info = {"module_id": "dtm_runner", "aliases": ["dtm_binary_runner"]}
```

| Situation | With an alias |
|---|---|
| Dashboard load | Old-id card restored; next save writes the current id |
| Recipe import / validation | Steps with the old id pass |
| Sequence run | Old-id step runs the current module |
| Dependency check | Old names in `requires_modules` resolve |

**The fingerprint keeps the old name** — otherwise a recipe recorded under the old
id could no longer be matched against past trace records. An alias makes a recipe
runnable; it does not rewrite history.

Display names are free to change — they are not identifiers.

### 9.4 No `module_id` in framework code

The core must never branch on a specific module. Everything is driven by declared
capabilities, `module_type` and the criteria schema. If a feature seems to need
`if module_id == ...`, add a capability or a schema field instead.

---

## 11. Verify

```bash
# 1. Syntax
python3 -m py_compile cards/my_custom_test/card_my_custom_test.py

# 2. The module is discovered, and its schema is well-formed
python3 cli_runner.py --list
python3 cli_runner.py --list --json | python3 -m json.tool > /dev/null

# 3. Run it in simulation
python3 cli_runner.py --run my_custom_test --mock

# 4. Real-hardware mode fails loudly while unimplemented (expect exit 1, not PASS)
python3 cli_runner.py --run my_custom_test ; echo "exit=$?"

# 5. Translations complete
python3 tools/check_language.py

# 6. GUI loads it
python3 gui_app.py
```

A `needs_serial` module cannot be run alone by `--run`: it requires the Serial
Interface Config card, and `--run` takes a single module. Either give it its own
port, or run it from a recipe:

```bash
python3 cli_runner.py --run my_custom_test --mock --set my_custom_test.port=/dev/MOCK
python3 cli_runner.py --recipe recipes/line1.recipe.json --mock
```

Checklist before declaring a module done:

- [ ] Folder and filename match section 3
- [ ] `info["module_id"]` set, and equal to the folder name
- [ ] `capabilities["needs_serial"]` reflects whether the DUT VCOM is used
- [ ] `default_criteria` declares no framework field (section 5.3)
- [ ] `run()` returns every key in section 6
- [ ] `use_mock=False` path raises or really measures — never mirrors the mock branch
- [ ] No `serial.Serial`, no `session.close()`
- [ ] `language/en.json` plus every other language present in `language/`
- [ ] No hardcoded display string
- [ ] `tools/check_language.py` reports no missing keys

---

## 12. Binary distribution (`.so` / `.pyd`)

### 11.1 Targets

| Folder | Contents |
|---|---|
| `library/` | shared device libraries (the DTM implementation) |
| `cards/` | test and config cards (`card_*.py`, `test_*.py`, `config_*.py`), subfolders included |

`base_module.py` and `serial_session.py` are **not compiled** — they are the
contract modules import, and source helps debugging.

```bash
pip install -r requirements-build.txt        # cython, setuptools

python3 build_binaries.py                    # everything
python3 build_binaries.py --targets library  # one target
python3 build_binaries.py --list             # targets and state
python3 build_binaries.py --check-stale       # exit 1 if sources are newer
python3 build_binaries.py --clean             # remove binaries and .c
python3 build_binaries.py --collect           # gather into dist/<platform>/
python3 build_binaries.py --strip-sources     # drop .py from the distribution
```

### 11.2 Cross-compilation is impossible

A C extension differs per **(OS × CPU architecture × Python version)**. The
extension suffix comes from the running Python:

| Build host | Produces |
|---|---|
| macOS | `dtm.cpython-310-darwin.so` |
| Linux | `dtm.cpython-310-x86_64-linux-gnu.so` |
| Windows | `dtm.cp310-win_amd64.pyd` |

Binaries for several platforms may share one folder — the ABI tag means each
Python loads only its own and ignores the rest. A `.py` source in the same folder
is the fallback when no binary matches.

### 11.3 A binary shadows the source

When a `.so`/`.pyd` exists it is loaded and the `.py` is ignored, so **editing a
source without rebuilding runs the old code silently.**

```bash
python3 build_binaries.py --check-stale   # detect it (CI-friendly, exit 1)
python3 build_binaries.py --clean         # while developing, work from source
```

### 11.4 Naming rule

A C extension loader looks for `PyInit_<module name>`, so the extension must be
loaded under the name it was compiled with:

| File | Loaded as |
|---|---|
| `test_x.py` | `ext_mod_test_x` (avoids a top-level clash) |
| `test_x.cpython-310-darwin.so` | `test_x` (no prefix — required) |

`build_binaries.py` passes a dotted extension name
(`modules.gpio_short_test.test_gpio_short`) so artefacts land next to their
source. The last component decides the `PyInit_` symbol.

### 11.5 Using `library/`

The DTM implementation imports its sibling by top-level name (`import dtm_common`),
the way the original Nordic code does. `library/__init__.py` prepares `sys.path`,
so source and binary behave identically.

```python
from library import DTM, dtm_common, is_binary_build, build_info
print(build_info())        # which implementation was loaded
```

Full DTM API — call sequence, the 23-key setup dict, return shapes, borrowing the
shared session: **[LIBRARY_DTM_API.md](LIBRARY_DTM_API.md)**.

---

## 13. Appendix — full `BaseTestModule` surface

Inherited; a module rarely needs more than `get_serial` and `run`.

| Member | Signature / type | Notes |
|---|---|---|
| `run` | `(criteria: dict, use_mock: bool = False) -> dict` | Override this |
| `get_serial` | `(criteria: dict, required: bool = True) -> SerialSession \| None` | The shared session |
| `get_extra_serial` | `(port, baudrate=19200, use_mock=False, log_callback=None, assert_dtr_rts=True) -> SerialSession \| None` | An **additional** device beyond the DUT (section 7.3). Empty port returns `None` |
| `get_ppk` | `(criteria: dict, required: bool = True) -> PPKSession \| None` | The shared PPK2 session |
| `card_id` / `module_id` | property → str | From `info` |
| `card_type` / `module_type` | property → str | `"test"` / `"config"` |
| `priority` | int | Execution priority / config card sorting order (default 100, config cards 10~30) |
| `needs_serial` | property → bool | From `capabilities`, falling back to `requires_serial` |
| `required_baudrate` | int \| None | Baud rate this module's protocol needs; the framework retargets the session to it (section 7.4) |
| `allows_pre_serial_cmd` | property → bool | From `capabilities["pre_serial_cmd"]` |
| `aliases` | property → list[str] | Former card / module ids |
| `tags` | property → list[str] | From `info["tags"]` |
| `localized_tags` | `(lang: str = "en") -> list[str]` | Tags for one language, English included |
| `all_tags` | `() -> list[str]` | Tags across every language — what search uses |
| `matches_search` | `(query: str, lang: str = "en") -> bool` | Palette filter |
| `get_localized` | `(field: str, lang: str = "en") -> str` | `name`, `description`, … |
| `get_criteria_label` | `(key: str, lang: str = "en") -> str` | Falls back to `default_criteria[key]["label"]` |
| `get_criteria_help` | `(key: str, lang: str = "en") -> str` | Per-field help |
| `get_tr` | `(key: str, lang: str = "en", default: str = "") -> str` | Any key from the card's language file |
| `get_info` | `(lang: str = "en") -> dict` | Localized metadata |
| `get_instrument` | `(key: str, criteria: dict, required: bool = True) -> Any` | Generic instrument retriever |
| `icon` / `color` | property → str | From `info` |
| `translations` | dict | Loaded automatically at construction |
| `load_translations` | `()` | Called by `__init__`; no need to call |

---

## 14. AI Assistant Guidelines & Instrument Extensibility

### 14.1 Can an AI easily create a new Card?
**Yes, absolutely.** The framework's architecture is specifically engineered to be modular and decoupled:
1. **Automated Scaffolding**: Run `python3 tools/create_card.py <card_id> [options]` to generate the Python class, directory structure, language dictionaries, and setup guide template in 1 second.
2. **Zero UI Code**: Criteria forms, inputs, validations, labels, and help tooltips are generated automatically by the Qt/Web engine from `default_criteria`. The author never writes UI layout or widget code.
3. **Multi-language Standard**: Language definitions live in `language/en.json`, `ko.json`, and `ja.json`. Keys match `default_criteria` field names.
4. **Mock Simulation Mode**: Every card must implement `use_mock=True` simulation branch. This allows full testing and CI verification without requiring physical hardware connected to the computer.
5. **No Registration Boilerplate**: Any folder placed in `cards/` with a subclass of `BaseCard` is automatically discovered and listed in the GUI card palette and CLI runner.

### 14.2 Prompting an AI to Create a New Card
When prompting an AI to create a new card, provide this concise prompt specification:
```text
Create a new test card for <MEASUREMENT_PURPOSE> with card_id "<card_id>".
1. Scaffold using: python3 tools/create_card.py <card_id> --category "<CATEGORY>" --emoji "<EMOJI>"
2. Define default_criteria with appropriate types (number, text, enum, etc.) and validation rules.
3. In run(self, criteria, use_mock=False):
   - Handle use_mock=True with realistic simulated telemetry and pass/fail logic.
   - For real hardware, interact with the shared serial/instrument session.
   - Return standard dict: {"result": "PASS"|"FAIL", "execution_time_sec": float, "summary_text": str, "details": dict}
4. Provide translations for criteria in language/en.json, ko.json, and ja.json.
5. Verify using:
   - python3 cli_runner.py --list
   - python3 cli_runner.py --run <card_id> --mock
   - python3 tools/check_language.py
```

### 14.3 Adding New Hardware Instruments (Decoupled Registry)
To add a new hardware instrument (e.g. J-Link programmer, Digital Multimeter, Programmable Power Supply, Relay Matrix):
1. **Define the Instrument Provider** in `core/dependencies.py`:
   ```python
   from core.dependencies import register_instrument_provider, InstrumentProviderSpec

   register_instrument_provider(
       InstrumentProviderSpec(
           name="jlink",
           card_id="config_jlink",
           display_names={
               "en": "J-Link Interface",
               "ko": "J-Link 인터페이스",
               "ja": "J-Linkインターフェース",
           },
           config_priority=15,
           default_criteria_keys=["jlink_serial", "target_device"],
       )
   )
   ```
2. **Create the Config Card**:
   Create `cards/config_jlink/config_config_jlink.py` inheriting from `BaseCard` with `card_type = "config"`, `priority = 15`, and `provides_instrument = "jlink"`.
3. **Declare Dependency in Test Cards**:
   In any test card that requires this instrument:
   ```python
   capabilities = {
       "needs_instrument": "jlink",  # or requires_cards = ["config_jlink"]
   }
   ```
4. **Automatic GUI & CLI Integration**:
   - The GUI automatically detects missing instrument cards on the dashboard and prompts the user in their selected language: *"Card X requires J-Link Interface. Would you like to add it?"*
   - Config cards are automatically ordered at the top according to their `priority` (PPK=10, J-Link=15, Serial=20).
   - Core runner automatically injects the initialized instrument session into `criteria["_jlink"]` for test cards to use.
