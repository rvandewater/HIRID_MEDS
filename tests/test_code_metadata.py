"""The shipped LOINC table and the `_metadata` blocks of the MESSY config stay in step."""

from pathlib import Path

import polars as pl
import yaml
from dftly import Parser

ROOT = Path(__file__).parents[1] / "src" / "HIRID_MEDS"
CONFIG_FP = ROOT / "configs" / "messy.yaml"
LOINC_FP = ROOT / "metadata" / "observation_loinc.csv"
OBSERVATION = "raw_stage/observation_tables/parquet"


def _events():
    for table, body in yaml.safe_load(CONFIG_FP.read_text()).items():
        if table in ("sources", "etl", "_defaults"):
            continue
        for name, event in body.items():
            if name != "_table":
                yield table, name, event


def _is_literal(code: str) -> bool:
    return "$" not in code


def test_loinc_table_is_complete_and_sourced():
    df = pl.read_csv(LOINC_FP, infer_schema=False)
    assert df.columns == ["variableid", "loinc_code", "match", "source"]
    assert df.null_count().sum_horizontal().item() == 0
    assert not df["variableid"].is_duplicated().any()
    assert df["variableid"].str.contains(r"^\d+$").all()
    assert df["loinc_code"].str.contains(r"^\d{1,6}-\d$").all()
    assert set(df["match"]) == {"loinc_name", "inferred_name_match"}
    assert df["source"].str.contains("LOINC 2.80").all()
    inferred = df.filter(pl.col("match") == "inferred_name_match")
    assert inferred["source"].str.starts_with("inferred:").all()


def test_parent_codes_encode_the_loinc_table():
    """The observation block's `parent_codes` chain yields exactly the table's LOINC codes."""
    event = yaml.safe_load(CONFIG_FP.read_text())[OBSERVATION]["observation"]
    expr = Parser()(
        event["_metadata"]["hirid_variable_reference"]["parent_codes"]
    ).polars_expr
    table = pl.read_csv(LOINC_FP, infer_schema=False)
    expected = dict(
        zip(table["variableid"], "LOINC/" + table["loinc_code"], strict=True)
    )
    unmapped = ["100", "2000030", "200000300", "24000170"]
    ids = [*expected, *unmapped]
    # The reference CSV is read as strings, but a parquet copy (as MESSY-synth writes) has int ids.
    for dtype in (pl.String, pl.Int64):
        ref = pl.DataFrame(
            {
                "Source Table": ["Observation"] * len(ids) + ["Pharma"] * len(ids),
                "ID": ids + ids,
            }
        ).with_columns(pl.col("ID").cast(dtype))
        got = ref.select(
            pl.col("Source Table"), pl.col("ID").cast(pl.String), parent=expr
        )
        obs = got.filter(pl.col("Source Table") == "Observation")
        assert dict(zip(obs["ID"], obs["parent"], strict=True)) == {
            **expected,
            **dict.fromkeys(unmapped),
        }
        assert got.filter(pl.col("Source Table") == "Pharma")["parent"].is_null().all()


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
