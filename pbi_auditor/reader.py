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


def read_package(path: str | Path) -> Package:
    path = Path(path)
    if not path.exists():
        raise PackageError(f"File not found: {path}")
    if not zipfile.is_zipfile(path):
        raise PackageError(
            f"{path.name} is not a ZIP container. "
            "Expected a .pbit or .pbix file."
        )

    pkg = Package(path=path)
    with zipfile.ZipFile(path) as zf:
        pkg.part_names = zf.namelist()
        for name in pkg.part_names:
            key = name.replace("\\", "/").lstrip("/").lower()
            if key == MODEL_PART:
                pkg.model_schema = _loads(zf.read(name), name)
            elif key == LAYOUT_PART:
                pkg.layout = _loads(zf.read(name), name)
            elif key.startswith(PBIR_PREFIX) and key.endswith(".json"):
                pkg.pbir_parts[name] = _loads(zf.read(name), name)

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
