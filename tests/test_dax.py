from pbi_auditor import dax


def refs(expression):
    return [(r.table, r.name) for r in dax.extract_refs(expression)]


def test_qualified_reference():
    assert refs("SUM(Sales[Amount])") == [("Sales", "Amount")]


def test_quoted_table_name():
    assert refs("SUM('Sales Header'[Net Amount])") == [("Sales Header", "Net Amount")]


def test_unqualified_reference_has_no_table():
    assert refs("DIVIDE([Total Cost], [Total Revenue])") == [
        (None, "Total Cost"),
        (None, "Total Revenue"),
    ]


def test_whitespace_before_bracket_is_not_a_table():
    assert refs("RETURN [Total Revenue]") == [(None, "Total Revenue")]


def test_string_literals_are_ignored():
    assert refs('IF(Sales[Region] = "Table[Column]", 1, 0)') == [("Sales", "Region")]


def test_comments_are_ignored():
    expression = """
    // legacy: SUM(Sales[OldColumn])
    /* SUM(Sales[AlsoOld]) */
    SUM(Sales[Amount]) -- SUM(Sales[Never])
    """
    assert refs(expression) == [("Sales", "Amount")]


def test_nested_and_mixed_references():
    expression = "CALCULATE([Total Revenue], FILTER(ALL(Customers), Customers[Segment] = \"SMB\"))"
    assert refs(expression) == [(None, "Total Revenue"), ("Customers", "Segment")]


def test_local_names_collects_string_literals():
    expression = 'SUMX(ADDCOLUMNS(Sales, "Margin", [Total Revenue] * 0.2), [Margin])'
    assert "margin" in dax.local_names(expression)


def test_empty_expression():
    assert refs("") == []
    assert dax.local_names("") == set()
