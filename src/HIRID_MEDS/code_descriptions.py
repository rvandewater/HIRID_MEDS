"""Descriptions of the literal codes, filled into a finished ``metadata/codes.parquet``.

The extraction itself is ``configs/messy.yaml``, and it describes every component-coded code (see
the README section "Code metadata"). Literal codes (``HOSPITAL_ADMISSION``, ``MEDS_BIRTH``, ...)
are the exception: MEDS-Extract joins ``_metadata`` on code components, which a literal code does
not have, and the pre-existing ``metadata/codes.parquet`` route it documents for that case is not
read by the standard extraction pipeline (``extract_code_metadata`` reads the output of its prior
metadata stage, ``split_and_shard_subjects``, which writes no ``codes.parquet``). So this module
fills them in after the run, from ``metadata/static_code_descriptions.csv``:

    meds-extract-run spec=HIRID output_dir=$MEDS_OUTPUT_DIR ...
    hirid-meds-describe-codes $MEDS_OUTPUT_DIR
"""

import argparse
import logging
import os
from importlib.resources import files
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

logger = logging.getLogger(__name__)

STATIC_DESCRIPTIONS_CSV = files("HIRID_MEDS").joinpath(
    "metadata/static_code_descriptions.csv"
)


def read_static_code_descriptions(path=STATIC_DESCRIPTIONS_CSV) -> pl.DataFrame:
    """Read the packaged descriptions of literal codes in the MEDS ``codes.parquet`` shape.

    Only ``code`` and ``description`` are returned; the ``source`` column documents the table
    and is not written into the dataset.

    Examples:
        >>> df = read_static_code_descriptions()
        >>> df.columns
        ['code', 'description']
        >>> sorted(df["code"])
        ['HOSPITAL_ADMISSION', 'HOSPITAL_DISCHARGE', 'MEDS_BIRTH', 'MEDS_DEATH']
    """
    return pl.read_csv(path.read_bytes(), infer_schema=False).select(
        "code", "description"
    )


def apply_static_descriptions(
    codes_fp: Path, static: pl.DataFrame | None = None
) -> int:
    """Fill the ``description`` of literal codes in a finished ``codes.parquet``, in place.

    Only null descriptions are filled (an extracted description always wins), and only codes
    already in the file are touched: the vocabulary is never extended. The file is validated
    against the MEDS code-metadata schema before it replaces the original: polars materializes
    strings as Arrow ``large_string``, which that schema rejects, so the MEDS columns are cast
    back to the types the pipeline itself writes (``string``, ``list<string>``).

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.
        static: Override of the packaged table (``code``, ``description``), for tests.

    Returns:
        The number of codes whose description was filled.

    Examples:
        >>> import tempfile
        >>> static = pl.DataFrame({"code": ["MEDS_BIRTH", "NOT_EMITTED"], "description": ["Birth", "x"]})
        >>> codes = pl.DataFrame(
        ...     {"code": ["MEDS_BIRTH", "SEX//F", "SEX//M"],
        ...      "description": [None, "Sex: F", None],
        ...      "parent_codes": [None, None, None]},
        ...     schema_overrides={"parent_codes": pl.List(pl.String)},
        ... )
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     codes.write_parquet(fp)
        ...     n = apply_static_descriptions(fp, static)
        ...     out = pl.read_parquet(fp)
        >>> n
        1
        >>> out.select("code", "description").rows()
        [('MEDS_BIRTH', 'Birth'), ('SEX//F', 'Sex: F'), ('SEX//M', None)]
    """
    codes_fp = Path(codes_fp)
    if static is None:
        static = read_static_code_descriptions()
    codes = pl.from_arrow(pq.read_table(codes_fp))
    if "description" not in codes.columns:
        codes = codes.with_columns(pl.lit(None, dtype=pl.String).alias("description"))
    filled = (
        codes.join(static.rename({"description": "_static"}), on="code", how="left")
        .with_columns(pl.coalesce("description", "_static").alias("description"))
        .drop("_static")
        .select(codes.columns)
    )
    n_filled = int(
        filled["description"].is_not_null().sum()
        - codes["description"].is_not_null().sum()
    )

    out = filled.to_arrow()
    meds_types = {
        "code": pa.string(),
        "description": pa.string(),
        "parent_codes": pa.list_(pa.string()),
    }
    out = out.cast(
        pa.schema(
            [pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]
        )
    )
    CodeMetadataSchema.validate(out)

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return n_filled


def main(argv: list[str] | None = None) -> None:
    """``hirid-meds-describe-codes``: fill literal-code descriptions after ``meds-extract-run``."""
    parser = argparse.ArgumentParser(
        prog="hirid-meds-describe-codes", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="The meds-extract-run output_dir (or its metadata/codes.parquet).",
    )
    args = parser.parse_args(argv)
    codes_fp = args.output_dir
    if codes_fp.is_dir():
        codes_fp = codes_fp / "metadata" / "codes.parquet"
    if not codes_fp.is_file():
        parser.error(f"{codes_fp} not found; run meds-extract-run first.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n_filled = apply_static_descriptions(codes_fp)
    logger.info(f"Filled the descriptions of {n_filled} literal codes in {codes_fp}.")


if __name__ == "__main__":  # pragma: no cover
    main()
