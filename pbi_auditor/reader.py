"""Read the JSON parts out of a .pbit / .pbix ZIP container.

A .pbit is a plain ZIP. The parts this tool cares about:

    DataModelSchema      TMSL JSON: tables, columns, measures, relationships
    Report/Layout        legacy report JSON: sections -> visualContainers
    Report/definition/** PBIR report JSON (one file per page/visual)

Text parts are usually UTF-16 LE with a BOM, but UTF-8 shows up in files that
have been round-tripped through other tooling, so both are handled.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODEL_PART = "datamodelschema"
LAYOUT_PART = "report/layout"
PBIR_PREFIX = "report/definition/"
DATAMODEL_BINARY = "datamodel"
MAX_JSON_PART_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_PARTS = 10000


class PackageError(Exception):
    """The file is not a readable Power BI package."""


def _decode(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig")
    # UTF-16 LE without a BOM leaves a NUL in every second byte.
    if len(raw) > 3 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le")
    return raw.decode("utf-8")


def _loads(raw: bytes, part: str) -> Any:
    text = _decode(raw).lstrip("\ufeff").rstrip("\x00").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise PackageError(f"{part} is not valid JSON: {exc}") from exc


@dataclass
class Package:
    """The parsed parts of one .pbit file."""

    path: Path
    model_schema: dict | None = None
    layout: dict | None = None
    pbir_parts: dict[str, Any] = field(default_factory=dict)
    part_names: list[str] = field(default_factory=list)

    @property
    def report_format(self) -> str:
        if self.layout is not None:
            return "legacy"
        if self.pbir_parts:
            return "pbir"
        return "none"



def _bounded_json(raw: bytes, label: str) -> Any:
    if len(raw) > MAX_JSON_PART_BYTES:
        raise PackageError(f"{label} exceeds the per-part size limit")
    try:
        return _loads(raw, label)
    except (UnicodeError, ValueError) as exc:
        raise PackageError(f"{label} could not be decoded as JSON: {exc}") from exc


def _read_project(path: Path) -> Package:
    """Read either one TMSL or one recognized TMDL model and PBIR report."""
    root = path.parent if path.suffix.lower() == ".pbip" else path
    if path.is_file():
        pointer = _bounded_json(path.read_bytes(), str(path))
        if not isinstance(pointer, dict):
            raise PackageError("PBIP pointer must contain a JSON object")
    models = sorted(root.glob("*.SemanticModel/model.bim"))
    tmdl_models = sorted(model.parent.parent for model in
                         root.glob("*.SemanticModel/definition/tables/*.tmdl"))
    tmdl_models = sorted(set(tmdl_models))
    reports = sorted(root.glob("*.Report/definition"))
    if len(models) + len(tmdl_models) != 1:
        raise PackageError("Expected exactly one semantic model in TMSL or TMDL format")
    if len(reports) != 1:
        raise PackageError("Expected exactly one *.Report/definition folder in the .pbip project")
    if (models[0] if models else tmdl_models[0]).is_symlink() or reports[0].is_symlink():
        raise PackageError("Symlinked PBIP model/report paths are refused")
    if models:
        schema = _bounded_json(models[0].read_bytes(), str(models[0]))
    else:
        from .tmdl import UnsupportedTMDL, parse_definition
        try:
            schema = parse_definition(tmdl_models[0])
        except (UnsupportedTMDL, UnicodeError, OSError) as exc:
            raise PackageError(f"TMDL model cannot be audited safely: {exc}") from exc
    pkg = Package(path=path, model_schema=schema)
    if not isinstance(pkg.model_schema, dict) or not isinstance(pkg.model_schema.get("model"), dict):
        raise PackageError("PBIP semantic model did not yield a valid catalog")
    parts = list(reports[0].rglob("*.json"))
    if len(parts) > MAX_PACKAGE_PARTS:
        raise PackageError("PBIP report contains too many JSON parts")
    for part in parts:
        if part.is_symlink():
            raise PackageError("Symlinked PBIP report definitions are refused")
        relative = part.relative_to(reports[0]).as_posix()
        name = f"Report/definition/{relative}"
        if part.stat().st_size > MAX_JSON_PART_BYTES:
            raise PackageError(f"{name} exceeds the per-part size limit")
        pkg.pbir_parts[name] = _bounded_json(part.read_bytes(), name)
        pkg.part_names.append(name)
    if not pkg.pbir_parts:
        raise PackageError("No PBIR report definition JSON found")
    return pkg


def read_package(path: str | Path) -> Package:
    path = Path(path)
    if not path.exists():
        raise PackageError(f"File not found: {path}")
    if path.is_dir() or path.suffix.lower() == ".pbip":
        return _read_project(path)
    if not zipfile.is_zipfile(path):
        raise PackageError(
            f"{path.name} is not a ZIP container. "
            "Expected a .pbit or .pbix file."
        )

    pkg = Package(path=path)
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_PACKAGE_PARTS:
            raise PackageError("Power BI ZIP contains too many parts")
        keys = set()
        pkg.part_names = [info.filename for info in infos]
        for info in infos:
            name = info.filename
            key = name.replace(chr(92), "/").lstrip("/").lower()
            if key in keys:
                raise PackageError(f"Duplicate canonical ZIP entry: {key}")
            keys.add(key)
            if key in (MODEL_PART, LAYOUT_PART) or (
                key.startswith(PBIR_PREFIX) and key.endswith(".json")
            ):
                if info.file_size > MAX_JSON_PART_BYTES:
                    raise PackageError(f"{name} exceeds the per-part size limit")
                parsed = _bounded_json(zf.read(info), name)
                if key == MODEL_PART:
                    pkg.model_schema = parsed
                elif key == LAYOUT_PART:
                    pkg.layout = parsed
                else:
                    pkg.pbir_parts[name] = parsed

    if pkg.model_schema is None:
        lower = {n.lower() for n in pkg.part_names}
        if DATAMODEL_BINARY in lower:
            raise PackageError(
                f"{path.name} contains a compiled DataModel (a .pbix with data), "
                "not a DataModelSchema. Export the report as a .pbit "
                "(File > Export > Power BI template) and audit that instead."
            )
        raise PackageError(f"{path.name} has no DataModelSchema part.")

    return pkg
