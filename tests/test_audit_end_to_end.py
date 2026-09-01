import zipfile
from pathlib import Path

import pytest

import audit as cli  # the CLI module at the repo root
from build_sample import build_layout, build_model, write_pbit
from pbi_auditor import audit_file, catalog_of, compare
from pbi_auditor.checks import (
    BROKEN_BINDING,
    BROKEN_DAX,
    BROKEN_RELATIONSHIP,
    BROKEN_SORT_BY,
    ORPHANED_MEASURE,
    UNUSED_COLUMN,
)
from pbi_auditor.reader import PackageError, read_package


@pytest.fixture(scope="module")
def broken(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pbit")
    return write_pbit(out / "broken.pbit", build_model(broken=True), build_layout(broken=True), "test")


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("pbit")
    return write_pbit(out / "baseline.pbit", build_model(broken=False), build_layout(broken=False), "test")


def codes(result):
    return sorted({f.code for f in result.findings})


def summaries(result, code):
    return [f.summary for f in result.findings if f.code == code]


# --- reading ---------------------------------------------------------------
def test_utf16_parts_round_trip(broken):
    package = read_package(broken)
    assert package.model_schema["name"] == "SalesPerformance"
    assert package.report_format == "legacy"
    assert len(package.layout["sections"]) == 3


def test_non_zip_is_rejected(tmp_path):
    bad = tmp_path / "not.pbit"
    bad.write_text("just text")
    with pytest.raises(PackageError, match="not a ZIP"):
        read_package(bad)


def test_pbix_with_compiled_datamodel_is_rejected(tmp_path):
    pbix = tmp_path / "report.pbix"
    with zipfile.ZipFile(pbix, "w") as zf:
        zf.writestr("DataModel", b"\x00\x01binary")
        zf.writestr("Report/Layout", b"{}")
    with pytest.raises(PackageError, match="compiled DataModel"):
        read_package(pbix)


def test_missing_file(tmp_path):
    with pytest.raises(PackageError, match="not found"):
        read_package(tmp_path / "nope.pbit")


# --- the baseline model is clean ------------------------------------------
def test_baseline_has_no_errors(baseline):
    result = audit_file(baseline)
    assert result.errors == []
    assert result.health_score > 80


# --- every planted defect is detected -------------------------------------
def test_all_defect_classes_are_found(broken):
    result = audit_file(broken)
    assert codes(result) == sorted(
        [BROKEN_BINDING, BROKEN_DAX, BROKEN_RELATIONSHIP, BROKEN_SORT_BY, ORPHANED_MEASURE, UNUSED_COLUMN]
    )


def test_renamed_column_breaks_its_visual(broken):
    found = summaries(audit_file(broken), BROKEN_BINDING)
    assert any("'Sales'[GrossRevenue]" in s for s in found)


def test_deleted_measure_breaks_its_visual(broken):
    found = summaries(audit_file(broken), BROKEN_BINDING)
    assert any("'Sales'[Total Profit Margin]" in s for s in found)


def test_page_filter_binding_is_checked(broken):
    result = audit_file(broken)
    country = [f for f in result.findings if "'Customers'[Country]" in f.summary]
    assert country and "page filter" in country[0].location


def test_broken_dax_reference_names_its_owner(broken):
    result = audit_file(broken)
    dax_findings = [f for f in result.findings if f.code == BROKEN_DAX]
    assert len(dax_findings) == 1
    assert "'Sales'[UnitPrice]" in dax_findings[0].summary
    assert "Revenue per Unit" in dax_findings[0].location


def test_relationship_on_dropped_column_is_flagged(broken):
    assert any("'Sales'[ProductID]" in s for s in summaries(audit_file(broken), BROKEN_RELATIONSHIP))


def test_sort_by_missing_column_is_flagged(broken):
    assert any("SegmentOrder" in s for s in summaries(audit_file(broken), BROKEN_SORT_BY))


def test_orphaned_measures(broken):
    found = summaries(audit_file(broken), ORPHANED_MEASURE)
    assert any("Depreciation_Old_Calc" in s for s in found)
    assert not any("Total Revenue" in s for s in found)  # used by two visuals


def test_measure_used_only_by_another_used_measure_is_not_orphaned(baseline):
    # [Total Cost] is referenced only by [Total Profit Margin], which a visual uses.
    found = summaries(audit_file(baseline), ORPHANED_MEASURE)
    assert not any("Total Cost" in s for s in found)


def test_unused_column_reports_its_unreachable_consumer(broken):
    result = audit_file(broken)
    quantity = [f for f in result.findings if f.code == UNUSED_COLUMN and "[Quantity]" in f.summary]
    assert quantity and "Total Quantity" in quantity[0].location


def test_relationship_and_hierarchy_columns_count_as_used(broken):
    unused = summaries(audit_file(broken), UNUSED_COLUMN)
    assert not any("[CustomerID]" in s for s in unused)  # relationship key
    assert not any("[Quarter]" in s for s in unused)  # hierarchy level


def test_rls_column_counts_as_used(broken):
    assert not any("'Customers'[Region]" in s for s in summaries(audit_file(broken), UNUSED_COLUMN))


def test_health_score_is_deterministic(broken):
    result = audit_file(broken)
    assert result.health_score == max(0, 100 - 7 * len(result.errors) - 2 * len(result.warnings))


# --- drift -----------------------------------------------------------------
def test_drift_detects_the_rename(baseline, broken):
    report = compare(catalog_of(baseline), catalog_of(broken), "baseline", "broken")
    removed = [i.name for i in report.items if i.change == "removed"]
    added = [i.name for i in report.items if i.change == "added"]
    assert "'Sales'[GrossRevenue]" in removed
    assert "'Sales'[NetRevenue]" in added


def test_drift_reports_measure_expression_change(baseline, broken):
    report = compare(catalog_of(baseline), catalog_of(broken), "baseline", "broken")
    changed = [(i.name, i.detail) for i in report.items if i.change == "changed"]
    assert ("'Sales'[Total Revenue]", "DAX expression changed") in changed


def test_drift_of_a_file_against_itself_is_empty(broken):
    report = compare(catalog_of(broken), catalog_of(broken), "a", "b")
    assert report.items == []


# --- CLI contract ----------------------------------------------------------
def test_cli_exit_code_on_errors(broken, capsys):
    assert cli.main(["--file", str(broken), "--no-color"]) == 1
    assert "BROKEN VISUAL BINDINGS" in capsys.readouterr().out


def test_cli_strict_fails_on_warnings_only(baseline, capsys):
    assert cli.main(["--file", str(baseline), "--no-color"]) == 0
    capsys.readouterr()
    assert cli.main(["--file", str(baseline), "--no-color", "--strict"]) == 1


def test_cli_json_output_is_parseable(broken, capsys):
    import json

    cli.main(["--file", str(broken), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["errors"] == 6
    assert payload["findings"][0]["code"] == BROKEN_BINDING


def test_cli_unreadable_file_exits_two(tmp_path, capsys):
    bad = tmp_path / "bad.pbit"
    bad.write_text("nope")
    assert cli.main(["--file", str(bad)]) == 2


# --- encoding tolerance ----------------------------------------------------
def test_utf8_encoded_parts_are_read(tmp_path):
    import json

    path = tmp_path / "utf8.pbit"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DataModelSchema", json.dumps(build_model(broken=True)).encode("utf-8"))
        zf.writestr("Report/Layout", json.dumps(build_layout(broken=True)).encode("utf-8"))
    result = audit_file(path)
    assert len(result.errors) == 6


def test_model_without_a_report_part_is_reported(tmp_path):
    import json

    path = tmp_path / "modelonly.pbit"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DataModelSchema", json.dumps(build_model(broken=True)).encode("utf-8"))
    result = audit_file(path)
    assert result.report_format == "none"
    assert result.pages == 0


# --- Windows console encoding ---------------------------------------------
def test_marks_degrade_when_the_stream_cannot_encode_them():
    from pbi_auditor import render

    class Cp1252Stream:
        encoding = "cp1252"

    assert render.marks(Cp1252Stream())[render.ERROR_MARK] == "[X]"

    class Utf8Stream:
        encoding = "utf-8"

    assert render.marks(Utf8Stream())[render.ERROR_MARK] == render.ERROR_MARK


def test_render_never_raises_on_a_legacy_codepage(broken, monkeypatch):
    import io

    from pbi_auditor import render

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", stream)
    text = render.render_audit(audit_file(broken), render.Style(False))
    stream.write(text)  # would raise UnicodeEncodeError if a glyph leaked through
    stream.flush()
