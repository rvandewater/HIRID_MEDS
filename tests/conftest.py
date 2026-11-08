"""A tiny hand-written HiRID-shaped fixture, and the plain ETL run over it (without suMEDS).

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

T0 = datetime(2150, 1, 1, 8, 0)

# The console scripts of this environment (meds-extract-run, suMEDS-enrich, ...). They shell out
# to further scripts (MEDS_transform-stage, meds-extract-download), which must be on PATH too.
BIN_DIR = Path(sys.executable).parent


def run_command(args: list[str]) -> subprocess.CompletedProcess:
    """Run one of this environment's console scripts, failing the test with its stderr."""
    env = {**os.environ, "PATH": f"{BIN_DIR}{os.pathsep}{os.environ['PATH']}"}
    cmd = [str(BIN_DIR / args[0]), *args[1:]]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr[-5000:]
    return result


def write_raw_fixture(root: Path) -> None:
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
            "Source Table": [
                "Observation",
                "Observation",
                "Observation",
                "Pharma",
                "Pharma",
            ],
            "ID": [100, 200, 20000300, 100, 300],
            "Variable Name": ["Heart rate", "GCS", "Lab A", "Drug A", "Drug B"],
            "Unit": ["/min", None, "mmol/l", None, None],
            "Additional information": [None, None, "from a drain", None, None],
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
                T0.replace(hour=11),
            ],
            "patientid": [1, 1, 1, 1, 2, 2, 2],
            "stringvalue": ["", "", "", "abnormal", "", "", ""],
            "value": [80.0, 85.0, 15.0, None, 90.0, 95.0, 7.4],
            # 999 has no reference entry.
            "variableid": [100, 100, 200, 999, 100, 100, 20000300],
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


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    raw = tmp_path_factory.mktemp("hirid_raw")
    write_raw_fixture(raw)
    return raw


@pytest.fixture(scope="session")
def meds_dir(tmp_path_factory, raw_dir: Path) -> Path:
    """The plain ETL over the fixture: `meds-extract-run spec=HIRID skip_stages=[sumeds_enrich]`."""
    out = tmp_path_factory.mktemp("hirid") / "out"
    run_command(
        [
            "meds-extract-run",
            "spec=HIRID",
            f"output_dir={out}",
            f"input_dir={raw_dir}",
            "do_download=false",
            "skip_stages=[sumeds_enrich]",
        ]
    )
    return out


@pytest.fixture(scope="session")
def meds(meds_dir: Path) -> pl.DataFrame:
    return pl.read_parquet(meds_dir / "data" / "**" / "*.parquet")
