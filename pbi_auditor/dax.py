"""Extract object references from DAX expressions.

This is a reference scanner, not a parser. It answers one question: which
`Table[Object]` and `[Object]` names does this expression mention? That is
enough to (a) find references to objects that no longer exist and (b) know
which measures and columns are actually reachable.

Deliberately conservative:
  * comments and string literals are stripped first, so `"Sales[Gone]"` inside
    a text literal is not reported;
  * an unqualified `[Name]` is ambiguous in DAX (it can be a measure, or a
    column in the current row context), so it is only flagged when it matches
    nothing at all in the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?://|--)[^\n]*")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_TRAILING_IDENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)$")


@dataclass(frozen=True)
class DaxRef:
    """One `Table[Object]` or `[Object]` mention."""

    table: str | None
    name: str

    @property
    def qualified(self) -> bool:
        return self.table is not None

    def __str__(self) -> str:
        return f"'{self.table}'[{self.name}]" if self.table else f"[{self.name}]"


def local_names(expression: str) -> set[str]:
    """Names the expression defines for itself.

    `ADDCOLUMNS(t, "Margin", ...)` creates `[Margin]`, which exists only inside
    that expression. Collecting every string literal is crude but it only ever
    suppresses findings, so it cannot invent one.
    """
    if not expression:
        return set()
    return {match.group(1).casefold() for match in re.finditer(r'"([^"\\]{1,128})"', expression)}


def _strip_noise(expression: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", expression)
    text = _LINE_COMMENT.sub(" ", text)
    return _STRING.sub('""', text)


def extract_refs(expression: str) -> list[DaxRef]:
    """Return every object reference in `expression`, in source order."""
    if not expression:
        return []

    text = _strip_noise(expression)
    refs: list[DaxRef] = []

    for match in _BRACKETED.finditer(text):
        name = match.group(1).strip()
        if not name:
            continue
        prefix = text[: match.start()]
        table: str | None = None

        if prefix.endswith("'"):
            # Quoted table name: 'Sales Header'[Amount]
            opening = prefix.rfind("'", 0, len(prefix) - 1)
            if opening != -1:
                candidate = prefix[opening + 1 : -1].replace("''", "'")
                if candidate:
                    table = candidate
        else:
            # Bare table name, no whitespace allowed: Sales[Amount]
            ident = _TRAILING_IDENT.search(prefix)
            if ident:
                table = ident.group(1)

        refs.append(DaxRef(table=table, name=name))

    return refs
