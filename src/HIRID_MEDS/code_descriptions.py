"""Descriptions of the literal codes, filled into a finished ``metadata/codes.parquet``.

The extraction itself is ``configs/messy.yaml``, and it describes every component-coded code (see
the README section "Code metadata"). Literal codes (``HOSPITAL_ADMISSION``, ``MEDS_BIRTH``, ...)
are the exception: MEDS-Extract joins ``_metadata`` on code components, which a literal code does
not have, and the pre-existing ``metadata/codes.parquet`` route it documents for that case is not
read by the standard extraction pipeline (``extract_code_metadata`` reads the output of its prior
metadata stage, ``split_and_shard_subjects``, which writes no ``codes.parquet``). So this module
fills them in after the run, from ``metadata/static_code_descriptions.csv``:

    suMEDS-extract spec=HIRID output_dir=$MEDS_OUTPUT_DIR ...
    hirid-meds-describe-codes $MEDS_OUTPUT_DIR

suMEDS and this step only fill null descriptions, so on a code both could describe, the step that
runs first would win. No code is in both: the static table holds exactly the literal codes, and
suMEDS can describe a code only through the ``OBSERVATION//`` crosswalk rules of
``sumeds_rules.yaml`` or its built-in layouts, which need a ``/`` in the code or an existing
``parent_codes`` entry. A literal code has neither, so the order of the two steps does not change
the result, and this step works the same after a plain ``meds-extract-run``.
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
    already in the file are touched: the vocabulary is never extended. Only the ``description``
    column is rewritten. The MEDS columns are cast to the types the pipeline itself writes
    (``string``, ``list<string>``), which the MEDS code-metadata schema requires, and every other
    column keeps its Arrow type, so the ``map<string, string>`` names of a suMEDS-enriched file
    stay maps. The file is validated against that schema before it replaces the original.

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

        The names map of a suMEDS-enriched file stays a map:

        >>> enriched = pa.table({
        ...     "code": ["MEDS_BIRTH", "OBSERVATION//Heart rate///min"],
        ...     "description": [None, "Heart rate [/min]"],
        ...     "parent_codes": [None, ["LOINC//8867-4"]],
        ...     "parent_names": pa.array(
        ...         [None, [("LOINC//8867-4", "Heart rate")]], pa.map_(pa.string(), pa.string())
        ...     ),
        ... })
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     pq.write_table(enriched, fp)
        ...     n = apply_static_descriptions(fp, static)
        ...     out = pq.read_table(fp)
        >>> n, out.schema.field("parent_names").type == enriched.schema.field("parent_names").type
        (1, True)
        >>> out.column("parent_names").to_pylist()
        [None, [('LOINC//8867-4', 'Heart rate')]]
    """
    codes_fp = Path(codes_fp)
    if static is None:
        static = read_static_code_descriptions()
    table = pq.read_table(codes_fp)
    codes = pl.from_arrow(table.column("code")).cast(pl.String)
    if "description" in table.column_names:
        description = pl.from_arrow(table.column("description")).cast(pl.String)
    else:
        description = pl.Series("description", [None] * table.num_rows, pl.String)
    lookup = dict(zip(static["code"], static["description"], strict=True))
    filled = description.fill_null(
        codes.replace_strict(lookup, default=None, return_dtype=pl.String)
    )
    n_filled = int(filled.is_not_null().sum() - description.is_not_null().sum())

    filled = filled.to_arrow().cast(pa.string())
    if "description" in table.column_names:
        index = table.schema.get_field_index("description")
        table = table.set_column(index, "description", filled)
    else:
        table = table.append_column("description", filled)
    meds_types = {
        "code": pa.string(),
        "description": pa.string(),
        "parent_codes": pa.list_(pa.string()),
    }
    fields = [
        field.with_type(meds_types[field.name]) if field.name in meds_types else field
        for field in table.schema
    ]
    out = table.cast(pa.schema(fields, metadata=table.schema.metadata))
    CodeMetadataSchema.validate(out)

    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return n_filled


def main(argv: list[str] | None = None) -> None:
    """``hirid-meds-describe-codes``: fill literal-code descriptions after the ETL run."""
    parser = argparse.ArgumentParser(
        prog="hirid-meds-describe-codes", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="The output_dir of suMEDS-extract or meds-extract-run (or its "
        "metadata/codes.parquet).",
    )
    args = parser.parse_args(argv)
    codes_fp = args.output_dir
    if codes_fp.is_dir():
        codes_fp = codes_fp / "metadata" / "codes.parquet"
    if not codes_fp.is_file():
        parser.error(f"{codes_fp} not found; run the ETL first.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n_filled = apply_static_descriptions(codes_fp)
    logger.info(f"Filled the descriptions of {n_filled} literal codes in {codes_fp}.")


if __name__ == "__main__":  # pragma: no cover
    main()
