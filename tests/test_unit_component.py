"""A code's unit component is the unit stripped, and `UNK` when it is missing or blank.

MEDS-Extract compares units stripped and drops blank ones when it writes `codes.parquet`, so a
code that kept the whitespace would split one unit over several codes (` mg`, `mg`, `mg `), and a
blank one would give a code ending in whitespace with no unit. The expressions are evaluated
straight from the shipped config with dftly, as MEDS-Extract evaluates them.
"""

from pathlib import Path

import polars as pl
import yaml
from dftly import Parser

CONFIG_FP = Path(__file__).parents[1] / "src" / "HIRID_MEDS" / "configs" / "messy.yaml"


def test_dose_unit_is_stripped_and_blank_is_unk():
    block = yaml.safe_load(CONFIG_FP.read_text())["raw_stage/pharma_records/parquet"]
    raw = pl.DataFrame(
        {
            "doseunit": ["mg", " mg", "mg ", "\tmg\n", "mL / h", "  ", "", None],
            "variable_name": ["Drug A"] * 8,
            "pharmaid": [100] * 8,
        }
    )
    unit = {"_dose_unit": block["_table"]["cols"]["_dose_unit"]}
    with_cols = raw.with_columns(**Parser.to_polars(unit))
    codes = with_cols.select(**Parser.to_polars({"code": block["medication"]["code"]}))[
        "code"
    ].to_list()
    assert codes == [
        "MEDICATION//Drug A//mg",
        "MEDICATION//Drug A//mg",
        "MEDICATION//Drug A//mg",
        "MEDICATION//Drug A//mg",
        "MEDICATION//Drug A//mL / h",
        "MEDICATION//Drug A//UNK",
        "MEDICATION//Drug A//UNK",
        "MEDICATION//Drug A//UNK",
    ]


def test_observation_unit_is_stripped_and_blank_is_unk():
    block = yaml.safe_load(CONFIG_FP.read_text())[
        "raw_stage/observation_tables/parquet"
    ]
    # `Unit` comes from the variable reference, joined onto the observation rows.
    raw = pl.DataFrame(
        {
            "Unit": ["/min", " /min", "/min ", "\t/min\n", "mL / h", "  ", "", None],
            "variable_name": ["Heart rate"] * 8,
            "variableid": [100] * 8,
        }
    )
    unit = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    with_cols = raw.with_columns(**Parser.to_polars(unit))
    codes = with_cols.select(
        **Parser.to_polars({"code": block["observation"]["code"]})
    )["code"].to_list()
    assert (
        codes
        == ["OBSERVATION//Heart rate///min"] * 4
        + ["OBSERVATION//Heart rate//mL / h"]
        + ["OBSERVATION//Heart rate//UNK"] * 3
    )
