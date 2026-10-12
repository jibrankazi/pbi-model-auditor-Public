"""Read a public third-party Power BI native TMDL/PBIR project verbatim.

The upstream EnSFinance model and report are not authored by this repository.
Only an ephemeral staging directory is created because upstream houses a
second semantic model under the same projects/ directory. No private data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from pbi_auditor import audit_file
from pbi_auditor.reader import read_package


def inspect(source: Path) -> dict:
    semantic = source / "EnSFinance.SemanticModel"
    report = source / "EnSFinance.Report"
    original = sorted((semantic / "definition" / "tables").glob("*.tmdl"))
    pbir = sorted((report / "definition").rglob("*.json"))
    if len(original) < 8 or not pbir or not (semantic / "definition" / "relationships.tmdl").is_file():
        raise ValueError("Expected genuine upstream native TMDL/PBIR source files")
    hashes = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in original}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copytree(semantic, root / semantic.name)
        shutil.copytree(report, root / report.name)
        pkg = read_package(root)
        model = pkg.model_schema["model"]
        if len(model["tables"]) != len(original):
            raise AssertionError("Native TMDL table files lost during catalog extraction")
        if len(model["relationships"]) < 8:
            raise AssertionError("Native relationship objects lost during parsing")
        result = audit_file(root)
        if result.tables != len(original):
            raise AssertionError("Audited model inventory differs from real source files")
        if result.pages < 1 or result.visuals < 1:
            raise AssertionError("Expected at least one real PBIR page and visual")
        return {
            "source_repository": "https://github.com/ens-bilgi/ens-pbi",
            "source_path": "projects/EnSFinance.SemanticModel and projects/EnSFinance.Report",
            "genuinely_external_public_repository": True,
            "original_tmdl_tables": len(original),
            "parsed_relationships": len(model["relationships"]),
            "model_table_sha256": hashes,
            "report_format": pkg.report_format,
            "audit": result.to_dict(),
            "authentic_source_static_read_passed": True,
            "tenant_refresh_or_rendered_visual_validation": False,
            "note": "Structural static audit on real third-party source, not proof of model correctness or absence of unsupported TMDL semantics.",
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    report = inspect(Path(args.source))
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "model_table_sha256"}, indent=2))


if __name__ == "__main__":
    main()
