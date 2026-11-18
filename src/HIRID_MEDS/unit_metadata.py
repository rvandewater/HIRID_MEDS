"""A parent for codes in ``metadata/codes.parquet`` that differ only in unit.

A unit is a property of the code (the unit of its numeric values). MEDS-Extract (0.9 and later)
writes ``unit`` as one string per code, and fails if any code has more than one unit: the unit then
belongs in the code for that family. This step no longer collapses ``unit``. MEDS does not
standardise this column.

Codes that are the same item in different units (``X//item//mg``, ``X//item//mcg``: the unit is
the code's last component) get the code without the unit (``X//item``) as an in-dataset
parent in ``parent_codes``, so tools can group them, and that parent code gets a row of its own
(no unit; the description and vocabulary parents its siblings share).

The step is registered as the MEDS-Transforms stage ``hirid_unit_metadata`` and declared in
``etl.post_stages`` of ``configs/messy.yaml``, before ``sumeds_enrich``, so
``meds-extract-run spec=HIRID`` runs it.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import NamedTuple

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema, code_metadata_filepath
from MEDS_transforms.stages import Stage

logger = logging.getLogger(__name__)

STAGE_NAME = "hirid_unit_metadata"


class UnitSummary(NamedTuple):
    """What :func:`unit_metadata_file` found and did.

    Codes with a unit, unit-sibling groups, codes in them, and stem rows added by this run.
    """

    with_unit: int
    sibling_groups: int
    sibling_codes: int
    stem_rows_added: int


def unit_sibling_stems(code: pl.Series, unit: pl.Series) -> pl.Series:
    """The code without its unit, for codes that have a unit sibling; null for every other code.

    A code is unit-suffixed when its (string) ``unit`` is set and the code ends with ``//`` and that
    unit; its stem is the code without that suffix. A stem shared by two or more unit-suffixed codes
    is a sibling group.

    Examples:
        >>> code = pl.Series(["X//a//mg", "X//a//mcg", "X//b//mg", "Y//c", "Y//d//1", "Y//d//2"])
        >>> unit = pl.Series(["mg", "mcg", "mg", "mg", None, None])
        >>> unit_sibling_stems(code, unit).to_list()
        ['X//a', 'X//a', None, None, None, None]
    """
    frame = pl.DataFrame(
        {"code": code, "unit": unit}, schema={"code": pl.String, "unit": pl.String}
    )
    suffixed = pl.col("unit").is_not_null() & pl.col("code").str.ends_with(
        pl.concat_str(pl.lit("//"), pl.col("unit"))
    )
    stem = pl.when(suffixed).then(
        pl.col("code").str.slice(
            0, pl.col("code").str.len_chars() - pl.col("unit").str.len_chars() - 2
        )
    )
    stems = frame.select(stem.alias("stem"))["stem"]
    stems = stems.set(stems == "", None)
    counts = stems.to_frame().select(pl.len().over("stem"))["len"]
    return stems.set(counts < 2, None)


def unit_sibling_parents(codes: pl.DataFrame) -> pl.DataFrame:
    """Append each unit sibling's stem (:func:`unit_sibling_stems`) to its ``parent_codes``.

    Existing parents are kept and the stem is not added twice; ``parent_codes`` is created when a
    code needs it. A unit-suffixed code without a sibling, and every other code, is left as it is.
    The stem's own row is :func:`unit_stem_rows`.

    Examples:
        >>> codes = pl.DataFrame({
        ...     "code": ["X//a//mg", "X//a//mcg", "X//b//mg"],
        ...     "unit": ["mg", "mcg", "mg"],
        ...     "parent_codes": [["ATC//N02", "X//a"], ["ATC//N02"], None],
        ... })
        >>> unit_sibling_parents(codes)["parent_codes"].to_list()
        [['ATC//N02', 'X//a'], ['ATC//N02', 'X//a'], None]
        >>> unit_sibling_parents(codes.drop("parent_codes").head(1)).columns
        ['code', 'unit']
    """
    stems = unit_sibling_stems(codes["code"], codes["unit"])
    if stems.is_null().all():
        return codes
    if "parent_codes" not in codes.columns:
        codes = codes.with_columns(
            pl.lit(None, dtype=pl.List(pl.String)).alias("parent_codes")
        )
    parents = pl.col("parent_codes")
    stem = pl.col("_stem")
    add = stem.is_not_null() & ~parents.list.contains(stem).fill_null(False)
    empty = pl.lit([], dtype=pl.List(pl.String))
    return (
        codes.with_columns(stems.alias("_stem"))
        .with_columns(
            pl.when(add)
            .then(pl.concat_list(parents.fill_null(empty), stem))
            .otherwise(parents)
            .alias("parent_codes")
        )
        .drop("_stem")
    )


def without_unit(description: str | None, unit: str | None) -> str | None:
    """``description`` with the last mention of ``unit`` removed, or None if it has none.

    The removed span is the unit with its brackets, if it is bracketed (``[mg]``, ``(mg)``), and
    the whitespace before it; the result is trimmed. Nothing else is edited.

    Examples:
        >>> without_unit("Heart rate [/min] (charted)", "/min")
        'Heart rate (charted)'
        >>> without_unit("Dose in mg", "mg")
        'Dose in'
        >>> without_unit("Dose", "mg") is None
        True
    """
    if not description or not unit:
        return None
    escaped = re.escape(unit)
    matches = list(
        re.finditer(
            rf"\s*(?:\[\s*{escaped}\s*\]|\(\s*{escaped}\s*\)|(?<!\w){escaped}(?!\w))",
            description,
        )
    )
    if not matches:
        return None
    span = matches[-1]
    return (description[: span.start()] + description[span.end() :]).strip()


def _shared_description(siblings: list[dict]) -> str | None:
    """The description all siblings share, as is or without each sibling's own unit; else None."""
    descriptions = {sibling["description"] for sibling in siblings}
    if len(descriptions) == 1:
        return descriptions.pop()
    stripped = {
        without_unit(sibling["description"], sibling["unit"]) for sibling in siblings
    }
    if len(stripped) == 1 and (description := stripped.pop()):
        return description
    return None


def unit_stem_rows(codes: pl.DataFrame) -> list[dict]:
    """Rows for the stems of unit-sibling groups (:func:`unit_sibling_stems`) that have none yet.

    A stem row has ``code`` the stem and ``unit`` null (its siblings differ in unit); its
    ``description`` is the siblings' description if they all share one non-null description, or,
    if their descriptions differ only by each sibling's own unit, the description without it
    (:func:`without_unit`), else null; its ``parent_codes`` are the parents every sibling has (in the first sibling's order,
    never the stem itself), null if none. A stem that already is a code gets no row.

    Examples:
        >>> codes = pl.DataFrame({
        ...     "code": ["X//a//mg", "X//a//g", "Y//b//mg", "Y//b//g", "Z//c//mg", "Z//c//g", "Z//c"],
        ...     "unit": ["mg", "g", "mg", "g", "mg", "g", None],
        ...     "description": ["Drug a", "Drug a", "b (mg)", "b (g)", None, None, "kept"],
        ...     "parent_codes": [["ATC//N02", "ATC//N", "X//a"], ["ATC//N", "X//a"], None, ["ATC//N"],
        ...                      None, None, None],
        ... })
        >>> for row in unit_stem_rows(codes):
        ...     print(row)
        {'code': 'X//a', 'unit': None, 'description': 'Drug a', 'parent_codes': ['ATC//N']}
        {'code': 'Y//b', 'unit': None, 'description': 'b', 'parent_codes': None}
    """
    stems = unit_sibling_stems(codes["code"], codes["unit"])
    present = set(codes["code"].to_list())
    columns = {"description", "parent_codes"} & set(codes.columns)
    groups: dict[str, list[dict]] = {}
    for stem, row in zip(stems.to_list(), codes.iter_rows(named=True), strict=True):
        if stem is not None and stem not in present:
            groups.setdefault(stem, []).append(row)
    rows = []
    for stem, siblings in groups.items():
        row = {"code": stem, "unit": None}
        if "description" in columns:
            row["description"] = _shared_description(siblings)
        if "parent_codes" in columns:
            parents = [p for p in siblings[0]["parent_codes"] or [] if p != stem]
            for sibling in siblings[1:]:
                parents = [p for p in parents if p in (sibling["parent_codes"] or [])]
            row["parent_codes"] = list(dict.fromkeys(parents)) or None
        rows.append(row)
    return rows


def unit_metadata_file(codes_fp: Path) -> UnitSummary:
    """Link unit siblings in a finished ``codes.parquet``.

    ``unit`` is the string MEDS-Extract writes, one per code. :func:`unit_sibling_parents` adds the
    in-dataset parents and :func:`unit_stem_rows` the rows of the stems; every other column of a
    stem row is null. A file without ``unit`` is not touched, and running the step again changes
    nothing. Existing rows keep every column but ``unit`` and ``parent_codes`` as they are; a file
    sorted by code stays sorted.

    Args:
        codes_fp: The ``metadata/codes.parquet`` of a finished extraction.

    Returns:
        The counts of :class:`UnitSummary`.

    Raises:
        TypeError: If ``unit`` is not a string column (MEDS-Extract before 0.9 wrote a list).

    Examples:
        >>> import tempfile
        >>> codes = pl.DataFrame(
        ...     {"code": ["A//x//g", "A//x//mg", "B"], "description": ["x", "x", None],
        ...      "unit": ["g", "mg", None]},
        ... )
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = Path(d) / "codes.parquet"
        ...     codes.write_parquet(fp)
        ...     summary = unit_metadata_file(fp)
        ...     again = unit_metadata_file(fp)
        ...     out = pl.read_parquet(fp)
        >>> summary, again.stem_rows_added
        (UnitSummary(with_unit=2, sibling_groups=1, sibling_codes=2, stem_rows_added=1), 0)
        >>> out.to_dicts()  # doctest: +NORMALIZE_WHITESPACE
        [{'code': 'A//x', 'description': 'x', 'unit': None, 'parent_codes': None},
         {'code': 'A//x//g', 'description': 'x', 'unit': 'g', 'parent_codes': ['A//x']},
         {'code': 'A//x//mg', 'description': 'x', 'unit': 'mg', 'parent_codes': ['A//x']},
         {'code': 'B', 'description': None, 'unit': None, 'parent_codes': None}]
    """
    codes_fp = Path(codes_fp)
    table = pq.read_table(codes_fp)
    if "unit" not in table.column_names:
        return UnitSummary(0, 0, 0, 0)

    # Through Python objects: codes.parquet is vocabulary-sized, and some polars versions panic on
    # Arrow large types read back from parquet.
    def column(name: str, dtype: pl.DataType) -> pl.Series:
        return pl.Series(name, table[name].to_pylist(), dtype=dtype)

    unit_type = table.schema.field("unit").type
    if not (
        pa.types.is_string(unit_type)
        or pa.types.is_large_string(unit_type)
        or pa.types.is_null(unit_type)
    ):
        raise TypeError(
            f"`unit` in {codes_fp} is {unit_type}, not a string: MEDS-Extract 0.9 and later "
            "write one string per code."
        )
    frame = pl.DataFrame([column("code", pl.String), column("unit", pl.String)])
    if "description" in table.column_names:
        frame = frame.with_columns(column("description", pl.String))
    if "parent_codes" in table.column_names:
        frame = frame.with_columns(column("parent_codes", pl.List(pl.String)))
    frame = unit_sibling_parents(frame)
    stem_rows = unit_stem_rows(frame)
    stems = unit_sibling_stems(frame["code"], frame["unit"])
    summary = UnitSummary(
        with_unit=int(frame["unit"].is_not_null().sum()),
        sibling_groups=stems.drop_nulls().n_unique(),
        sibling_codes=int(stems.is_not_null().sum()),
        stem_rows_added=len(stem_rows),
    )

    # Only `unit` and `parent_codes` are replaced; every other column keeps its Arrow type. `unit`
    # is written as large_string, as suMEDS writes it back; `parent_codes` keeps its type, or is
    # the MEDS list<string> when it is new.
    out = table
    for name in ("unit", "parent_codes"):
        if name not in frame.columns:
            continue
        if name == "unit":
            dtype = pa.large_string()
        elif name in out.column_names:
            dtype = out.schema.field(name).type
        else:
            dtype = pa.list_(pa.string())
        field, array = pa.field(name, dtype), pa.array(frame[name].to_list(), dtype)
        if name in out.column_names:
            out = out.set_column(out.column_names.index(name), field, array)
        else:
            out = out.append_column(field, array)
    if out.equals(table) and not stem_rows:
        return summary
    if stem_rows:
        was_sorted = frame["code"].is_sorted()
        out = pa.concat_tables(
            [out, pa.Table.from_pylist(stem_rows, schema=out.schema)]
        )
        if was_sorted:
            out = out.sort_by("code")
    # Validated as MEDS code metadata; files written by polars (tests) hold large types, which
    # the schema rejects, so only the check sees its MEDS columns cast.
    meds_types = {
        "code": pa.string(),
        "description": pa.string(),
        "parent_codes": pa.list_(pa.string()),
    }
    CodeMetadataSchema.validate(
        out.cast(
            pa.schema(
                [pa.field(f.name, meds_types.get(f.name, f.type)) for f in out.schema]
            )
        )
    )
    tmp_fp = codes_fp.with_suffix(".parquet.tmp")
    pq.write_table(out, tmp_fp)
    os.replace(tmp_fp, codes_fp)
    return summary


def _main(cfg) -> None:
    """Run :func:`unit_metadata_file` on the cohort's ``metadata/codes.parquet`` (worker 0)."""
    if cfg.worker != 0:
        return
    name = Path(code_metadata_filepath).name
    in_fp = Path(cfg.stage_cfg.metadata_input_dir) / name
    out_fp = Path(cfg.stage_cfg.reducer_output_dir) / name
    if not in_fp.is_file():
        return
    if out_fp.resolve() != in_fp.resolve():
        out_fp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(in_fp, out_fp)
    summary = unit_metadata_file(out_fp)
    logger.info(
        f"unit: {summary.with_unit} codes have one; {summary.sibling_codes} "
        f"codes in {summary.sibling_groups} unit-sibling groups have their stem as a parent; "
        f"{summary.stem_rows_added} stem rows added."
    )


main = Stage.register(main_fn=_main, stage_name=STAGE_NAME, is_metadata=True)
