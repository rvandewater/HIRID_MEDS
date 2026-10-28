"""End-to-end run of the bundled MESSY config over a tiny hand-written HiRID-shaped fixture.

The fixture mirrors the real HiRID 1.1.1 layout and dtypes (raw_stage parquet with Int32 ids and
native timestamps; reference_data CSVs at the input root), because that is where this config has
broken before: join keys of mismatched dtypes, and an `ID` that is listed under both `Source
Table`s. Every value in it is made up.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest
from MEDS_extract.config import MessyConfig

T0 = datetime(2150, 1, 1, 8, 0)


def _write_fixture(root: Path) -> None:
    pl.DataFrame(
        {
            "patientid": [1, 2, 3],
            "admissiontime": [
                "2150-01-01 08:00:00",
                "2150-01-01 08:00:00",
                "2150-01-01 08:00:00",
            ],
            "sex": ["M", "F", "F"],
            "age": [60, 70, 80],
            "discharge_status": ["alive", "dead", None],
        }
    ).write_csv(root / "general_table.csv")
    # ID 100 is both an observation variable and a drug, as one id is in the real reference.
    pl.DataFrame(
        {
            "Source Table": ["Observation", "Observation", "Pharma", "Pharma"],
            "ID": [100, 200, 100, 300],
            "Variable Name": ["Heart rate", "GCS", "Drug A", "Drug B"],
            "Unit": ["/min", None, None, None],
            "Additional information": [None, None, None, None],
        }
    ).write_csv(root / "hirid_variable_reference.csv")

    obs_dir = root / "raw_stage" / "observation_tables" / "parquet"
    obs_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "datetime": [
                T0,
                T0.replace(hour=9),
                T0.replace(hour=9),
                T0.replace(hour=10),
                T0,
                T0.replace(hour=12),
            ],
            "patientid": [1, 1, 1, 1, 2, 2],
            "stringvalue": ["", "", "", "abnormal", "", ""],
            "value": [80.0, 85.0, 15.0, None, 90.0, 95.0],
            # 999 has no reference entry.
            "variableid": [100, 100, 200, 999, 100, 100],
        },
        schema_overrides={
            "patientid": pl.Int32,
            "variableid": pl.Int32,
            "value": pl.Float32,
        },
    ).with_columns(pl.col("datetime").cast(pl.Datetime("ns"))).write_parquet(
        obs_dir / "part-0.parquet"
    )

    pharma_dir = root / "raw_stage" / "pharma_records" / "parquet"
    pharma_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "patientid": [1, 2],
            "pharmaid": [100, 300],
            "givenat": [T0.replace(hour=9), T0.replace(hour=11)],
            "givendose": [5.0, 1.5],
            "doseunit": ["mg", "ml"],
        },
        schema_overrides={
            "patientid": pl.Int32,
            "pharmaid": pl.Int32,
            "givendose": pl.Float32,
        },
    ).with_columns(pl.col("givenat").cast(pl.Datetime("ns"))).write_parquet(
        pharma_dir / "part-0.parquet"
    )


@pytest.fixture(scope="module")
def meds(tmp_path_factory) -> pl.DataFrame:
    tmp = tmp_path_factory.mktemp("hirid")
    raw, out = tmp / "raw", tmp / "out"
    raw.mkdir()
    _write_fixture(raw)

    # meds-extract-run shells out to MEDS_transform-stage, which must be on PATH.
    bin_dir = Path(sys.executable).parent
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    cmd = [
        str(bin_dir / "meds-extract-run"),
        "spec=HIRID", f"output_dir={out}", f"input_dir={raw}", "do_download=false",
    ]  # fmt: skip
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr[-5000:]
    return pl.read_parquet(out / "data" / "**" / "*.parquet")


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
