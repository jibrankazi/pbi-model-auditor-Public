"""Conservative TMDL-to-catalog translation for common PBIP model objects.

Unsupported calculation groups fail closed. This is not a full TMDL parser.
"""
from pathlib import Path
import re

NAME = r"(?:'(?:''|[^'])*'|[^\s=]+)"
DECL = re.compile(rf"^(table|column|measure|hierarchy|level|relationship|role|tablePermission)\s+({NAME})(?:\s*=\s*(.*))?$")
PROP = re.compile(r"^([A-Za-z]\w*):\s*(.*)$")
QUALIFIED = re.compile(r"^\s*('(?:''|[^'])*'|[^.\s]+)\.('(?:''|[^'])*'|.+?)\s*$")


class UnsupportedTMDL(ValueError):
    pass


def unquote(value):
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def qualified(value):
    match = QUALIFIED.fullmatch(value)
    if not match:
        raise UnsupportedTMDL(f"Unsupported TMDL relationship endpoint: {value}")
    return unquote(match[1]), unquote(match[2])


def tokens(file):
    for n, raw in enumerate(file.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.expandtabs(4)
        stripped = line.strip()
        if not stripped or stripped.startswith(("///", "//")):
            continue
        yield (len(line) - len(line.lstrip(" ")), stripped, n)


def read_expression(lines, index, indent, inline):
    if inline:
        return inline
    values = []
    for depth, line, _ in lines[index+1:]:
        if depth <= indent or PROP.match(line) or DECL.match(line):
            break
        values.append(line)
    return "\n".join(values).strip()


def parse_definition(root: Path) -> dict:
    table_files = sorted((root / "tables").glob("*.tmdl"))
    if not table_files:
        raise UnsupportedTMDL("No TMDL table definitions found")
    role_files = sorted((root / "roles").glob("*.tmdl"))
    rel = root / "relationships.tmdl"
    files = table_files + role_files + ([rel] if rel.is_file() else [])
    tables, relationships, roles = {}, [], []
    seen = set()
    for path in files:
        if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
            raise UnsupportedTMDL("Unsafe TMDL path or size")
        lines = list(tokens(path))
        section = path.parent.name
        table = role = obj = hierarchy = None
        skip_below = None
        for i, (depth, line, n) in enumerate(lines):
            if skip_below is not None:
                if depth > skip_below:
                    continue
                skip_below = None
            if line.startswith(("calculationGroup", "calculationItem")):
                raise UnsupportedTMDL(f"{path}:{n}: calculation groups not safely supported")
            if line.startswith("partition "):
                skip_below = depth
                continue
            if line.startswith(("annotation ", "extendedProperty ", "ref ")):
                continue
            m = DECL.match(line)
            if m:
                kind, raw_name, inline = m.groups()
                name = unquote(raw_name)
                if kind == "table":
                    if section != "tables":
                        raise UnsupportedTMDL(f"{path}:{n}: misplaced table")
                    table = tables.setdefault(name, {"name": name, "columns": [],
                                                    "measures": [], "hierarchies": []})
                    obj = hierarchy = None
                elif kind in ("column", "measure", "hierarchy"):
                    if table is None or section != "tables":
                        raise UnsupportedTMDL(f"{path}:{n}: missing table context")
                    key = (table["name"].casefold(), kind, name.casefold())
                    if key in seen:
                        raise UnsupportedTMDL(f"{path}:{n}: duplicate object {key}")
                    seen.add(key)
                    if kind == "column":
                        obj = {"name": name, "dataType": ""}
                        if inline is not None:
                            obj.update(type="calculated",
                                       expression=read_expression(lines, i, depth, inline))
                        table["columns"].append(obj)
                        hierarchy = None
                    elif kind == "measure":
                        obj = {"name": name, "expression": read_expression(lines, i, depth, inline)}
                        table["measures"].append(obj)
                        hierarchy = None
                    else:
                        obj = None
                        hierarchy = {"name": name, "levels": []}
                        table["hierarchies"].append(hierarchy)
                elif kind == "level":
                    if hierarchy is None:
                        raise UnsupportedTMDL(f"{path}:{n}: level without hierarchy")
                    obj = {"name": name}
                    hierarchy["levels"].append(obj)
                elif kind == "role":
                    if section != "roles":
                        raise UnsupportedTMDL(f"{path}:{n}: misplaced role")
                    role = {"name": name, "tablePermissions": []}
                    roles.append(role)
                    obj = None
                elif kind == "tablePermission":
                    if role is None:
                        raise UnsupportedTMDL(f"{path}:{n}: permission outside role")
                    obj = {"name": name}
                    if inline is not None:
                        obj["filterExpression"] = read_expression(lines, i, depth, inline)
                    role["tablePermissions"].append(obj)
                elif kind == "relationship":
                    if section != "definition":
                        raise UnsupportedTMDL(f"{path}:{n}: misplaced relationship")
                    obj = {"name": name}
                    relationships.append(obj)
                continue
            prop = PROP.match(line)
            if prop:
                key, value = prop.groups()
                if obj is not None and key in {"dataType", "sortByColumn", "column",
                        "fromColumn", "toColumn", "filterExpression", "expression"}:
                    obj[key] = unquote(value)
                if obj is not None and key in {"isActive", "isHidden"}:
                    obj[key] = value.lower() != "false"
                continue
            if obj is not None and line == "isHidden":
                obj["isHidden"] = True
                continue
            if depth == 0 and re.match(r"[A-Za-z]+\s", line):
                raise UnsupportedTMDL(f"{path}:{n}: unrecognized top-level structure: {line[:64]}")
        if section == "tables" and not any(depth == 0 and line.startswith("table ")
                                            for depth, line, _ in lines):
            raise UnsupportedTMDL(f"{path}: no top-level table")
    for rel in relationships:
        if not {"fromColumn", "toColumn"}.issubset(rel):
            raise UnsupportedTMDL("Relationship lacks endpoints")
        rel["fromTable"], rel["fromColumn"] = qualified(rel["fromColumn"])
        rel["toTable"], rel["toColumn"] = qualified(rel["toColumn"])
    for table in tables.values():
        for h in table["hierarchies"]:
            if any("column" not in level for level in h["levels"]):
                raise UnsupportedTMDL(f"Hierarchy {h['name']} has unresolved level columns")
    return {"name": root.parent.name, "model": {
        "tables": list(tables.values()), "relationships": relationships, "roles": roles}}
