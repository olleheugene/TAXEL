"""
criteria schema normalisation.

Turns what a module declares in default_criteria into "form field specs". A
frontend builds widgets from those specs alone, so the core never needs to know
anything about a specific module.

Previously gui_app.on_settings_clicked branched on module_id with if/elif to
pick a module-specific dialog, which meant a new module wanting its own UI
required a core change. Now the module declares a type and the core just
renders it.

Supported types:
  text    single-line string
  number  numeric (min / max / decimals / step)
  bool    checkbox
  enum    pick from a fixed list (options, editable)
  file    file path + browse (accept, dialog_title)
  dir     directory path + browse
  port    serial port picker (populated with the ports actually present, editable)
  ppk_device  PPK2 picker (populated from the attached PPK2s, rescan button, editable)

Existing or third-party modules that declare no type get one inferred from the
Python type of value, so extending the contract is backwards compatible.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.language import language
from cards.base_card import BaseCard, BaseTestModule

FIELD_TYPES = ("text", "number", "bool", "enum", "file", "dir", "port", "ppk_device")

# Fields the framework offers to every module, or to those matching a gate.
#   gate="serial"       -> only modules with allows_pre_serial_cmd
#   gate="needs_serial" -> only modules with needs_serial
#   gate="needs_ppk"    -> only modules with needs_ppk
#   gate=None           -> every module
#
# A module that declares a key itself keeps its own definition: the Serial
# Interface Config card owns "port" and "baudrate" for real, so it never gets
# the override versions below.
FRAMEWORK_FIELDS: Dict[str, Dict[str, Any]] = {
    "step_repeat": {
        "label": "Repeat count for this card",
        "value": 1,
        "unit": "times",
        "type": "number",
        "min": 1,
        "max": 10000,
        "decimals": 0,
        "label_key": "field_step_repeat",
        "unit_key": "field_step_repeat_unit",
        "help_key": "field_step_repeat_help",
        "gate": None,
        "framework": True,
    },
    "send_serial_cmd": {
        "gate": "serial",
        "label": "Send serial command before execution",
        "value": False,
        "unit": "",
        "type": "bool",
        "label_key": "lbl_send_serial_enable",
        "framework": True,
    },  # noqa: E501
    "serial_cmd_text": {
        "gate": "serial",
        "label": "Serial command",
        "value": "",
        "unit": "",
        "type": "text",
        "placeholder": "e.g. lfxo_drift_check",
        "monospace": True,
        "enabled_by": "send_serial_cmd",
        "label_key": "lbl_send_serial_cmd",
        "framework": True,
    },
    # Per-module serial target override. Empty means "inherit the shared
    # session" - the normal case, and the reason the default is blank rather
    # than a port name: a filled-in default would silently split one DUT into
    # two sessions. Filling it in points this one step at a different device.
    "port": {
        "gate": "needs_serial",
        "label": "Serial port override",
        "value": "",
        "unit": "",
        "type": "port",
        "label_key": "field_port_override",
        "help_key": "field_port_override_help",
        "framework": True,
    },
    # Per-module PPK2 override, the counterpart of the serial one. Empty means
    # "inherit the shared PPK2 session"; filling it in points this one step at a
    # different instrument. The config card declares ppk_port itself, so it keeps
    # its own device list rather than this plain text field.
    "ppk_port": {
        "gate": "needs_ppk",
        "label": "PPK2 device override",
        "value": "",
        "unit": "",
        "type": "text",
        "placeholder": "e.g. /dev/cu.usbmodem-PPK2",
        "label_key": "field_ppk_port_override",
        "help_key": "field_ppk_port_override_help",
        "framework": True,
    },
    "baudrate": {
        "gate": "needs_serial",
        "label": "Baud rate override",
        "value": 0,
        "unit": "bps",
        "type": "number",
        "min": 0,
        "max": 4000000,
        "decimals": 0,
        "label_key": "field_baudrate_override",
        "unit_key": "field_baudrate_override_unit",
        "help_key": "field_baudrate_override_help",
        "framework": True,
    },
}


@dataclass
class FieldSpec:
    """Spec for one form field. A frontend builds its widget from this alone."""

    key: str
    type: str
    label: str
    value: Any
    unit: str = ""
    options: List[Any] = field(default_factory=list)
    # Display text for `options`, positionally. Lets an enum show a readable
    # name while storing the value the device protocol actually needs - a DTM
    # packet type is the integer 3, but "3=CONSTANT_CARRIER" is what a person
    # can pick from. Ignored unless it is exactly as long as `options`, so a
    # mismatched declaration degrades to the raw values instead of mislabelling
    # them, which on a test limit would be worse than being terse.
    option_labels: List[str] = field(default_factory=list)
    editable: bool = False
    accept: List[str] = field(default_factory=list)
    dialog_title: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    decimals: Optional[int] = None
    step: Optional[float] = None
    placeholder: str = ""
    monospace: bool = False
    enabled_by: Optional[str] = None
    added_by: Optional[str] = None
    add_button_text: str = ""
    help_text: str = ""
    framework: bool = False
    # When set, the frontend draws a horizontal rule with this caption above the
    # field, starting a new group. Empty string = a plain rule with no caption.
    # None = no rule. Grouping lives here rather than in the frontend because
    # only the module knows which of its settings belong together.
    section: Optional[str] = None
    rescan: bool = False
    rescan_fn: Optional[Any] = None

    @property
    def label_with_unit(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


def _localized(translator, key: Optional[str], fallback: str) -> str:
    """Use the translation when a key is given; fall back to the literal when
    there is no key or the translator echoes the key back."""
    if not key:
        return fallback
    try:
        text = translator(key)
    except Exception:
        return fallback
    return text if text and text != key else fallback


def _infer_type(value: Any, spec: Dict[str, Any]) -> str:
    """Inference for modules that declare no type. Backwards-compatible path."""
    if spec.get("options"):
        return "enum"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def _coerce(value: Any, field_type: str) -> Any:
    try:
        if field_type == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if field_type == "number":
            num = float(value)
            return int(num) if num.is_integer() else num
        if field_type in ("text", "file", "dir", "port", "ppk_device"):
            return "" if value is None else str(value)
    except (TypeError, ValueError):
        pass
    return value


def _current_value(key: str, declared_default: Any, current: Dict[str, Any]) -> Any:
    """Stored criteria usually hold the plain value, but a schema dict shows up
    in older saved data too."""
    if key not in current:
        return declared_default
    val = current[key]
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    return val


def normalize_schema(
    module,
    current_criteria: Optional[Dict[str, Any]] = None,
    lang: str = "en",
    include_framework_fields: bool = True,
    translator=None,
) -> List[FieldSpec]:
    """
    Convert a module's default_criteria into a list of FieldSpec.

    :param current_criteria: values currently stored on the card (declared
                             defaults are used when absent)
    :param translator:       tr(key) function for framework-provided labels
    """
    current = dict(current_criteria or {})
    specs: List[FieldSpec] = []

    declared = dict(getattr(module, "default_criteria", {}) or {})

    # Framework-provided fields. A gate restricts them to matching modules.
    if include_framework_fields:
        allows_serial_cmd = bool(getattr(module, "allows_pre_serial_cmd", False))
        needs_serial = bool(getattr(module, "needs_serial", False))
        needs_ppk = bool(getattr(module, "needs_ppk", False))
        for key, meta in FRAMEWORK_FIELDS.items():
            if key in declared:
                continue
            if meta.get("gate") == "serial" and not allows_serial_cmd:
                continue
            if meta.get("gate") == "needs_serial" and not needs_serial:
                continue
            if meta.get("gate") == "needs_ppk" and not needs_ppk:
                continue
            tr = translator or language.tr
            mod_label = ""
            mod_help = ""
            if hasattr(module, "get_criteria_label"):
                try:
                    custom_lbl = module.get_criteria_label(key, lang)
                    if custom_lbl and custom_lbl != key:
                        mod_label = custom_lbl
                except Exception:
                    pass
            if hasattr(module, "get_criteria_help"):
                try:
                    custom_help = module.get_criteria_help(key, lang)
                    if custom_help:
                        mod_help = custom_help
                except Exception:
                    pass

            specs.append(
                FieldSpec(
                    key=key,
                    type=meta["type"],
                    label=mod_label or _localized(tr, meta.get("label_key"), meta["label"]),
                    value=_coerce(_current_value(key, meta["value"], current), meta["type"]),
                    unit=_localized(tr, meta.get("unit_key"), meta.get("unit", "")),
                    minimum=meta.get("min"),
                    maximum=meta.get("max"),
                    decimals=meta.get("decimals"),
                    placeholder=meta.get("placeholder", ""),
                    monospace=meta.get("monospace", False),
                    enabled_by=meta.get("enabled_by"),
                    help_text=mod_help or _localized(tr, meta.get("help_key"), meta.get("help", "")),
                    framework=True,
                )
            )

    for key, spec in declared.items():
        if not isinstance(spec, dict):
            # Allow the shorthand form where only a value is declared.
            spec = {"label": key, "value": spec, "unit": ""}

        declared_default = spec.get("value")
        value = _current_value(key, declared_default, current)
        field_type = str(spec.get("type") or _infer_type(declared_default, spec)).lower()
        if field_type not in FIELD_TYPES:
            field_type = _infer_type(declared_default, spec)

        try:
            label = module.get_criteria_label(key, lang)
        except Exception:
            label = spec.get("label", key)

        # Help text goes through the module translations too: a literal baked
        # into module code stays in one language no matter what the user picks.
        try:
            help_text = module.get_criteria_help(key, lang)
        except Exception:
            help_text = str(spec.get("help", "") or "")

        raw_options = spec.get("options", [])
        rescan_fn = spec.get("rescan_fn")
        if callable(raw_options):
            if rescan_fn is None:
                rescan_fn = raw_options
            try:
                nrf_path = current.get("nrfutil_path")
                try:
                    raw_options = raw_options(nrf_path)
                except TypeError:
                    raw_options = raw_options()
            except Exception:
                raw_options = []
        options = list(raw_options or [])

        option_labels = [str(t) for t in (spec.get("option_labels") or [])]
        if len(option_labels) != len(options):
            option_labels = []
        # Keep a stored value that is no longer in the list, rather than losing it.
        if field_type == "enum" and value is not None and value not in options:
            options.append(value)
            # The appended value has no declared label; label it with itself so
            # the two lists stay the same length and the labels stay usable.
            if option_labels:
                option_labels.append(str(value))

        specs.append(
            FieldSpec(
                key=key,
                type=field_type,
                label=label,
                value=_coerce(value, field_type),
                unit=str(spec.get("unit", "") or ""),
                options=options,
                option_labels=option_labels,
                editable=bool(spec.get("editable", field_type == "port")),
                accept=list(spec.get("accept", []) or []),
                dialog_title=str(spec.get("dialog_title", "") or ""),
                minimum=spec.get("min"),
                maximum=spec.get("max"),
                decimals=spec.get("decimals"),
                step=spec.get("step"),
                placeholder=str(spec.get("placeholder", "") or ""),
                monospace=bool(spec.get("monospace", False)),
                enabled_by=spec.get("enabled_by"),
                added_by=spec.get("added_by"),
                add_button_text=str(spec.get("add_button_text", "") or ""),
                help_text=help_text,
                section=spec.get("section"),
                rescan=bool(spec.get("rescan", False) or rescan_fn is not None),
                rescan_fn=rescan_fn,
            )
        )

    return specs


def default_values(module) -> Dict[str, Any]:
    """Build a criteria dict from the defaults a module declares."""
    values = {}
    for key, spec in (getattr(module, "default_criteria", {}) or {}).items():
        values[key] = spec.get("value") if isinstance(spec, dict) else spec
    return values


def describe_actions(module, lang: str = "en") -> List[Dict[str, Any]]:
    """
    Return the actions a module declares (buttons on its settings screen).

    Declaration form:
        actions = {
            "test_connection": {
                "label": "Test Serial Connection",
                "icon": "fa-bolt",
                "method": "test_connection",   # defaults to the key
            }
        }

    The "test connection" button used to be hard-coded into a core dialog.
    Now any module can have buttons simply by declaring them.
    """
    described = []
    for key, meta in (getattr(module, "actions", {}) or {}).items():
        meta = meta if isinstance(meta, dict) else {}
        method_name = meta.get("method", key)
        if not callable(getattr(module, method_name, None)):
            continue
        label = meta.get("label", key.replace("_", " ").title())
        try:
            localized = module.get_tr(f"action_{key}", lang, default="")
            if localized and localized != f"action_{key}":
                label = localized
        except Exception:
            pass
        described.append(
            {
                "key": key,
                "label": label,
                "icon": meta.get("icon", ""),
                "method": method_name,
                "confirm": meta.get("confirm", ""),
            }
        )
    return described
