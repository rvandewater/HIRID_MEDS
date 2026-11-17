"""Which medication codes need their dose unit in the code, so that each code has one unit.

A medication code is ``MEDICATION//{Variable Name}``, and the unit of its values is the ``doseunit``
of its rows. Every pharmaid has one dose unit, but pharmaids that share a name may not. Only the
codes of such names get the unit as their last component (``MEDICATION//{Variable Name}//{doseunit}``);
every other code stays as it was.

Which names those are depends on the data, so the pre-stage ``hirid_unit_keys`` writes them as a
side table, ``pharma_unit_in_code`` (the pharmaids of those names, each with ``unit_in_code`` true),
that the pharma block of ``configs/messy.yaml`` joins on the pharmaid.
"""

import logging
import os
from pathlib import Path

import polars as pl
from MEDS_extract.io import resolve_source_files, scan_source
from MEDS_transforms.stages import Stage

logger = logging.getLogger(__name__)

STAGE_NAME = "hirid_unit_keys"
SIDE_TABLE_NAME = "pharma_unit_in_code"
# Table prefixes as the spec names them; MEDS-Extract resolves each to `{prefix}/*.{ext}` or
# `{prefix}.{ext}` (parquet, par, csv.gz or csv), and so does this stage.
REFERENCE = "hirid_variable_reference"
PHARMA = "raw_stage/pharma_records/parquet"


def pharmaids_with_unit_in_code(
    reference: pl.LazyFrame, pharma: pl.LazyFrame
) -> pl.DataFrame:
    """The pharmaids whose code name is shared with rows of another dose unit.

    The name is the ``Variable Name`` of the pharmaid's ``Pharma`` reference row, or the pharmaid
    itself without one, as in the code. Dose units are compared stripped, blanks and nulls left out.

    Examples:
        >>> reference = pl.LazyFrame({
        ...     "Source Table": ["Pharma", "Pharma", "Pharma", "Observation"],
        ...     "ID": [1, 2, 3, 4],
        ...     "Variable Name": ["A", "A", "B", "A"],
        ... })
        >>> pharma = pl.LazyFrame({
        ...     "pharmaid": [1, 2, 2, 3, 5, 5],
        ...     "doseunit": ["mg", "ml", " ml", "mg", "mg", None],
        ... }, schema_overrides={"pharmaid": pl.Int32})
        >>> pharmaids_with_unit_in_code(reference, pharma).sort("pharmaid").rows()
        [(1, True), (2, True)]
    """
    names = reference.filter(pl.col("Source Table") == "Pharma").select(
        pl.col("ID").cast(pl.Int64).alias("pharmaid"),
        pl.col("Variable Name").alias("name"),
    )
    units = (
        pharma.select(
            pl.col("pharmaid").cast(pl.Int64),
            pl.col("doseunit").str.strip_chars().alias("unit"),
        )
        .filter(pl.col("unit").is_not_null() & (pl.col("unit") != ""))
        .unique()
        .join(names, on="pharmaid", how="left")
        .with_columns(
            pl.coalesce("name", pl.col("pharmaid").cast(pl.String)).alias("name")
        )
    )
    shared = (
        units.group_by("name")
        .agg(pl.col("unit").n_unique().alias("n"))
        .filter(pl.col("n") > 1)
    )
    return (
        units.join(shared, on="name", how="semi")
        .select("pharmaid", pl.lit(True).alias("unit_in_code"))
        .unique()
        .collect()
    )


def write_unit_keys(raw_input_dir: Path, output_dir: Path) -> Path:
    """Write the ``pharma_unit_in_code`` side table of the release in ``raw_input_dir``.

    Both tables are found as MEDS-Extract finds the spec's tables (``MEDS_extract.io``): the
    release's ``hirid_variable_reference.csv`` and ``raw_stage/pharma_records/parquet/*.parquet``,
    or the same prefixes in another supported layout or format (e.g. the single parquet files
    MESSY-synth writes). Both are required: a missing table raises ``FileNotFoundError``.

    Returns:
        The written file, replaced atomically.
    """
    raw_input_dir = Path(raw_input_dir)
    table = pharmaids_with_unit_in_code(
        scan_source(
            resolve_source_files(raw_input_dir, REFERENCE), infer_schema_length=None
        ),
        scan_source(resolve_source_files(raw_input_dir, PHARMA)),
    )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / f"{SIDE_TABLE_NAME}.parquet"
    tmp = out.with_name(f".{out.name}.tmp")
    table.write_parquet(tmp)
    os.replace(tmp, out)
    logger.info(
        f"{table.height} pharmaids share a name with another dose unit; their codes end in it."
    )
    return out


@Stage.register(stage_name=STAGE_NAME, is_metadata=False, default_config={})
def main(cfg) -> None:
    """Write the ``pharma_unit_in_code`` side table of the release in ``input_dir``."""
    if cfg.worker != 0:
        return
    write_unit_keys(Path(cfg.input_dir), Path(cfg.stage_cfg.output_dir))
