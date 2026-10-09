## Verified public Microsoft .pbit audit — October 9, 2026

[Successful GitHub Actions real-template run](https://github.com/jibrankazi/pbi-model-auditor-Public/actions/runs/37935480581) downloaded Microsoft's original COVID-19 US Tracking Sample.pbit file (1,276,277 bytes) and ran the production CLI.

| Static model inventory / findings | Measured |
|---|---:|
| Tables | 6 |
| Columns | 16 |
| Measures | 10 |
| Report pages | 2 |
| Visuals | 67 |
| Structural errors | 0 |
| Warnings | 5 |
| Auditor health score | 90/100 |

**Conclusion:** the CLI correctly read and analyzed a real externally published Power BI template, not a locally generated test package. Warnings are retained in the output artifact. **No live Power BI refresh, business data, Microsoft tenant connection or actual rendered-visual correctness was checked.**

---
# pbi-model-auditor

[![tests](https://github.com/jibrankazi/pbi-model-auditor-Public/actions/workflows/tests.yml/badge.svg)](https://github.com/jibrankazi/pbi-model-auditor-Public/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-none-lightgrey)
![license](https://img.shields.io/badge/license-MIT-green)

A command-line static analyser for Power BI template (`.pbit`) files. It reads the model and the report definition out of the package and cross-checks them, so broken field bindings, dead DAX and schema drift are caught before a report is published rather than after someone opens a blank visual.

```console
$ python audit.py --file samples/broken_sales_model.pbit

[PARSING] broken_sales_model.pbit (legacy report format)
[ANALYSIS] Auditing 6 tables, 24 columns, 6 measures, 3 report pages, 7 visuals...

✖ BROKEN VISUAL BINDINGS (3 found)
  - Missing column 'Sales'[GrossRevenue] (table exists, column missing)
      Page: Executive Summary | Visual: "Monthly Revenue" (clusteredBarChart)
  - Missing column 'Customers'[Country] (table exists, column missing)
      Page: Regional Drilldown (page filter)
  - Missing measure 'Sales'[Total Profit Margin] (table exists, measure missing)
      Page: Regional Drilldown | Visual: "Territory Map" (shapeMap)

✖ BROKEN DAX REFERENCES (1 found)
  - Missing object 'Sales'[UnitPrice]
      Referenced by 'Sales'[Revenue per Unit]

✖ BROKEN RELATIONSHIPS (1 found)
  - Missing column 'Sales'[ProductID] on the from side of a relationship
      'Sales'[ProductID] -> 'Products'[ProductID]

✖ BROKEN SORT-BY COLUMNS (1 found)
  - 'Customers'[Segment] sorts by missing column [SegmentOrder]
      Sort-by targets must exist in the same table

▲ ORPHANED MEASURES (3 found)
  - 'Sales'[Total Quantity]
      Not used by any visual, filter, RLS rule or dependent DAX expression
  - 'Sales'[Revenue per Unit]
      Not used by any visual, filter, RLS rule or dependent DAX expression
  - 'Finance'[Depreciation_Old_Calc]
      Not used by any visual, filter, RLS rule or dependent DAX expression

▲ UNUSED MODEL COLUMNS (5 found)
  - 'Sales'[Quantity]
      Referenced only by unreachable objects: 'Sales'[Total Quantity]
  - 'Customers'[LegacyCustomerID]
      Loaded to the model; 0 downstream references
  - 'Products'[SubCategory]
      Loaded to the model; 0 downstream references
  - 'Finance'[BudgetAmount]
      Loaded to the model; 0 downstream references
  - 'Territory'[TerritoryID]
      Loaded to the model; 0 downstream references

✔ Audit complete | Health Score: 42/100 (6 errors, 8 warnings)
```

That output is reproducible: `python samples/build_sample.py` writes the deliberately broken sample model, and auditing it prints exactly the above.

## Why

Power BI fails silently at the seam between the semantic model and the report. Rename a source column and the model refreshes fine, the file opens fine, and one visual on page 4 quietly renders nothing. Nobody notices until a stakeholder does. The information needed to catch it is already sitting in the `.pbit` file, in two JSON documents that never get compared to each other.

## What it checks

| Check | Severity | Catches |
| --- | --- | --- |
| Broken visual bindings | error | A visual, slicer or filter bound to a column, measure or hierarchy the model no longer has |
| Broken DAX references | error | A measure or calculated column pointing at a deleted object |
| Broken relationships | error | A relationship whose key column was dropped or renamed |
| Broken sort-by columns | error | `sortByColumn` targeting a column that does not exist |
| Orphaned measures | warning | Measures nothing can reach, transitively |
| Unused model columns | warning | Columns imported into memory with zero downstream references |
| Schema drift | report | Everything added, removed or redefined between two versions of the same file |

Orphan detection is reachability, not a text search. A measure used only by another measure that a visual uses is not orphaned; a measure used only by a measure that nothing uses is. The same walk explains itself in the output above, where `'Sales'[Quantity]` is flagged because its one consumer is itself unreachable.

## Quickstart

```bash
git clone https://github.com/jibrankazi/pbi-model-auditor-Public.git
cd pbi-model-auditor-Public

python samples/build_sample.py                             # write the sample models
python audit.py --file samples/broken_sales_model.pbit     # audit one file
```

No runtime dependencies: the standard library covers the whole job. To run the tests, `pip install -r requirements.txt && pytest`. To get a `pbi-audit` command on your PATH, `pip install -e .`.

```
--file       PATH   the .pbit to audit                        (required)
--baseline   PATH   an earlier .pbit; adds a schema drift report
--json              machine-readable output for CI
--strict            exit non-zero on warnings as well as errors
--no-color          disable ANSI colour
```

Exit codes are `0` clean, `1` findings, `2` unreadable file, so it drops straight into a pipeline:

```yaml
- run: python audit.py --file dist/sales.pbit --strict
```

## Schema drift

Point it at the previous version of the same file and it explains *why* the bindings broke:

```console
$ python audit.py --file samples/broken_sales_model.pbit \
                  --baseline samples/baseline_sales_model.pbit

[DRIFT] baseline_sales_model.pbit -> broken_sales_model.pbit

✖ REMOVED FROM MODEL (7)
  - column: 'Sales'[GrossRevenue]
  - column: 'Sales'[UnitPrice]
  - column: 'Sales'[ProductID]
  - column: 'Customers'[Country]
  - column: 'Customers'[SegmentRank]
  - measure: 'Sales'[Total Profit Margin]
  - measure: 'Sales'[Total Cost]

▲ CHANGED DEFINITION (2)
  - column: 'Customers'[Segment]  -- sortByColumn 'SegmentRank' -> 'SegmentOrder'
  - measure: 'Sales'[Total Revenue]  -- DAX expression changed

✔ ADDED TO MODEL (2)
  - column: 'Sales'[NetRevenue]
  - measure: 'Sales'[Revenue per Unit]

9 change(s) can break existing report bindings.
```

`GrossRevenue` out, `NetRevenue` in. One rename upstream, five downstream failures.

## How it works

A `.pbit` is a ZIP. `DataModelSchema` holds the model as TMSL JSON (tables, columns, measures, relationships, RLS); `Report/Layout` holds the report, with its query definitions embedded as JSON strings. Field references live in both, expressed as query expression nodes whose `SourceRef` points at an alias declared in the enclosing `From` clause. The auditor resolves those aliases, builds a catalog from the model side, then walks every reference on the report side against it, and finally inverts the question to find model objects nothing reaches.

```
pbi-audit
├── reader.py — unzip, decode UTF-16 LE or UTF-8 parts / fails loudly on a .pbix with a compiled DataModel
├── model.py — DataModelSchema to catalog: tables, columns, measures, hierarchies, relationships, RLS
│   └── auto-generated date tables and RowNumber columns are excluded from unused-object checks
├── report.py — Report/Layout or Report/definition to a flat reference list
│   └── resolves SourceRef aliases via the From clause; falls back to queryRef strings
├── dax.py — reference scanner over measure and calculated-column expressions
│   └── strips comments and string literals; names created by ADDCOLUMNS are treated as local
├── checks.py — cross-check both directions, then reachability over the DAX dependency graph
│   └── health score = 100 - 7 per error - 2 per warning, floored at 0
├── drift.py — catalog-to-catalog diff between two versions of a file
└── tests/ — 46 tests, including end-to-end runs over a generated broken model
```

## Scope and limitations

- **Report formats.** Legacy `Report/Layout` and the newer PBIR layout (`Report/definition/**`) are both read. PBIR became the default report format during 2026, so which one you get depends on the Desktop version that exported the template.
- **Semantic model format.** `DataModelSchema` (TMSL JSON) only. TMDL semantic models in `.pbip` projects are not parsed yet.
- **`.pbix` is not supported.** A `.pbix` carries a compiled binary `DataModel` rather than a JSON schema. Export the report as a template (File > Export > Power BI template) and audit that. The tool says so rather than failing obscurely.
- **Unqualified DAX references are only flagged when nothing in the model matches.** `[Name]` is ambiguous in DAX, so the scanner stays conservative and would rather miss a finding than invent one.
- **The sample models are synthetic.** They are generated by `samples/build_sample.py` to mimic the `.pbit` container layout so the tool can be tested end to end. They are inputs for static analysis, not files Power BI Desktop is expected to open. The tool has been validated against these fixtures; run it against your own templates before trusting it in a pipeline.
- Custom visuals that store bindings outside the standard query expression shape may not be fully covered.

## Repository layout

```
audit.py                  run without installing
pbi_auditor/              the package
samples/build_sample.py   generates both sample .pbit files
tests/                    pytest suite
```

## License

MIT
