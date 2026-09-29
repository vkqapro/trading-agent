from pathlib import Path


DECISION_LAB = Path(__file__).resolve().parents[1] / "dashboard_react" / "index.html"


def test_recent_model_decisions_exposes_filters_for_every_visible_column():
    source = DECISION_LAB.read_text(encoding="utf-8")

    for label in (
        "TIME FROM",
        "TIME TO",
        "SYMBOL",
        "SOURCE",
        "STRATEGY",
        "SIGNAL",
        "PROVIDER",
        "MODEL",
        "EFFECTIVE",
        "ORIGIN",
        "RISK",
        "EXECUTION",
        "LAST ENTRIES",
    ):
        assert label in source

    assert "const decisionRows = useMemo" in source
    assert "decisionDisplayRows = decisions.map" in source
    assert "model_action:row.model || row.model_action" in source
    assert "No rows match the active filters." in source
    assert "CLEAR FILTERS" in source
    assert "lastEntries" in source
    assert "slice(0, lastEntries)" in source
    assert "Date.parse(left.completed_at || left.requested_at || '')" in source


def test_decision_lab_filters_are_client_side_and_do_not_add_broker_access():
    source = DECISION_LAB.read_text(encoding="utf-8")
    filter_section = source[source.index("const decisionFilterValue"):source.index("const workerCard")]

    assert "decisionFilterOptions" in filter_section
    assert "decisionFilters[field]" in source
    assert "fetch(" not in filter_section
    assert "ib_insync" not in filter_section
    assert "ibkr" not in filter_section.lower()
