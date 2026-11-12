"""End-to-end run of the bundled MESSY config over the tiny HiRID-shaped fixture of ``conftest.py``.

The fixture mirrors the real HiRID 1.1.1 layout and dtypes, because that is where this config has
broken before: join keys of mismatched dtypes, and an `ID` that is listed under both `Source
Table`s. Every value in it is made up.
"""

from datetime import datetime
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
from conftest import T0
from meds import CodeMetadataSchema
from MEDS_extract.config import MessyConfig

# The descriptions of the literal codes, which the `_self` blocks of `configs/messy.yaml` write. They
# are exactly the ones the removed `hirid-meds-describe-codes` step filled in after the run, from
# `metadata/static_code_descriptions.csv`.
LITERAL_DESCRIPTIONS = {
    "HOSPITAL_ADMISSION": (
        "ICU admission, at general_table.admissiontime (date-shifted). HiRID covers stays in the "
        "intensive care unit only; the code keeps the name used by earlier releases of this ETL."
    ),
    "HOSPITAL_DISCHARGE": (
        "ICU discharge, placed at the stay's last observation (latest "
        "observation_tables.datetime) because HiRID records no discharge time; the code keeps the "
        "name used by earlier releases of this ETL."
    ),
    "MEDS_BIRTH": (
        "Pseudo date of birth: general_table.admissiontime minus general_table.age in years. "
        "HiRID reports age in bins (ages above 89 are set to 90), so the date can be off by "
        "several years."
    ),
    "MEDS_DEATH": (
        "Death in the ICU: general_table.discharge_status, the ICU (not hospital) discharge "
        "status, is not 'alive'; a missing status is not counted. Placed at the stay's last "
        "observation because HiRID records no time of death."
    ),
}


def test_registered_spec_loads():
    assert set(MessyConfig.load("HIRID").table_prefixes) == {
        "general_table",
        "raw_stage/pharma_records/parquet",
        "raw_stage/observation_tables/parquet",
    }


def test_shared_id_does_not_fan_out(meds: pl.DataFrame):
    codes = meds["code"].value_counts().to_dict(as_series=False)
    counts = dict(zip(codes["code"], codes["count"], strict=True))
    assert counts["OBSERVATION//Heart rate///min"] == 4
    assert counts["MEDICATION//Drug A"] == 1
    assert not any("Drug" in c for c in counts if c.startswith("OBSERVATION"))
    assert not any("Heart rate" in c for c in counts if c.startswith("MEDICATION"))


def test_unreferenced_id_and_null_unit(meds: pl.DataFrame):
    codes = set(meds["code"])
    assert "OBSERVATION//999//UNK" in codes
    assert "OBSERVATION//GCS//UNK" in codes


def test_text_values(meds: pl.DataFrame):
    obs = meds.filter(pl.col("code").str.starts_with("OBSERVATION"))
    assert obs["text_value"].drop_nulls().to_list() == ["abnormal"]
    med = meds.filter(pl.col("code").str.starts_with("MEDICATION")).sort("subject_id")
    assert med["text_value"].to_list() == ["mg", "ml"]
    assert med["numeric_value"].to_list() == [5.0, 1.5]


def test_patient_events(meds: pl.DataFrame):
    def times(code: str) -> dict[int, datetime]:
        df = meds.filter(pl.col("code") == code)
        return dict(zip(df["subject_id"], df["time"], strict=True))

    assert times("HOSPITAL_ADMISSION") == {1: T0, 2: T0, 3: T0}
    # Discharge is the last observation; patient 3 has none, so no discharge.
    assert times("HOSPITAL_DISCHARGE") == {
        1: T0.replace(hour=10),
        2: T0.replace(hour=12),
    }
    # Only an explicit non-"alive" status is a death; a null status is not.
    assert times("MEDS_DEATH") == {2: T0.replace(hour=12)}
    birth = times("MEDS_BIRTH")
    assert birth.keys() == {1, 2, 3}
    assert abs((T0 - birth[1]).days / 365.25 - 60) < 0.01
    assert set(meds.filter(pl.col("code").str.starts_with("SEX//"))["code"]) == {
        "SEX//M",
        "SEX//F",
    }


def test_code_metadata(meds: pl.DataFrame, meds_dir: Path):
    table = pq.read_table(meds_dir / "metadata" / "codes.parquet")
    CodeMetadataSchema.validate(table)
    # The ETL adds no vocabulary parents; suMEDS adds LOINC parents from `sumeds_rules.yaml`
    # (tests/test_run_with_enrichment.py).
    assert "parent_codes" not in table.column_names
    by_code = {r["code"]: r for r in pl.from_arrow(table).iter_rows(named=True)}
    assert set(meds["code"]) <= set(by_code)
    # Every code is described, the literal ones by their `_self` blocks (next test).
    assert all(row["description"] is not None for row in by_code.values())

    # Id 100 is in both tables; each code is described from its own table's row only.
    hr = by_code["OBSERVATION//Heart rate///min"]
    assert hr["description"].startswith("Heart rate [/min] (HiRID observation_tables")
    assert "Drug" not in hr["description"]
    drug = by_code["MEDICATION//Drug A"]
    assert drug["description"].startswith("Drug A (HiRID pharma_records")
    assert "Heart rate" not in drug["description"]
    assert by_code["OBSERVATION//GCS//UNK"]["description"].startswith("GCS [no unit]")

    # The reference's additional information is kept.
    lab = by_code["OBSERVATION//Lab A//mmol/l"]
    assert lab["description"].startswith("Lab A [mmol/l]; from a drain (HiRID")

    # An id without a reference entry is described by the fallback `_self` block.
    assert by_code["OBSERVATION//999//UNK"]["description"].startswith(
        "HiRID observation variableid 999 without an Observation entry"
    )
    assert by_code["SEX//F"]["description"].startswith(
        "Sex: F (HiRID general_table.sex"
    )


def test_literal_codes_are_described(meds_dir: Path):
    """The `_self` blocks describe the literal codes exactly as the removed static table did."""
    codes = pl.read_parquet(meds_dir / "metadata" / "codes.parquet")
    literal = codes.filter(pl.col("code").is_in(list(LITERAL_DESCRIPTIONS)))
    assert (
        dict(zip(literal["code"], literal["description"], strict=True))
        == LITERAL_DESCRIPTIONS
    )
    # A literal code with metadata also gets its template, the literal itself.
    assert literal["code_template"].to_list() == literal["code"].to_list()
