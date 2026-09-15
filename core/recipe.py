"""
Recipe - a versioned, lockable test sequence.

dashboard_layout.json only ever answered "what is on screen right now". It was
editable at any moment and carried no version, so there was no way to prove
afterwards which board had been tested with which settings.

A recipe replaces that:
  - bundles the sequence, its criteria and the hashes of referenced files
  - is identified by name / version / fingerprint
  - locked=True prevents operators from editing it
  - exports to and imports from a single .json file (distribution across stations)

The fingerprint answers "is this recipe exactly this content". It covers the
sequence, the criteria and the firmware file hashes, so swapping just the
firmware file changes it. Recording that fingerprint in the trace log turns
"tested with recipe v1.3" into a verifiable claim.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.dependencies import check_dependencies
from core.language import language
from core.registry import get_module

RECIPE_SCHEMA_VERSION = 1
RECIPE_FILE_SUFFIX = ".recipe.json"

# Keys the framework injects into criteria. They are not part of a module's
# schema, so validation must not flag them as unknown.
STEP_REPEAT_KEY = "step_repeat"
FRAMEWORK_CRITERIA_KEYS = (
    "send_serial_cmd", "serial_cmd_text", STEP_REPEAT_KEY,
    # Per-module serial target override (core.schema.FRAMEWORK_FIELDS).
    "port", "baudrate", "ppk_port",
)

# Override keys whose empty value means "inherit". They are dropped when a
# recipe is built so that a step which overrides nothing produces exactly the
# criteria - and therefore exactly the fingerprint - it did before the override
# fields existed. Only a deliberate override is recorded.
INHERITED_WHEN_EMPTY_KEYS = ("port", "baudrate", "ppk_port")

# criteria types whose files are hashed: changing a referenced file must change
# the fingerprint.
HASHED_FIELD_TYPES = ("file",)


def _sha256_file(path: str, chunk_size: int = 1 << 16) -> Optional[Dict[str, Any]]:
    """File hash. None when the file is missing, which import validation
    reports as such."""
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    total = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return {
        "name": os.path.basename(path),
        "sha256": digest.hexdigest(),
        "size": total,
    }


def _canonical(obj: Any) -> str:
    """Canonical JSON for fingerprinting - must not depend on key order or
    whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


@dataclass
class RecipeStep:
    """One step of the sequence, corresponding to a single dashboard card."""

    module_id: str
    criteria: Dict[str, Any] = field(default_factory=dict)
    step_id: str = ""
    enabled: bool = True
    timeout_sec: Optional[float] = None
    # Repeat only this step. Distinct from a sequence repeat: a sequence repeat
    # tests the DUT again from the beginning, while a step repeat measures the
    # same item several times in place (to check spread, for example).
    repeat: int = 1
    # Hashes of referenced files, computed from criteria fields of type "file".
    artifacts: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.step_id:
            self.step_id = f"step_{uuid.uuid4().hex[:8]}"

    def public_criteria(self) -> Dict[str, Any]:
        """Everything except the framework-injected keys (_serial, _log_callback)."""
        return {k: v for k, v in self.criteria.items() if not k.startswith("_")}

    @property
    def effective_repeat(self) -> int:
        """Honour the framework field in criteria when present."""
        raw = self.criteria.get(STEP_REPEAT_KEY, self.repeat)
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return 1

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "module_id": self.module_id,
            "criteria": self.public_criteria(),
            "enabled": self.enabled,
            "timeout_sec": self.timeout_sec,
            "repeat": self.effective_repeat,
            "artifacts": {k: v.get("sha256") for k, v in sorted(self.artifacts.items())},
        }


@dataclass
class Recipe:
    """A versioned test sequence."""

    name: str = "Untitled Recipe"
    version: str = "0.1"
    steps: List[RecipeStep] = field(default_factory=list)
    locked: bool = False
    notes: str = ""
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    schema_version: int = RECIPE_SCHEMA_VERSION
    # Recorded at export time. Not part of the fingerprint.
    exported_at: str = ""
    exported_from: str = ""

    # ----------------------------------------------------------- fingerprint
    def fingerprint(self) -> str:
        """
        Content fingerprint. Excludes name / version / notes / timestamps and
        covers only what affects execution. If renaming a recipe changed the
        fingerprint, the trace records would lose their meaning.
        """
        payload = {
            "schema_version": self.schema_version,
            "steps": [s.fingerprint_payload() for s in self.steps],
        }
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    @property
    def short_fingerprint(self) -> str:
        return self.fingerprint()[:12]

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}"

    # ---------------------------------------------------------- serialisation
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["steps"] = [
            {**asdict(s), "criteria": s.public_criteria()} for s in self.steps
        ]
        data["fingerprint"] = self.fingerprint()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Recipe":
        steps = []
        for raw in data.get("steps", []) or []:
            steps.append(
                RecipeStep(
                    module_id=str(raw.get("module_id", "")),
                    criteria=dict(raw.get("criteria", {}) or {}),
                    step_id=str(raw.get("step_id", "") or ""),
                    enabled=bool(raw.get("enabled", True)),
                    timeout_sec=raw.get("timeout_sec"),
                    repeat=max(1, int(raw.get("repeat", 1) or 1)),
                    artifacts=dict(raw.get("artifacts", {}) or {}),
                )
            )
        return cls(
            name=str(data.get("name", "Untitled Recipe")),
            version=str(data.get("version", "0.1")),
            steps=steps,
            locked=bool(data.get("locked", False)),
            notes=str(data.get("notes", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
            created_by=str(data.get("created_by", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
            schema_version=int(data.get("schema_version", RECIPE_SCHEMA_VERSION)),
            exported_at=str(data.get("exported_at", "") or ""),
            exported_from=str(data.get("exported_from", "") or ""),
        )

    def save(self, path: str, timestamp: str = "") -> str:
        data = self.to_dict()
        if timestamp:
            data["updated_at"] = timestamp
            self.updated_at = timestamp
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> "Recipe":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def export(self, path: str, timestamp: str = "", station: str = "") -> str:
        """
        Export to a single file for distribution across stations.
        The content fingerprint is written alongside, so the receiving side can
        detect tampering.
        """
        if not path.endswith(".json"):
            path += RECIPE_FILE_SUFFIX
        data = self.to_dict()
        data["exported_at"] = timestamp
        data["exported_from"] = station
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path


# ------------------------------------------------------------------ creation
def recipe_from_cards(
    cards: List[Tuple[str, Dict[str, Any]]],
    modules: Dict[str, Any],
    name: str = "Untitled Recipe",
    version: str = "0.1",
    timestamp: str = "",
    author: str = "",
) -> Recipe:
    """
    Build a recipe from a list of (module_id, criteria) - used when creating one
    from the dashboard cards. criteria of type "file" are hashed into artifacts.
    """
    steps = []
    for module_id, criteria in cards:
        module = get_module(modules, module_id)
        declared = set(getattr(module, "default_criteria", {}) or {}) if module else set()
        step = RecipeStep(module_id=module_id, criteria={
            k: v for k, v in (criteria or {}).items()
            if not k.startswith("_")
            # An empty override means "inherit"; storing it would change the
            # fingerprint of every existing recipe for no behavioural reason.
            # A module that declares the key itself (the config card's real
            # "port") keeps it even when blank.
            and not (k in INHERITED_WHEN_EMPTY_KEYS and k not in declared and not v)
        })
        step.repeat = step.effective_repeat
        step.artifacts = compute_artifacts(module, step.criteria)
        steps.append(step)

    return Recipe(
        name=name,
        version=version,
        steps=steps,
        created_at=timestamp,
        created_by=author,
        updated_at=timestamp,
    )


def compute_artifacts(module, criteria: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Hash the files referenced by criteria fields of type "file", so that
    replacing a firmware file changes the recipe fingerprint.
    """
    artifacts: Dict[str, Dict[str, Any]] = {}
    if module is None:
        return artifacts

    declared = getattr(module, "default_criteria", {}) or {}
    for key, spec in declared.items():
        if not isinstance(spec, dict):
            continue
        if str(spec.get("type", "")).lower() not in HASHED_FIELD_TYPES:
            continue
        path = str(criteria.get(key, spec.get("value", "")) or "")
        info = _sha256_file(path)
        if info is not None:
            artifacts[key] = {**info, "path": path}
        else:
            # Record the fact that the file was missing. Dropping it silently
            # would silently change the fingerprint.
            artifacts[key] = {"name": os.path.basename(path), "path": path, "sha256": "", "missing": True}
    return artifacts


# ---------------------------------------------------------------- validation
@dataclass
class ValidationReport:
    """The outcome of an import check, itemised so nothing passes silently."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_modules: List[str] = field(default_factory=list)
    unknown_criteria: List[str] = field(default_factory=list)
    artifact_mismatches: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def clean(self) -> bool:
        return not self.errors and not self.warnings

    def summary(self) -> str:
        if self.clean:
            return language.tr("validate_clean")
        parts = []
        if self.errors:
            parts.append(language.tr("validate_errors", count=len(self.errors)))
        if self.warnings:
            parts.append(language.tr("validate_warnings", count=len(self.warnings)))
        return " / ".join(parts)

    def as_text(self) -> str:
        lines = []
        for e in self.errors:
            lines.append(f"❌ {e}")
        for w in self.warnings:
            lines.append(f"⚠️ {w}")
        return "\n".join(lines) if lines else language.tr("validate_no_problem")


def validate_recipe(
    recipe: Recipe,
    modules: Dict[str, Any],
    verify_artifacts: bool = True,
    declared_fingerprint: str = "",
) -> ValidationReport:
    """
    Check whether a recipe can run on this station.

    Checks:
      - schema version
      - every referenced module is installed here
      - every criteria key exists in the module schema
      - referenced files exist and their hashes match
      - the fingerprint recorded in the file matches the actual content
        (tamper detection)
    """
    report = ValidationReport()

    if recipe.schema_version > RECIPE_SCHEMA_VERSION:
        report.errors.append(
            language.tr("validate_schema_too_new",
                    found=recipe.schema_version, supported=RECIPE_SCHEMA_VERSION)
        )

    if not recipe.steps:
        report.warnings.append(language.tr("validate_no_steps"))

    if declared_fingerprint:
        actual = recipe.fingerprint()
        if declared_fingerprint != actual:
            report.errors.append(
                language.tr("validate_fp_mismatch",
                        declared=declared_fingerprint[:12], actual=actual[:12])
            )

    # Prerequisite check: only preceding steps count as "already present", so a
    # mis-ordered recipe (DTM before Serial Config) is caught too.
    seen_ids: List[str] = []
    for idx, step in enumerate(recipe.steps, 1):
        module = get_module(modules, step.module_id)
        if module is not None:
            dep = check_dependencies(module, seen_ids, modules)
            for requirement in dep.unmet:
                report.errors.append(
                    language.tr("validate_dep_missing",
                            index=idx, module=step.module_id, reason=requirement.short())
                )
        seen_ids.append(step.module_id)

    for idx, step in enumerate(recipe.steps, 1):
        module = get_module(modules, step.module_id)
        if module is None:
            report.missing_modules.append(step.module_id)
            report.errors.append(
                language.tr("validate_module_missing", index=idx, module=step.module_id)
            )
            continue

        declared_keys = set((getattr(module, "default_criteria", {}) or {}).keys())
        framework_keys = set(FRAMEWORK_CRITERIA_KEYS)
        for key in step.public_criteria():
            if key not in declared_keys and key not in framework_keys:
                report.unknown_criteria.append(f"{step.module_id}.{key}")
                report.warnings.append(
                    # "field", not "key": tr()'s own first parameter is named
                    # key, so passing key= raises TypeError.
                    language.tr("validate_unknown_criteria",
                            index=idx, module=step.module_id, field=key)
                )

        if not verify_artifacts:
            continue

        current = compute_artifacts(module, step.criteria)
        for key, recorded in step.artifacts.items():
            now = current.get(key, {})
            if recorded.get("missing") or not recorded.get("sha256"):
                report.warnings.append(
                    language.tr("validate_artifact_unrecorded",
                            index=idx, module=step.module_id, field=key,
                            name=recorded.get("name") or language.tr("validate_unspecified"))
                )
                continue
            if now.get("missing") or not now.get("sha256"):
                report.artifact_mismatches.append(f"{step.module_id}.{key}")
                report.errors.append(
                    language.tr("validate_artifact_not_found",
                            index=idx, module=step.module_id,
                            path=recorded.get("path") or recorded.get("name") or "")
                )
            elif now["sha256"] != recorded["sha256"]:
                report.artifact_mismatches.append(f"{step.module_id}.{key}")
                report.errors.append(
                    language.tr("validate_artifact_mismatch",
                            index=idx, module=step.module_id, name=recorded.get("name"),
                            expected=recorded["sha256"][:12], actual=now["sha256"][:12])
                )

    return report


def import_recipe(
    path: str,
    modules: Dict[str, Any],
    verify_artifacts: bool = True,
) -> Tuple[Optional[Recipe], ValidationReport]:
    """
    Read and validate a recipe file. On error the recipe is still returned but
    report.ok is False; whether to apply it is the frontend's decision.
    """
    report = ValidationReport()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        report.errors.append(language.tr("validate_file_not_found", path=path))
        return None, report
    except json.JSONDecodeError as e:
        report.errors.append(language.tr("validate_json_error", error=e))
        return None, report
    except Exception as e:
        report.errors.append(language.tr("validate_read_error", error=e))
        return None, report

    if not isinstance(data, dict):
        report.errors.append(language.tr("validate_not_object"))
        return None, report

    declared_fp = str(data.get("fingerprint", "") or "")
    recipe = Recipe.from_dict(data)
    sub = validate_recipe(recipe, modules, verify_artifacts, declared_fingerprint=declared_fp)

    report.errors.extend(sub.errors)
    report.warnings.extend(sub.warnings)
    report.missing_modules.extend(sub.missing_modules)
    report.unknown_criteria.extend(sub.unknown_criteria)
    report.artifact_mismatches.extend(sub.artifact_mismatches)
    return recipe, report
