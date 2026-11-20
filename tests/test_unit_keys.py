"""The pre-stage that stages `pharma_unit_in_code`, on the table layouts MEDS-Extract reads."""

import polars as pl
import pytest

from HIRID_MEDS.unit_keys import SIDE_TABLE_NAME, write_unit_keys

REFERENCE = pl.DataFrame(
    {
        "Source Table": ["Pharma", "Pharma", "Pharma"],
        "ID": [1, 2, 3],
        "Variable Name": ["A", "A", "B"],
    }
)
PHARMA = pl.DataFrame(
    {"pharmaid": [1, 2, 3], "doseunit": ["mg", "ml", "mg"]},
    schema_overrides={"pharmaid": pl.Int32},
)


def _keys(raw, tmp_path) -> list:
    out = write_unit_keys(raw, tmp_path / "staged")
    assert out.name == f"{SIDE_TABLE_NAME}.parquet"
    return pl.read_parquet(out).sort("pharmaid").rows()


def test_release_layout(tmp_path):
    """The HiRID release: the reference as CSV, the pharma records as a directory of parquet."""
    raw = tmp_path / "raw"
    (raw / "raw_stage" / "pharma_records" / "parquet").mkdir(parents=True)
    REFERENCE.write_csv(raw / "hirid_variable_reference.csv")
    PHARMA.write_parquet(
        raw / "raw_stage" / "pharma_records" / "parquet" / "part-0.parquet"
    )
    assert _keys(raw, tmp_path) == [(1, True), (2, True)]


def test_single_file_layout(tmp_path):
    """One parquet file per table, as MESSY-synth writes them (the synthetic-etl CI job)."""
    raw = tmp_path / "raw"
    (raw / "raw_stage" / "pharma_records").mkdir(parents=True)
    REFERENCE.write_parquet(raw / "hirid_variable_reference.parquet")
    PHARMA.write_parquet(raw / "raw_stage" / "pharma_records" / "parquet.parquet")
    assert _keys(raw, tmp_path) == [(1, True), (2, True)]


def test_missing_reference_fails(tmp_path):
    """The reference is required: without it the stage fails rather than staging nothing."""
    raw = tmp_path / "raw"
    (raw / "raw_stage" / "pharma_records").mkdir(parents=True)
    PHARMA.write_parquet(raw / "raw_stage" / "pharma_records" / "parquet.parquet")
    with pytest.raises(FileNotFoundError, match="hirid_variable_reference"):
        write_unit_keys(raw, tmp_path / "staged")
