"""Terminal output. No dependencies, ANSI only when stdout is a TTY."""

from __future__ import annotations

import sys

from .checks import CODE_TITLES, ERROR, AuditResult
from .drift import CHANGE_TITLES, REMOVED, CHANGED, DriftReport

RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
DIM = "\033[2m"

ERROR_MARK = "\u2716"  # heavy multiplication x
WARN_MARK = "\u25b2"  # black up-pointing triangle
OK_MARK = "\u2714"  # heavy check mark

ASCII_MARKS = {ERROR_MARK: "[X]", WARN_MARK: "[!]", OK_MARK: "[OK]"}


def _encodable(text: str, stream=None) -> bool:
    encoding = getattr(stream or sys.stdout, "encoding", None)
    if not encoding:
        return True
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def marks(stream=None) -> dict[str, str]:
    """The three status marks, degraded to ASCII on a console that can't take them.

    Redirecting stdout on Windows gives a cp1252 stream, which cannot encode
    U+2716. Crashing on `> report.txt` is not acceptable, so the glyphs are
    swapped rather than the run lost.
    """
    if _encodable(ERROR_MARK + WARN_MARK + OK_MARK, stream):
        return {ERROR_MARK: ERROR_MARK, WARN_MARK: WARN_MARK, OK_MARK: OK_MARK}
    return dict(ASCII_MARKS)


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return "".join(codes) + text + RESET


def use_color(flag: bool | None = None) -> Style:
    if flag is None:
        flag = sys.stdout.isatty()
    return Style(bool(flag))


def render_audit(result: AuditResult, style: Style) -> str:
    mark_for = marks()
    out: list[str] = []
    out.append(
        style(f"[PARSING] {result.source} ({result.report_format} report format)", DIM)
    )
    out.append(
        style(
            f"[ANALYSIS] Auditing {result.tables} tables, {result.columns} columns, "
            f"{result.measures} measures, {result.pages} report pages, {result.visuals} visuals...",
            DIM,
        )
    )
    if result.report_format == "none":
        out.append(
            style(
                f"{mark_for[WARN_MARK]} No report part found in this package. Model checks still ran, "
                "but every measure and column will look unused.",
                YELLOW,
            )
        )
    out.append("")

    for code, findings in result.by_code():
        severity = findings[0].severity
        mark, colour = (ERROR_MARK, RED) if severity == ERROR else (WARN_MARK, YELLOW)
        header = f"{mark_for[mark]} {CODE_TITLES.get(code, code)} ({len(findings)} found)"
        out.append(style(header, BOLD, colour))
        for finding in findings:
            out.append(f"  - {finding.summary}")
            for line in (finding.location, finding.detail):
                if line:
                    out.append(style(f"      {line}", DIM))
        out.append("")

    errors, warnings = len(result.errors), len(result.warnings)
    score = result.health_score
    if errors == 0 and warnings == 0:
        out.append(style(f"{mark_for[OK_MARK]} No issues found | Health Score: 100/100", BOLD, GREEN))
    else:
        colour = RED if errors else YELLOW
        out.append(
            style(
                f"{mark_for[OK_MARK]} Audit complete | Health Score: {score}/100 "
                f"({errors} errors, {warnings} warnings)",
                BOLD,
                colour,
            )
        )
    return "\n".join(out)


def render_drift(report: DriftReport, style: Style) -> str:
    mark_for = marks()
    out: list[str] = []
    out.append(style(f"[DRIFT] {report.baseline} -> {report.current}", DIM))
    out.append("")

    if not report.items:
        out.append(style(f"{mark_for[OK_MARK]} No schema drift detected", BOLD, GREEN))
        return "\n".join(out)

    for change, items in report.by_change():
        if change == REMOVED:
            mark, colour = ERROR_MARK, RED
        elif change == CHANGED:
            mark, colour = WARN_MARK, YELLOW
        else:
            mark, colour = OK_MARK, GREEN
        out.append(style(f"{mark_for[mark]} {CHANGE_TITLES[change]} ({len(items)})", BOLD, colour))
        for item in items:
            suffix = f"  {style('-- ' + item.detail, DIM)}" if item.detail else ""
            out.append(f"  - {item.kind}: {item.name}{suffix}")
        out.append("")

    out.append(
        style(
            f"{len(report.breaking)} change(s) can break existing report bindings.",
            BOLD,
        )
    )
    return "\n".join(out)
