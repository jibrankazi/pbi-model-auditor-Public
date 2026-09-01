"""Extract every model reference made by the report layer.

Two report formats are supported:

  legacy  Report/Layout, a single JSON document whose `config`, `filters`,
          `query` and `dataTransforms` members are themselves JSON *strings*
  PBIR    Report/definition/**, one JSON file per page and visual

Both encode field references the same way, as query expression nodes:

    {"Column":  {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Amount"}}
    {"Measure": {"Expression": {"SourceRef": {"Entity": "Sales"}}, "Property": "Total Revenue"}}

`Source` is an alias declared in the enclosing query's `From` clause, so the
alias map is collected first and used to resolve those aliases to table names.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any

from .reader import Package

_AGG_WRAPPER = re.compile(r"^[A-Za-z]+\((.*)\)$")


@dataclass(frozen=True)
class ReportRef:
    """One reference from the report layer to a model object."""

    kind: str  # column | measure | hierarchy | field
    table: str | None
    name: str
    page: str
    visual: str
    origin: str  # visual | visual filter | page filter | report filter

    @property
    def display(self) -> str:
        if self.table:
            return f"'{self.table}'[{self.name}]"
        return f"[{self.name}]"


@dataclass
class ReportInventory:
    refs: list[ReportRef]
    pages: int = 0
    visuals: int = 0
    report_format: str = "none"


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------
def _maybe_json(value: Any) -> Any:
    """Layout embeds sub-documents as JSON strings; parse them transparently."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return None
        return None
    return value


def _alias_map(node: Any, aliases: dict[str, str] | None = None) -> dict[str, str]:
    """Collect every `From` alias declared anywhere inside a blob."""
    aliases = {} if aliases is None else aliases
    if isinstance(node, dict):
        entries = node.get("From")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("Name") and entry.get("Entity"):
                    aliases.setdefault(entry["Name"], entry["Entity"])
        for value in node.values():
            _alias_map(value, aliases)
    elif isinstance(node, list):
        for item in node:
            _alias_map(item, aliases)
    return aliases


def _entity(expression: Any, aliases: dict[str, str]) -> str | None:
    if not isinstance(expression, dict):
        return None
    source = expression.get("SourceRef")
    if isinstance(source, dict):
        if source.get("Entity"):
            return source["Entity"]
        if source.get("Source"):
            return aliases.get(source["Source"])
    return None


def _walk(node: Any, aliases: dict[str, str], out: list[tuple[str, str | None, str]]) -> None:
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            spec = node.get(kind)
            if isinstance(spec, dict) and isinstance(spec.get("Property"), str):
                out.append((kind.lower(), _entity(spec.get("Expression"), aliases), spec["Property"]))

        level = node.get("HierarchyLevel")
        if isinstance(level, dict):
            hierarchy = (level.get("Expression") or {}).get("Hierarchy")
            if isinstance(hierarchy, dict) and isinstance(hierarchy.get("Hierarchy"), str):
                out.append(
                    ("hierarchy", _entity(hierarchy.get("Expression"), aliases), hierarchy["Hierarchy"])
                )

        for value in node.values():
            _walk(value, aliases, out)
    elif isinstance(node, list):
        for item in node:
            _walk(item, aliases, out)


def _query_refs(node: Any, out: list[str]) -> None:
    """Fallback: collect `queryRef` strings such as `Sum(Sales.Amount)`."""
    if isinstance(node, dict):
        ref = node.get("queryRef")
        if isinstance(ref, str):
            out.append(ref)
        refs = node.get("queryRefs")
        if isinstance(refs, list):
            out.extend(r for r in refs if isinstance(r, str))
        for value in node.values():
            _query_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            _query_refs(item, out)


def _split_query_ref(ref: str) -> tuple[str | None, str]:
    inner = ref.strip()
    match = _AGG_WRAPPER.match(inner)
    if match:
        inner = match.group(1).strip()
    if "." in inner:
        table, _, name = inner.partition(".")
        return table or None, name
    return None, inner


def _literal(value: Any) -> str | None:
    """Unwrap `{"expr": {"Literal": {"Value": "'Title'"}}}`."""
    if not isinstance(value, dict):
        return None
    literal = ((value.get("expr") or {}).get("Literal") or {}).get("Value")
    if isinstance(literal, str):
        return literal.strip().strip("'")
    return None


def _visual_label(config: Any, fallback: str = "") -> str:
    visual = {}
    if isinstance(config, dict):
        visual = config.get("singleVisual") or config.get("visual") or {}
    visual_type = visual.get("visualType") or "visual"

    title = None
    for container in ("vcObjects", "visualContainerObjects", "objects"):
        objects = visual.get(container) or (config.get(container) if isinstance(config, dict) else None)
        if isinstance(objects, dict):
            entries = objects.get("title")
            if isinstance(entries, list) and entries:
                properties = entries[0].get("properties") if isinstance(entries[0], dict) else None
                if isinstance(properties, dict):
                    title = _literal(properties.get("text"))
            if title:
                break

    if title:
        return f'"{title}" ({visual_type})'
    if fallback:
        return f"{visual_type} #{fallback[:8]}"
    return visual_type


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
def _refs_from_blob(blob: Any, page: str, visual: str, origin: str) -> list[ReportRef]:
    if blob is None:
        return []

    aliases = _alias_map(blob)
    found: list[tuple[str, str | None, str]] = []
    _walk(blob, aliases, found)

    if not found:
        # Older or hand-built layouts may only carry queryRef strings.
        raw: list[str] = []
        _query_refs(blob, raw)
        for ref in raw:
            table, name = _split_query_ref(ref)
            if name:
                found.append(("field", table, name))

    refs: list[ReportRef] = []
    seen: set[tuple[str, str | None, str]] = set()
    for kind, table, name in found:
        key = (kind, (table or "").casefold() or None, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        refs.append(ReportRef(kind=kind, table=table, name=name, page=page, visual=visual, origin=origin))
    return refs


def _from_legacy(layout: dict) -> ReportInventory:
    refs: list[ReportRef] = []
    sections = layout.get("sections") or []
    visuals = 0

    refs += _refs_from_blob(_maybe_json(layout.get("filters")), "(report)", "-", "report filter")

    for section in sections:
        page = section.get("displayName") or section.get("name") or "(unnamed page)"
        refs += _refs_from_blob(_maybe_json(section.get("filters")), page, "-", "page filter")

        for container in section.get("visualContainers") or []:
            visuals += 1
            config = _maybe_json(container.get("config"))
            label = _visual_label(config, str(container.get("id", "")))
            refs += _refs_from_blob(config, page, label, "visual")
            refs += _refs_from_blob(_maybe_json(container.get("query")), page, label, "visual")
            refs += _refs_from_blob(_maybe_json(container.get("dataTransforms")), page, label, "visual")
            refs += _refs_from_blob(_maybe_json(container.get("filters")), page, label, "visual filter")

    return ReportInventory(refs=refs, pages=len(sections), visuals=visuals, report_format="legacy")


def _from_pbir(parts: dict[str, Any]) -> ReportInventory:
    normalised = {name.replace("\\", "/"): data for name, data in parts.items()}
    page_names: dict[str, str] = {}
    for path, data in normalised.items():
        if path.endswith("/page.json") and isinstance(data, dict):
            page_names[posixpath.dirname(path)] = data.get("displayName") or data.get("name") or "(unnamed page)"

    def page_for(path: str) -> str:
        directory = posixpath.dirname(path)
        while directory and directory != "/":
            if directory in page_names:
                return page_names[directory]
            directory = posixpath.dirname(directory)
        return "(unknown page)"

    refs: list[ReportRef] = []
    visuals = 0
    for path, data in sorted(normalised.items()):
        base = posixpath.basename(path)
        if base == "report.json":
            refs += _refs_from_blob(data, "(report)", "-", "report filter")
        elif base == "page.json":
            refs += _refs_from_blob(data, page_for(path), "-", "page filter")
        elif base in {"visual.json", "visualContainer.json"}:
            visuals += 1
            label = _visual_label(data, posixpath.basename(posixpath.dirname(path)))
            refs += _refs_from_blob(data, page_for(path), label, "visual")

    return ReportInventory(refs=refs, pages=len(page_names), visuals=visuals, report_format="pbir")


def extract_report(pkg: Package) -> ReportInventory:
    if pkg.layout is not None:
        return _from_legacy(pkg.layout)
    if pkg.pbir_parts:
        return _from_pbir(pkg.pbir_parts)
    return ReportInventory(refs=[], report_format="none")
