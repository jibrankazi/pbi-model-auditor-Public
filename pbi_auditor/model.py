"""Turn a TMSL DataModelSchema into a flat, queryable catalog.

Power BI object names are case-insensitive in DAX, so every lookup here is
case-folded while the original casing is kept for display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# Auto-generated objects Power BI creates for date hierarchies and internal
# row numbering. They are never authored by a developer, so flagging them as
# unused is noise.
AUTO_TABLE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")
AUTO_COLUMN_PREFIXES = ("RowNumber-",)


def _expr_text(value: Any) -> str:
    """TMSL allows an expression to be a string or a list of lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(part) for part in value)
    return str(value)


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    data_type: str = ""
    kind: str = "data"  # data | calculated | calculatedTableColumn
    expression: str = ""
    sort_by: str = ""
    hidden: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.table.casefold(), self.name.casefold())

    def __str__(self) -> str:
        return f"'{self.table}'[{self.name}]"


@dataclass(frozen=True)
class Measure:
    table: str
    name: str
    expression: str = ""
    hidden: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.table.casefold(), self.name.casefold())

    def __str__(self) -> str:
        return f"'{self.table}'[{self.name}]"


@dataclass(frozen=True)
class Hierarchy:
    table: str
    name: str
    levels: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.table.casefold(), self.name.casefold())


@dataclass(frozen=True)
class Relationship:
    name: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool = True


@dataclass
class Catalog:
    name: str = ""
    tables: list[str] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    hierarchies: list[Hierarchy] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    role_expressions: list[str] = field(default_factory=list)

    # ---- lookups -------------------------------------------------------
    def __post_init__(self) -> None:
        self._tables = {t.casefold(): t for t in self.tables}
        self._columns = {c.key: c for c in self.columns}
        self._measures = {m.key: m for m in self.measures}
        self._hierarchies = {h.key: h for h in self.hierarchies}
        self._measures_by_name: dict[str, list[Measure]] = {}
        for m in self.measures:
            self._measures_by_name.setdefault(m.name.casefold(), []).append(m)
        self._columns_by_name: dict[str, list[Column]] = {}
        for c in self.columns:
            self._columns_by_name.setdefault(c.name.casefold(), []).append(c)

    def has_table(self, table: str) -> bool:
        return table.casefold() in self._tables

    def get_column(self, table: str, name: str) -> Column | None:
        return self._columns.get((table.casefold(), name.casefold()))

    def get_measure(self, table: str, name: str) -> Measure | None:
        return self._measures.get((table.casefold(), name.casefold()))

    def get_hierarchy(self, table: str, name: str) -> Hierarchy | None:
        return self._hierarchies.get((table.casefold(), name.casefold()))

    def measures_named(self, name: str) -> list[Measure]:
        return self._measures_by_name.get(name.casefold(), [])

    def columns_named(self, name: str) -> list[Column]:
        return self._columns_by_name.get(name.casefold(), [])

    def columns_of(self, table: str) -> list[Column]:
        return [c for c in self.columns if c.table.casefold() == table.casefold()]

    def auditable_columns(self) -> Iterable[Column]:
        for col in self.columns:
            if col.table.startswith(AUTO_TABLE_PREFIXES):
                continue
            if col.name.startswith(AUTO_COLUMN_PREFIXES):
                continue
            yield col


def build_catalog(model_schema: dict) -> Catalog:
    model = model_schema.get("model", model_schema) or {}
    tables: list[str] = []
    columns: list[Column] = []
    measures: list[Measure] = []
    hierarchies: list[Hierarchy] = []

    for table in model.get("tables", []) or []:
        tname = table.get("name", "")
        if not tname:
            continue
        tables.append(tname)
        for col in table.get("columns", []) or []:
            cname = col.get("name")
            if not cname:
                continue
            columns.append(
                Column(
                    table=tname,
                    name=cname,
                    data_type=col.get("dataType", ""),
                    kind=col.get("type", "data"),
                    expression=_expr_text(col.get("expression")),
                    sort_by=col.get("sortByColumn", "") or "",
                    hidden=bool(col.get("isHidden", False)),
                )
            )
        for mea in table.get("measures", []) or []:
            mname = mea.get("name")
            if not mname:
                continue
            measures.append(
                Measure(
                    table=tname,
                    name=mname,
                    expression=_expr_text(mea.get("expression")),
                    hidden=bool(mea.get("isHidden", False)),
                )
            )
        for hier in table.get("hierarchies", []) or []:
            hname = hier.get("name")
            if not hname:
                continue
            hierarchies.append(
                Hierarchy(
                    table=tname,
                    name=hname,
                    levels=tuple(
                        lvl.get("column", "")
                        for lvl in hier.get("levels", []) or []
                        if lvl.get("column")
                    ),
                )
            )

    relationships = [
        Relationship(
            name=rel.get("name", ""),
            from_table=rel.get("fromTable", ""),
            from_column=rel.get("fromColumn", ""),
            to_table=rel.get("toTable", ""),
            to_column=rel.get("toColumn", ""),
            is_active=bool(rel.get("isActive", True)),
        )
        for rel in model.get("relationships", []) or []
    ]

    role_expressions = [
        _expr_text(perm.get("filterExpression"))
        for role in model.get("roles", []) or []
        for perm in role.get("tablePermissions", []) or []
        if perm.get("filterExpression")
    ]

    return Catalog(
        name=model_schema.get("name", ""),
        tables=tables,
        columns=columns,
        measures=measures,
        hierarchies=hierarchies,
        relationships=relationships,
        role_expressions=role_expressions,
    )
