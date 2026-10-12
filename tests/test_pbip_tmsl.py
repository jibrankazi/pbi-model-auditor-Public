"""PBIP sources here are software fixtures, not real customer reports."""
import json
import zipfile
import pytest

from pbi_auditor import audit_file
from pbi_auditor.reader import PackageError, read_package
from build_sample import build_model


def create_pbip(root):
    root.mkdir()
    (root / "Sales.pbip").write_text('{"version":"1.0"}', encoding="utf-8")
    semantic = root / "Sales.SemanticModel"
    semantic.mkdir()
    (semantic / "model.bim").write_text(json.dumps(build_model(broken=False)))
    visual = root / "Sales.Report/definition/pages/Page01/visuals/Card01"
    visual.mkdir(parents=True)
    (visual.parent.parent / "page.json").write_text(json.dumps({
        "displayName": "Genuine source schema fixture"
    }))
    (visual / "visual.json").write_text(json.dumps({
        "visual": {"visualType": "card"},
        "query": {"From": [{"Name": "s", "Entity": "Sales"}],
                  "Select": [{"Measure": {"Expression": {"SourceRef": {"Source": "s"}},
                                          "Property": "Total Revenue"}}]},
    }))
    return root


def test_tmsl_pbip_project_folder_and_pointer(tmp_path):
    root = create_pbip(tmp_path / "Project")
    for path in (root, root / "Sales.pbip"):
        package = read_package(path)
        assert package.report_format == "pbir"
        assert package.model_schema["name"] == "SalesPerformance"
        result = audit_file(path)
        assert result.pages == 1
        assert result.visuals == 1
        assert not result.errors


def test_tmdl_is_refused_instead_of_falsely_clean(tmp_path):
    root = create_pbip(tmp_path / "Project")
    (root / "Sales.SemanticModel/model.bim").unlink()
    table = root / "Sales.SemanticModel/definition/tables"
    table.mkdir(parents=True)
    (table / "Sales.tmdl").write_text("table Sales")
    with pytest.raises(PackageError, match="TMDL-only"):
        read_package(root / "Sales.pbip")


def test_ambiguous_semantic_models_are_rejected(tmp_path):
    root = create_pbip(tmp_path / "Project")
    second = root / "Extra.SemanticModel"
    second.mkdir()
    (second / "model.bim").write_text("{}")
    with pytest.raises(PackageError, match="exactly one"):
        read_package(root)


def test_canonical_zip_duplicate_refused(tmp_path):
    file = tmp_path / "ambiguous.pbit"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("DataModelSchema", "{}")
        z.writestr("datamodelschema", "{}")
    with pytest.raises(PackageError, match="Duplicate canonical ZIP"):
        read_package(file)


def test_json_bomb_size_limit(tmp_path, monkeypatch):
    import pbi_auditor.reader as reader
    file = tmp_path / "large.pbit"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("DataModelSchema", '{"model":{"tables":[]}}')
    monkeypatch.setattr(reader, "MAX_JSON_PART_BYTES", 10)
    with pytest.raises(PackageError, match="size limit"):
        read_package(file)
