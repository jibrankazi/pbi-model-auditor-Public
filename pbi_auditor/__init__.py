"""Static analysis for Power BI template (.pbit) files."""

from __future__ import annotations

from pathlib import Path

from .checks import AuditResult, Finding, audit
from .drift import DriftReport, compare
from .model import Catalog, build_catalog
from .reader import Package, PackageError, read_package
from .report import ReportInventory, extract_report

__version__ = "0.1.0"

__all__ = [
    "AuditResult",
    "Catalog",
    "DriftReport",
    "Finding",
    "Package",
    "PackageError",
    "ReportInventory",
    "audit",
    "audit_file",
    "build_catalog",
    "catalog_of",
    "compare",
    "extract_report",
    "read_package",
]


def audit_file(path: str | Path) -> AuditResult:
    """Read a .pbit and return its audit result."""
    package = read_package(path)
    catalog = build_catalog(package.model_schema or {})
    inventory = extract_report(package)
    return audit(catalog, inventory, source=Path(path).name)


def catalog_of(path: str | Path) -> Catalog:
    """Read a .pbit and return just its model catalog."""
    package = read_package(path)
    return build_catalog(package.model_schema or {})
