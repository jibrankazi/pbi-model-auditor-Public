"""Cross-check the report layer against the model catalog.

Two directions:

    report  -> model   a visual binds to something the model no longer has
    model   -> report  a model object nothing can reach

The second one needs the DAX dependency graph, not just direct hits: a measure
used only by another measure that a visual uses is not orphaned. Reachability
is computed from report bindings, RLS filters, relationships, sort-by columns
and hierarchy levels, then walked through measure and calculated-column
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import dax
from .model import Catalog
from .report import ReportInventory, ReportRef

ERROR = "error"
WARNING = "warning"

BROKEN_BINDING = "BROKEN_VISUAL_BINDING"
BROKEN_DAX = "BROKEN_DAX_REFERENCE"
BROKEN_RELATIONSHIP = "BROKEN_RELATIONSHIP"
BROKEN_SORT_BY = "BROKEN_SORT_BY"
ORPHANED_MEASURE = "ORPHANED_MEASURE"
UNUSED_COLUMN = "UNUSED_COLUMN"

CODE_TITLES = {
    BROKEN_BINDING: "BROKEN VISUAL BINDINGS",
    BROKEN_DAX: "BROKEN DAX REFERENCES",
    BROKEN_RELATIONSHIP: "BROKEN RELATIONSHIPS",
    BROKEN_SORT_BY: "BROKEN SORT-BY COLUMNS",
    ORPHANED_MEASURE: "ORPHANED MEASURES",
    UNUSED_COLUMN: "UNUSED MODEL COLUMNS",
}

CODE_ORDER = [
    BROKEN_BINDING,
    BROKEN_DAX,
    BROKEN_RELATIONSHIP,
    BROKEN_SORT_BY,
    ORPHANED_MEASURE,
    UNUSED_COLUMN,
]

ERROR_WEIGHT = 7
WARNING_WEIGHT = 2

ObjectKey = tuple[str, tuple[str, str]]  # ("column" | "measure", (table, name))


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    summary: str
    detail: str = ""
    location: str = ""


@dataclass
class AuditResult:
    source: str
    report_format: str
    tables: int = 0
    columns: int = 0
    measures: int = 0
    pages: int = 0
    visuals: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def health_score(self) -> int:
        """100 - 7 per error - 2 per warning, floored at 0."""
        penalty = ERROR_WEIGHT * len(self.errors) + WARNING_WEIGHT * len(self.warnings)
        return max(0, 100 - penalty)

    def by_code(self) -> list[tuple[str, list[Finding]]]:
        groups = []
        for code in CODE_ORDER:
            matches = [f for f in self.findings if f.code == code]
            if matches:
                groups.append((code, matches))
        return groups

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "reportFormat": self.report_format,
            "summary": {
                "tables": self.tables,
                "columns": self.columns,
                "measures": self.measures,
                "pages": self.pages,
                "visuals": self.visuals,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "healthScore": self.health_score,
            },
            "findings": [
                {
                    "code": f.code,
                    "severity": f.severity,
                    "summary": f.summary,
                    "detail": f.detail,
                    "location": f.location,
                }
                for f in self.findings
            ],
        }


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def _where(ref: ReportRef) -> str:
    """Human-readable location of a report reference."""
    if ref.origin == "report filter":
        return "(report-level filter)"
    if ref.origin == "page filter":
        return f"Page: {ref.page} (page filter)"
    suffix = " (visual filter)" if ref.origin == "visual filter" else ""
    return f"Page: {ref.page} | Visual: {ref.visual}{suffix}"


def _resolve_report_ref(ref: ReportRef, catalog: Catalog) -> tuple[list[ObjectKey], str | None]:
    """Return (resolved object keys, error message or None)."""
    if ref.table:
        if not catalog.has_table(ref.table):
            return [], f"Missing table '{ref.table}'"
        column = catalog.get_column(ref.table, ref.name)
        measure = catalog.get_measure(ref.table, ref.name)
        hierarchy = catalog.get_hierarchy(ref.table, ref.name)
        if measure:
            return [("measure", measure.key)], None
        if column:
            return [("column", column.key)], None
        if hierarchy:
            keys: list[ObjectKey] = []
            for level in hierarchy.levels:
                level_column = catalog.get_column(ref.table, level)
                if level_column:
                    keys.append(("column", level_column.key))
            return keys, None
        noun = {"measure": "measure", "hierarchy": "hierarchy"}.get(ref.kind, "column")
        return [], f"Missing {noun} '{ref.table}'[{ref.name}] (table exists, {noun} missing)"

    measures = catalog.measures_named(ref.name)
    if measures:
        return [("measure", m.key) for m in measures], None
    columns = catalog.columns_named(ref.name)
    if columns:
        return [("column", c.key) for c in columns], None
    return [], f"Missing field [{ref.name}] (no table binding recorded in the layout)"


def _resolve_dax_ref(ref: dax.DaxRef, catalog: Catalog) -> tuple[list[ObjectKey], str | None]:
    if ref.table:
        if not catalog.has_table(ref.table):
            return [], f"Missing table '{ref.table}'"
        column = catalog.get_column(ref.table, ref.name)
        if column:
            return [("column", column.key)], None
        measure = catalog.get_measure(ref.table, ref.name)
        if measure:
            return [("measure", measure.key)], None
        return [], f"Missing object '{ref.table}'[{ref.name}]"

    measures = catalog.measures_named(ref.name)
    if measures:
        return [("measure", m.key) for m in measures], None
    columns = catalog.columns_named(ref.name)
    if columns:
        return [("column", c.key) for c in columns], None
    return [], f"Missing object [{ref.name}]"


def _dependencies(expression: str, catalog: Catalog) -> tuple[list[ObjectKey], list[tuple[dax.DaxRef, str]]]:
    """Resolved dependencies plus unresolvable references."""
    resolved: list[ObjectKey] = []
    broken: list[tuple[dax.DaxRef, str]] = []
    locals_ = dax.local_names(expression)

    for ref in dax.extract_refs(expression):
        if not ref.table and ref.name.casefold() in locals_:
            continue  # name defined inside this expression
        keys, error = _resolve_dax_ref(ref, catalog)
        if error:
            broken.append((ref, error))
        resolved.extend(keys)
    return resolved, broken


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def audit(catalog: Catalog, inventory: ReportInventory, source: str) -> AuditResult:
    result = AuditResult(
        source=source,
        report_format=inventory.report_format,
        tables=len(catalog.tables),
        columns=len(list(catalog.auditable_columns())),
        measures=len(catalog.measures),
        pages=inventory.pages,
        visuals=inventory.visuals,
    )

    roots: set[ObjectKey] = set()

    # 1. report -> model
    for ref in inventory.refs:
        keys, error = _resolve_report_ref(ref, catalog)
        if error:
            result.findings.append(
                Finding(
                    code=BROKEN_BINDING,
                    severity=ERROR,
                    summary=error,
                    location=_where(ref),
                )
            )
        roots.update(keys)

    # 2. DAX dependency graph
    graph: dict[ObjectKey, list[ObjectKey]] = {}

    def add_expression(owner: ObjectKey, label: str, expression: str) -> None:
        deps, broken = _dependencies(expression, catalog)
        graph[owner] = deps
        for ref, error in broken:
            result.findings.append(
                Finding(
                    code=BROKEN_DAX,
                    severity=ERROR,
                    summary=error,
                    location=f"Referenced by {label}",
                )
            )

    for measure in catalog.measures:
        add_expression(("measure", measure.key), str(measure), measure.expression)
    for column in catalog.columns:
        if column.expression:
            add_expression(("column", column.key), str(column), column.expression)

    # 3. relationships
    for rel in catalog.relationships:
        for table, name, side in (
            (rel.from_table, rel.from_column, "from"),
            (rel.to_table, rel.to_column, "to"),
        ):
            if not catalog.has_table(table):
                result.findings.append(
                    Finding(
                        code=BROKEN_RELATIONSHIP,
                        severity=ERROR,
                        summary=f"Missing table '{table}' on the {side} side of a relationship",
                        location=f"'{rel.from_table}'[{rel.from_column}] -> '{rel.to_table}'[{rel.to_column}]",
                    )
                )
                continue
            column = catalog.get_column(table, name)
            if column is None:
                result.findings.append(
                    Finding(
                        code=BROKEN_RELATIONSHIP,
                        severity=ERROR,
                        summary=f"Missing column '{table}'[{name}] on the {side} side of a relationship",
                        location=f"'{rel.from_table}'[{rel.from_column}] -> '{rel.to_table}'[{rel.to_column}]",
                    )
                )
            else:
                roots.add(("column", column.key))

    # 4. sort-by columns and hierarchy levels
    for column in catalog.columns:
        if not column.sort_by:
            continue
        target = catalog.get_column(column.table, column.sort_by)
        if target is None:
            result.findings.append(
                Finding(
                    code=BROKEN_SORT_BY,
                    severity=ERROR,
                    summary=f"'{column.table}'[{column.name}] sorts by missing column [{column.sort_by}]",
                    location="Sort-by targets must exist in the same table",
                )
            )
        else:
            roots.add(("column", target.key))

    for hierarchy in catalog.hierarchies:
        for level in hierarchy.levels:
            column = catalog.get_column(hierarchy.table, level)
            if column:
                roots.add(("column", column.key))

    # 5. row-level security
    for expression in catalog.role_expressions:
        deps, broken = _dependencies(expression, catalog)
        roots.update(deps)
        for ref, error in broken:
            result.findings.append(
                Finding(
                    code=BROKEN_DAX,
                    severity=ERROR,
                    summary=error,
                    location="Referenced by a row-level security filter",
                )
            )

    reachable = _reachable(roots, graph)
    consumers = _consumers(graph)
    labels: dict[ObjectKey, str] = {("measure", m.key): str(m) for m in catalog.measures}
    labels.update({("column", c.key): str(c) for c in catalog.columns})

    def why_unused(key: ObjectKey, default: str) -> str:
        dead = [labels.get(owner, _label(owner)) for owner in consumers.get(key, []) if owner not in reachable]
        if dead:
            listed = ", ".join(sorted(dead)[:3])
            more = f" (+{len(dead) - 3} more)" if len(dead) > 3 else ""
            return f"Referenced only by unreachable objects: {listed}{more}"
        return default

    # 6. model -> report
    for measure in catalog.measures:
        key: ObjectKey = ("measure", measure.key)
        if key not in reachable:
            result.findings.append(
                Finding(
                    code=ORPHANED_MEASURE,
                    severity=WARNING,
                    summary=str(measure),
                    location=why_unused(
                        key, "Not used by any visual, filter, RLS rule or dependent DAX expression"
                    ),
                )
            )

    for column in catalog.auditable_columns():
        key = ("column", column.key)
        if key not in reachable:
            result.findings.append(
                Finding(
                    code=UNUSED_COLUMN,
                    severity=WARNING,
                    summary=str(column),
                    location=why_unused(key, "Loaded to the model; 0 downstream references"),
                )
            )

    return result


def _label(key: ObjectKey) -> str:
    _kind, (table, name) = key
    return f"'{table}'[{name}]"


def _consumers(graph: dict[ObjectKey, list[ObjectKey]]) -> dict[ObjectKey, list[ObjectKey]]:
    """Reverse the dependency graph: object -> things that reference it."""
    reverse: dict[ObjectKey, list[ObjectKey]] = {}
    for owner, deps in graph.items():
        for dep in deps:
            reverse.setdefault(dep, []).append(owner)
    return reverse


def _reachable(roots: Iterable[ObjectKey], graph: dict[ObjectKey, list[ObjectKey]]) -> set[ObjectKey]:
    seen: set[ObjectKey] = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    return seen
