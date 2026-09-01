#!/usr/bin/env python3
"""Generate the synthetic .pbit fixtures used by the demo and the tests.

Two files are written:

    baseline_sales_model.pbit   a healthy earlier version of the same report
    broken_sales_model.pbit     the same report after a source-schema change

Everything here is invented. There is no real data, no real report and no
connection string - the fixtures mimic the .pbit *container* layout
(a ZIP holding a UTF-16 LE `DataModelSchema` and `Report/Layout`) so the
auditor can be exercised end to end without shipping someone's model.

They are inputs for static analysis, not files Power BI Desktop is expected
to open.

Defects planted in broken_sales_model.pbit:
    1. visual binds to Sales[GrossRevenue]      - column renamed to NetRevenue
    2. visual binds to [Total Profit Margin]    - measure deleted
    3. page filter binds to Customers[Country]  - column dropped upstream
    4. DAX in [Revenue per Unit] uses Sales[UnitPrice] - column dropped
    5. relationship uses Sales[ProductID]       - column dropped
    6. Customers[Segment] sorts by [SegmentOrder] - column never existed
    plus three unreferenced measures and four unreferenced columns
"""

from __future__ import annotations

import argparse
import codecs
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------
def _column(name: str, data_type: str = "string", **extra) -> dict:
    return {"name": name, "dataType": data_type, **extra}


def _measure(name: str, expression: str) -> dict:
    return {"name": name, "expression": expression}


def build_model(broken: bool) -> dict:
    """Return a TMSL DataModelSchema. `broken=False` gives the baseline."""

    sales_columns = [
        _column("OrderID"),
        _column("OrderDate", "dateTime"),
        _column("CustomerID"),
        _column("Quantity", "int64"),
    ]
    if broken:
        # The source system renamed GrossRevenue and dropped two columns.
        sales_columns.append(_column("NetRevenue", "double"))
    else:
        sales_columns.append(_column("GrossRevenue", "double"))
        sales_columns.append(_column("UnitPrice", "double"))
        sales_columns.append(_column("ProductID"))

    revenue_column = "NetRevenue" if broken else "GrossRevenue"
    sales_measures = [
        _measure("Total Revenue", f"SUM(Sales[{revenue_column}])"),
        _measure("Total Orders", "DISTINCTCOUNT(Sales[OrderID])"),
        _measure("Total Quantity", "SUM(Sales[Quantity])"),
    ]
    if broken:
        # Added during the refactor, still pointing at the dropped column.
        sales_measures.append(
            _measure("Revenue per Unit", "DIVIDE([Total Revenue], SUM(Sales[UnitPrice]))")
        )
    else:
        sales_measures.append(_measure("Total Profit Margin", "DIVIDE([Total Revenue] - [Total Cost], [Total Revenue])"))
        sales_measures.append(_measure("Total Cost", "SUMX(Sales, Sales[Quantity] * Sales[UnitPrice])"))

    customer_columns = [
        _column("CustomerID"),
        _column("CustomerName"),
        _column("Segment", sortByColumn="SegmentOrder" if broken else "SegmentRank"),
        _column("Region"),
        _column("LegacyCustomerID"),
    ]
    if not broken:
        customer_columns.append(_column("Country"))
        customer_columns.append(_column("SegmentRank", "int64"))

    tables = [
        {"name": "Sales", "columns": sales_columns, "measures": sales_measures},
        {"name": "Customers", "columns": customer_columns},
        {
            "name": "Products",
            "columns": [
                _column("ProductID"),
                _column("ProductName"),
                _column("Category"),
                _column("SubCategory"),
            ],
        },
        {
            "name": "Calendar",
            "columns": [
                _column("Date", "dateTime"),
                _column("Year", "int64"),
                _column("Quarter"),
                _column("MonthName", sortByColumn="MonthNumber"),
                _column("MonthNumber", "int64"),
            ],
            "hierarchies": [
                {
                    "name": "Calendar Drill",
                    "levels": [
                        {"name": "Year", "ordinal": 0, "column": "Year"},
                        {"name": "Quarter", "ordinal": 1, "column": "Quarter"},
                        {"name": "Month", "ordinal": 2, "column": "MonthName"},
                    ],
                }
            ],
        },
        {
            "name": "Finance",
            "columns": [
                _column("CostCentre"),
                _column("ActualAmount", "double"),
                _column("BudgetAmount", "double"),
            ],
            "measures": [
                _measure("Operating Cost", "SUM(Finance[ActualAmount])"),
                _measure(
                    "Depreciation_Old_Calc",
                    "// superseded by the 2024 cost model\nSUMX(Finance, Finance[ActualAmount] * 0.15)",
                ),
            ],
        },
        {"name": "Territory", "columns": [_column("TerritoryID"), _column("TerritoryName")]},
    ]

    for table in tables:
        table.setdefault("partitions", [
            {
                "name": f"{table['name']}-partition",
                "mode": "import",
                "source": {"type": "m", "expression": f'let Source = Csv.Document(File.Contents("C:\\\\demo\\\\{table["name"]}.csv")) in Source'},
            }
        ])

    relationships = [
        {
            "name": "sales-customers",
            "fromTable": "Sales",
            "fromColumn": "CustomerID",
            "toTable": "Customers",
            "toColumn": "CustomerID",
        },
        {
            "name": "sales-calendar",
            "fromTable": "Sales",
            "fromColumn": "OrderDate",
            "toTable": "Calendar",
            "toColumn": "Date",
        },
        {
            # Sales[ProductID] no longer exists in the broken model.
            "name": "sales-products",
            "fromTable": "Sales",
            "fromColumn": "ProductID",
            "toTable": "Products",
            "toColumn": "ProductID",
        },
        {
            "name": "customers-territory",
            "fromTable": "Customers",
            "fromColumn": "Region",
            "toTable": "Territory",
            "toColumn": "TerritoryName",
        },
    ]

    return {
        "name": "SalesPerformance",
        "compatibilityLevel": 1567,
        "model": {
            "culture": "en-GB",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-GB",
            "tables": tables,
            "relationships": relationships,
            "roles": [
                {
                    "name": "Regional Manager",
                    "modelPermission": "read",
                    "tablePermissions": [
                        {"name": "Customers", "filterExpression": "Customers[Region] = USERNAME()"}
                    ],
                }
            ],
            "annotations": [{"name": "ClientCompatibilityLevel", "value": "700"}],
        },
    }


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _query(fields: list[dict]) -> tuple[dict, dict]:
    """Build a prototypeQuery plus its projections from a field list."""
    aliases: dict[str, str] = {}
    from_clause: list[dict] = []
    select: list[dict] = []
    projections: dict[str, list[dict]] = {}

    for field in fields:
        table, name, kind = field["table"], field["name"], field["kind"]
        if table not in aliases:
            base = table[0].lower()
            alias = base if base not in aliases.values() else f"{base}{len(aliases)}"
            aliases[table] = alias
            from_clause.append({"Name": alias, "Entity": table, "Type": 0})
        source = {"Expression": {"SourceRef": {"Source": aliases[table]}}, "Property": name}

        if kind == "measure":
            entry = {"Measure": source, "Name": f"{table}.{name}", "NativeReferenceName": name}
            query_ref = f"{table}.{name}"
        elif kind == "sum":
            entry = {
                "Aggregation": {"Expression": {"Column": source}, "Function": 0},
                "Name": f"Sum({table}.{name})",
                "NativeReferenceName": f"Sum of {name}",
            }
            query_ref = f"Sum({table}.{name})"
        else:
            entry = {"Column": source, "Name": f"{table}.{name}", "NativeReferenceName": name}
            query_ref = f"{table}.{name}"

        select.append(entry)
        projections.setdefault(field["role"], []).append({"queryRef": query_ref, "active": True})

    return {"Version": 2, "From": from_clause, "Select": select}, projections


def _visual(name: str, visual_type: str, title: str, position: tuple[int, int, int, int], fields: list[dict]) -> dict:
    prototype, projections = _query(fields)
    x, y, width, height = position
    config = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": width, "height": height, "tabOrder": 0}}],
        "singleVisual": {
            "visualType": visual_type,
            "projections": projections,
            "prototypeQuery": prototype,
            "drillFilterOtherVisuals": True,
            "vcObjects": {
                "title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]
            },
        },
    }
    return {
        "x": x,
        "y": y,
        "z": 0,
        "width": width,
        "height": height,
        "config": json.dumps(config),
        "filters": "[]",
    }


def _filter(name: str, table: str, column: str) -> str:
    return json.dumps(
        [
            {
                "name": name,
                "type": "Categorical",
                "expression": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": column}},
                "howCreated": 0,
            }
        ]
    )


def build_layout(broken: bool) -> dict:
    revenue_column = "GrossRevenue"  # the report was never updated after the rename
    margin_measure = "Total Profit Margin"
    page_filter_column = "Country"

    executive = {
        "id": 0,
        "name": "ReportSection5f1c",
        "displayName": "Executive Summary",
        "ordinal": 0,
        "width": 1280,
        "height": 720,
        "displayOption": 1,
        "filters": "[]",
        "visualContainers": [
            _visual(
                "a1b2c3d4e5",
                "card",
                "Total Revenue",
                (24, 24, 280, 160),
                [{"role": "Values", "kind": "measure", "table": "Sales", "name": "Total Revenue"}],
            ),
            _visual(
                "b2c3d4e5f6",
                "card",
                "Orders",
                (320, 24, 280, 160),
                [{"role": "Values", "kind": "measure", "table": "Sales", "name": "Total Orders"}],
            ),
            _visual(
                "c3d4e5f6a7",
                "clusteredBarChart",
                "Monthly Revenue",
                (24, 200, 700, 400),
                [
                    {"role": "Category", "kind": "column", "table": "Calendar", "name": "MonthName"},
                    {"role": "Y", "kind": "sum", "table": "Sales", "name": revenue_column},
                ],
            ),
            _visual(
                "d4e5f6a7b8",
                "slicer",
                "Segment",
                (740, 200, 260, 400),
                [{"role": "Values", "kind": "column", "table": "Customers", "name": "Segment"}],
            ),
        ],
    }

    regional = {
        "id": 1,
        "name": "ReportSection7a2d",
        "displayName": "Regional Drilldown",
        "ordinal": 1,
        "width": 1280,
        "height": 720,
        "displayOption": 1,
        "filters": _filter("Filter_Country", "Customers", page_filter_column),
        "visualContainers": [
            _visual(
                "e5f6a7b8c9",
                "shapeMap",
                "Territory Map",
                (24, 24, 620, 560),
                [
                    {"role": "Category", "kind": "column", "table": "Territory", "name": "TerritoryName"},
                    {"role": "Values", "kind": "measure", "table": "Sales", "name": margin_measure},
                ],
            ),
            _visual(
                "f6a7b8c9d0",
                "tableEx",
                "Customer Detail",
                (664, 24, 580, 560),
                [
                    {"role": "Values", "kind": "column", "table": "Customers", "name": "CustomerName"},
                    {"role": "Values", "kind": "column", "table": "Customers", "name": "Region"},
                    {"role": "Values", "kind": "column", "table": "Products", "name": "ProductName"},
                    {"role": "Values", "kind": "column", "table": "Products", "name": "Category"},
                    {"role": "Values", "kind": "measure", "table": "Sales", "name": "Total Revenue"},
                ],
            ),
        ],
    }

    cost = {
        "id": 2,
        "name": "ReportSection9c4e",
        "displayName": "Cost Detail",
        "ordinal": 2,
        "width": 1280,
        "height": 720,
        "displayOption": 1,
        "filters": "[]",
        "visualContainers": [
            _visual(
                "a7b8c9d0e1",
                "pivotTable",
                "Cost by Centre",
                (24, 24, 1200, 560),
                [
                    {"role": "Rows", "kind": "column", "table": "Finance", "name": "CostCentre"},
                    {"role": "Values", "kind": "measure", "table": "Finance", "name": "Operating Cost"},
                    {"role": "Columns", "kind": "column", "table": "Calendar", "name": "Quarter"},
                ],
            )
        ],
    }

    return {
        "id": 0,
        "resourcePackages": [],
        "sections": [executive, regional, cost],
        "config": json.dumps({"version": "5.43", "themeCollection": {"baseTheme": {"name": "CY24SU06"}}}),
        "layoutOptimization": 0,
        "filters": _filter("Filter_Year", "Calendar", "Year"),
    }


# ---------------------------------------------------------------------------
# packaging
# ---------------------------------------------------------------------------
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="json" ContentType="" />'
    '<Override PartName="/DataModelSchema" ContentType="" />'
    '<Override PartName="/Report/Layout" ContentType="" />'
    '<Override PartName="/Version" ContentType="" />'
    "</Types>"
)


def _utf16(text: str) -> bytes:
    return codecs.BOM_UTF16_LE + text.encode("utf-16-le")


def write_pbit(path: Path, model: dict, layout: dict, description: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("Version", _utf16("1.28"))
        zf.writestr("DataModelSchema", _utf16(json.dumps(model, indent=2)))
        zf.writestr("Report/Layout", _utf16(json.dumps(layout)))
        zf.writestr("Settings", _utf16(json.dumps({"Description": description})))
        zf.writestr("Metadata", _utf16(json.dumps({"Version": 3, "AutoCreatedRelationships": []})))
        zf.writestr("DiagramLayout", _utf16(json.dumps({"version": "1.1.0", "diagrams": []})))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the synthetic .pbit fixtures.")
    parser.add_argument("--out", default=str(HERE), help="output directory")
    args = parser.parse_args()
    out = Path(args.out)

    baseline = write_pbit(
        out / "baseline_sales_model.pbit",
        build_model(broken=False),
        build_layout(broken=False),
        "Synthetic baseline sales model - fictional data",
    )
    broken = write_pbit(
        out / "broken_sales_model.pbit",
        build_model(broken=True),
        build_layout(broken=True),
        "Synthetic sales model after an upstream schema change - fictional data",
    )
    for path in (baseline, broken):
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
