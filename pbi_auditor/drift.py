"""Compare two model catalogs and report what moved between them.

Removals are the ones that matter: a column that disappeared between the
baseline and the current file is almost always the cause of a broken binding
somewhere in the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Catalog

REMOVED = "removed"
ADDED = "added"
CHANGED = "changed"

CHANGE_ORDER = [REMOVED, CHANGED, ADDED]
CHANGE_TITLES = {
    REMOVED: "REMOVED FROM MODEL",
    CHANGED: "CHANGED DEFINITION",
    ADDED: "ADDED TO MODEL",
}


@dataclass(frozen=True)
class DriftItem:
    change: str
    kind: str  # table | column | measure | relationship
    name: str
    detail: str = ""


@dataclass
class DriftReport:
    baseline: str
    current: str
    items: list[DriftItem] = field(default_factory=list)

    @property
    def breaking(self) -> list[DriftItem]:
        return [i for i in self.items if i.change in (REMOVED, CHANGED)]

    def by_change(self) -> list[tuple[str, list[DriftItem]]]:
        groups = []
        for change in CHANGE_ORDER:
            matches = [i for i in self.items if i.change == change]
            if matches:
                groups.append((change, matches))
        return groups

    def to_dict(self) -> dict:
        return {
            "baseline": self.baseline,
            "current": self.current,
            "summary": {
                "removed": len([i for i in self.items if i.change == REMOVED]),
                "changed": len([i for i in self.items if i.change == CHANGED]),
                "added": len([i for i in self.items if i.change == ADDED]),
            },
            "items": [
                {"change": i.change, "kind": i.kind, "name": i.name, "detail": i.detail}
                for i in self.items
            ],
        }


def _rel_key(rel) -> str:
    return (
        f"'{rel.from_table}'[{rel.from_column}] -> '{rel.to_table}'[{rel.to_column}]"
    ).casefold()


def compare(baseline: Catalog, current: Catalog, baseline_name: str, current_name: str) -> DriftReport:
    report = DriftReport(baseline=baseline_name, current=current_name)

    old_tables = {t.casefold(): t for t in baseline.tables}
    new_tables = {t.casefold(): t for t in current.tables}
    for key, name in old_tables.items():
        if key not in new_tables:
            report.items.append(DriftItem(REMOVED, "table", f"'{name}'"))
    for key, name in new_tables.items():
        if key not in old_tables:
            report.items.append(DriftItem(ADDED, "table", f"'{name}'"))

    old_columns = {c.key: c for c in baseline.columns}
    new_columns = {c.key: c for c in current.columns}
    for key, col in old_columns.items():
        if key not in new_columns:
            if key[0] in new_tables:  # table survived, column did not
                report.items.append(DriftItem(REMOVED, "column", str(col)))
        else:
            updated = new_columns[key]
            if col.data_type and updated.data_type and col.data_type != updated.data_type:
                report.items.append(
                    DriftItem(CHANGED, "column", str(col), f"dataType {col.data_type} -> {updated.data_type}")
                )
            if col.expression != updated.expression:
                report.items.append(DriftItem(CHANGED, "column", str(col), "calculation changed"))
            if col.sort_by != updated.sort_by:
                report.items.append(
                    DriftItem(CHANGED, "column", str(col), f"sortByColumn '{col.sort_by}' -> '{updated.sort_by}'")
                )
    for key, col in new_columns.items():
        if key not in old_columns and key[0] in old_tables:
            report.items.append(DriftItem(ADDED, "column", str(col)))

    old_measures = {m.key: m for m in baseline.measures}
    new_measures = {m.key: m for m in current.measures}
    for key, mea in old_measures.items():
        if key not in new_measures:
            report.items.append(DriftItem(REMOVED, "measure", str(mea)))
        elif mea.expression != new_measures[key].expression:
            report.items.append(DriftItem(CHANGED, "measure", str(mea), "DAX expression changed"))
    for key, mea in new_measures.items():
        if key not in old_measures:
            report.items.append(DriftItem(ADDED, "measure", str(mea)))

    old_rels = {_rel_key(r): r for r in baseline.relationships}
    new_rels = {_rel_key(r): r for r in current.relationships}
    for key, rel in old_rels.items():
        if key not in new_rels:
            report.items.append(
                DriftItem(REMOVED, "relationship", f"'{rel.from_table}'[{rel.from_column}] -> '{rel.to_table}'[{rel.to_column}]")
            )
    for key, rel in new_rels.items():
        if key not in old_rels:
            report.items.append(
                DriftItem(ADDED, "relationship", f"'{rel.from_table}'[{rel.from_column}] -> '{rel.to_table}'[{rel.to_column}]")
            )

    return report
