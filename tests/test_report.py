import json

from pbi_auditor.reader import Package
from pbi_auditor.report import _split_query_ref, _visual_label, extract_report


def _layout(config: dict, filters: str = "[]") -> dict:
    return {
        "sections": [
            {
                "name": "ReportSection1",
                "displayName": "Page 1",
                "filters": "[]",
                "visualContainers": [{"config": json.dumps(config), "filters": filters}],
            }
        ],
        "filters": "[]",
    }


def _package(layout: dict) -> Package:
    return Package(path="memory.pbit", model_schema={"model": {}}, layout=layout)


def test_source_alias_resolves_to_entity():
    config = {
        "name": "v1",
        "singleVisual": {
            "visualType": "clusteredBarChart",
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
                "Select": [
                    {
                        "Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Amount"},
                        "Name": "Sales.Amount",
                    }
                ],
            },
        },
    }
    refs = extract_report(_package(_layout(config))).refs
    assert [(r.kind, r.table, r.name) for r in refs] == [("column", "Sales", "Amount")]


def test_measure_and_aggregation_both_found():
    config = {
        "singleVisual": {
            "visualType": "card",
            "prototypeQuery": {
                "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
                "Select": [
                    {"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Total Revenue"}},
                    {
                        "Aggregation": {
                            "Expression": {
                                "Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Quantity"}
                            },
                            "Function": 0,
                        }
                    },
                ],
            },
        }
    }
    refs = extract_report(_package(_layout(config))).refs
    assert ("measure", "Sales", "Total Revenue") in [(r.kind, r.table, r.name) for r in refs]
    assert ("column", "Sales", "Quantity") in [(r.kind, r.table, r.name) for r in refs]


def test_entity_source_ref_needs_no_alias():
    config = {
        "singleVisual": {
            "visualType": "slicer",
            "prototypeQuery": {
                "Select": [
                    {"Column": {"Expression": {"SourceRef": {"Entity": "Customers"}}, "Property": "Segment"}}
                ]
            },
        }
    }
    refs = extract_report(_package(_layout(config))).refs
    assert [(r.table, r.name) for r in refs] == [("Customers", "Segment")]


def test_visual_filters_are_scanned():
    config = {"singleVisual": {"visualType": "card"}}
    filters = json.dumps(
        [
            {
                "name": "f1",
                "expression": {
                    "Column": {"Expression": {"SourceRef": {"Entity": "Calendar"}}, "Property": "Year"}
                },
            }
        ]
    )
    refs = extract_report(_package(_layout(config, filters=filters))).refs
    assert [(r.table, r.name, r.origin) for r in refs] == [("Calendar", "Year", "visual filter")]


def test_query_ref_fallback_when_no_structured_query():
    config = {"singleVisual": {"visualType": "card", "projections": {"Values": [{"queryRef": "Sales.Amount"}]}}}
    refs = extract_report(_package(_layout(config))).refs
    assert [(r.kind, r.table, r.name) for r in refs] == [("field", "Sales", "Amount")]


def test_split_query_ref_unwraps_aggregation():
    assert _split_query_ref("Sum(Sales.Amount)") == ("Sales", "Amount")
    assert _split_query_ref("Sales.Total Revenue") == ("Sales", "Total Revenue")
    assert _split_query_ref("Amount") == (None, "Amount")


def test_visual_label_uses_the_title_when_present():
    config = {
        "singleVisual": {
            "visualType": "card",
            "vcObjects": {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Revenue'"}}}}}]},
        }
    }
    assert _visual_label(config) == '"Revenue" (card)'
    assert _visual_label({"singleVisual": {"visualType": "card"}}, "abcdef123456") == "card #abcdef12"


def test_pbir_visual_json_is_read():
    pkg = Package(
        path="memory.pbit",
        model_schema={"model": {}},
        pbir_parts={
            "Report/definition/pages/p1/page.json": {"name": "p1", "displayName": "Overview"},
            "Report/definition/pages/p1/visuals/v1/visual.json": {
                "visual": {
                    "visualType": "barChart",
                    "query": {
                        "queryState": {
                            "Y": {
                                "projections": [
                                    {
                                        "field": {
                                            "Measure": {
                                                "Expression": {"SourceRef": {"Entity": "Sales"}},
                                                "Property": "Total Revenue",
                                            }
                                        }
                                    }
                                ]
                            }
                        }
                    },
                }
            },
        },
    )
    inventory = extract_report(pkg)
    assert inventory.report_format == "pbir"
    assert [(r.kind, r.table, r.name, r.page) for r in inventory.refs] == [
        ("measure", "Sales", "Total Revenue", "Overview")
    ]
