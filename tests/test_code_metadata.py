"""The `_metadata` blocks of the MESSY config and the packaged static table stay in step."""

from pathlib import Path

import polars as pl
import yaml

from HIRID_MEDS.code_descriptions import STATIC_DESCRIPTIONS_CSV

ROOT = Path(__file__).parents[1] / "src" / "HIRID_MEDS"
CONFIG_FP = ROOT / "configs" / "messy.yaml"


def _events():
    for table, body in yaml.safe_load(CONFIG_FP.read_text()).items():
        if table in ("sources", "etl", "_defaults"):
            continue
        for name, event in body.items():
            if name != "_table":
                yield table, name, event


def _is_literal(code: str) -> bool:
    return "$" not in code


def test_component_coded_events_declare_a_description():
    """Every event whose code has components is described, including its fallback codes."""
    undescribed = []
    for table, name, event in _events():
        if _is_literal(str(event["code"])):
            continue
        blocks = event.get("_metadata", {})
        if not any("description" in block for block in blocks.values()):
            undescribed.append(f"{table}/{name}")
        # A code that falls back to the raw id has no reference row, so it needs a `_self` block.
        if "??" in event["code"] and "variable_name" in event["code"]:
            assert "description" in blocks.get("_self", {}), f"{table}/{name}"
    assert not undescribed, undescribed


def test_no_block_adds_vocabulary_parents():
    """Vocabulary parents come from suMEDS (`sumeds_rules.yaml`), not from the extraction."""
    with_parents = [
        f"{table}/{name}"
        for table, name, event in _events()
        for block in event.get("_metadata", {}).values()
        if "parent_codes" in block
    ]
    assert not with_parents, with_parents


def test_static_table_covers_exactly_the_literal_codes():
    static = pl.read_csv(STATIC_DESCRIPTIONS_CSV.read_bytes(), infer_schema=False)
    assert static.columns == ["code", "description", "source"]
    assert static.null_count().sum_horizontal().item() == 0
    assert not static["code"].is_duplicated().any()
    literal = {str(e["code"]) for _, _, e in _events() if _is_literal(str(e["code"]))}
    assert literal, "no literal-coded events found; the detection is stale"
    assert set(static["code"]) == literal
