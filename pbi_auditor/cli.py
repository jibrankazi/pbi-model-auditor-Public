#!/usr/bin/env python3
"""pbi-audit - static analysis for Power BI template (.pbit) files.

Exit codes:
    0  clean (or warnings only, without --strict)
    1  errors found (or any finding with --strict)
    2  the file could not be read
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .checks import audit
from .drift import compare
from .model import build_catalog
from .reader import PackageError, read_package
from .render import render_audit, render_drift, use_color
from .report import extract_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pbi-audit",
        description="Detect broken field bindings, orphaned objects and schema drift in .pbit files.",
    )
    parser.add_argument("--file", "-f", required=True, help="path to the .pbit file to audit")
    parser.add_argument(
        "--baseline",
        "-b",
        help="path to an earlier .pbit; adds a schema drift report against it",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of text")
    parser.add_argument("--strict", action="store_true", help="exit non-zero on warnings as well as errors")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    parser.add_argument("--version", action="version", version=f"pbi-audit {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Windows hands us a cp1252 stream when stdout is redirected to a file.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass  # render.py degrades the status marks to ASCII instead

    style = use_color(False if args.no_color else None)

    try:
        package = read_package(args.file)
        catalog = build_catalog(package.model_schema or {})
        inventory = extract_report(package)
        result = audit(catalog, inventory, source=Path(args.file).name)

        drift = None
        if args.baseline:
            baseline_package = read_package(args.baseline)
            baseline_catalog = build_catalog(baseline_package.model_schema or {})
            drift = compare(
                baseline_catalog,
                catalog,
                baseline_name=Path(args.baseline).name,
                current_name=Path(args.file).name,
            )
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = result.to_dict()
        if drift is not None:
            payload["drift"] = drift.to_dict()
        print(json.dumps(payload, indent=2))
    else:
        print(render_audit(result, style))
        if drift is not None:
            print()
            print(render_drift(drift, style))

    if result.errors:
        return 1
    if args.strict and result.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
