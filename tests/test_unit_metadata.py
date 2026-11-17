"""The post-step that makes `unit` one string per code and links unit siblings, on synthetic files."""

import polars as pl
import pyarrow.parquet as pq
import pytest
from meds import CodeMetadataSchema

from HIRID_MEDS.unit_metadata import UnitSummary, collapse_unit_metadata


def _write(fp, codes: list[str], units: list, **columns) -> None:
    pl.DataFrame(
        {
            "code": codes,
            "description": pl.Series([None] * len(codes), dtype=pl.String),
            "unit": pl.Series(units, dtype=pl.List(pl.String)),
            **columns,
        }
    ).write_parquet(fp)


def test_list_units_become_one_string(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(
        fp,
        ["A", "B", "C", "D", "E", "F", "G"],
        [
            ["mg/dL"],  # one value
            ["g/dL", "g/dL", " g/dL "],  # duplicates, also after stripping
            ["", "  ", "%"],  # blanks dropped
            [None, "mmol/L"],  # nulls dropped
            ["", None],  # nothing left
            [],
            None,
        ],
    )
    assert collapse_unit_metadata(fp) == UnitSummary(4, 0, 0, 0)
    out = pl.read_parquet(fp)
    assert out.schema["unit"] == pl.String
    assert out["unit"].to_list() == ["mg/dL", "g/dL", "%", "mmol/L", None, None, None]
    # No unit siblings: no parent_codes column is added.
    assert out.columns == ["code", "description", "unit"]
    CodeMetadataSchema.validate(pq.read_table(fp))


def test_several_units_are_an_error_and_nothing_is_written(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(fp, ["C0", "C1", "C2"], [["mg/dL", "mmol/L"], ["%"], ["ml", " mL"]])
    before = fp.read_bytes()
    with pytest.raises(
        ValueError,
        match=r"^2 codes have more than one unit, e\.g\. \['C0', 'C2'\]\..*in the code",
    ):
        collapse_unit_metadata(fp)
    assert fp.read_bytes() == before


def test_string_unit_passes_through_and_no_unit_column_is_a_no_op(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(fp, ["A", "B"], [["mg"], None])
    collapse_unit_metadata(fp)
    before = fp.read_bytes()
    assert collapse_unit_metadata(fp) == UnitSummary(1, 0, 0, 0)
    assert fp.read_bytes() == before

    fp = tmp_path / "no_unit.parquet"
    pl.DataFrame({"code": ["MEDS_BIRTH"], "description": ["x"]}).write_parquet(fp)
    before = fp.read_bytes()
    assert collapse_unit_metadata(fp) == UnitSummary(0, 0, 0, 0)
    assert fp.read_bytes() == before


def test_unit_siblings_get_the_code_without_the_unit_as_parent(tmp_path):
    fp = tmp_path / "codes.parquet"
    codes = [
        "DRUG//a//mcg",  # siblings: same item, the unit is the last component
        "DRUG//a//mg",
        "DRUG//a//ml",
        "DRUG//b//mg",  # unit-suffixed, but no sibling
        "LAB//c//1",  # last component is not the code's unit
        "LAB//c//2",
        "LAB//d",  # a unit that is not in the code
        "MEDS_BIRTH",
    ]
    units = [["mcg"], ["mg"], ["ml"], ["mg"], ["mg/dL"], ["mg/dL"], ["%"], None]
    parents = pl.Series(
        [
            ["ATC//N02", "ATC//N"],
            ["ATC//N"],
            ["DRUG//a", "ATC//N"],
            None,
            None,
            None,
            None,
            None,
        ],
        dtype=pl.List(pl.String),
    )
    _write(fp, codes, units, parent_codes=parents)
    pl.read_parquet(fp).with_columns(
        description=pl.Series(["Drug a"] * 3 + [None] * 5, dtype=pl.String)
    ).write_parquet(fp)
    assert collapse_unit_metadata(fp) == UnitSummary(7, 1, 3, 1)
    out = pl.read_parquet(fp)
    # The stem gets a row of its own, sorted in like the others.
    assert out["code"].to_list() == ["DRUG//a", *codes]
    by_code = {row["code"]: row for row in out.iter_rows(named=True)}
    assert by_code["DRUG//a"] == {
        "code": "DRUG//a",
        "description": "Drug a",  # shared by every sibling
        "unit": None,
        "parent_codes": [
            "ATC//N"
        ],  # the parents every sibling has, not the stem itself
    }
    assert [by_code[c]["parent_codes"] for c in codes] == [
        ["ATC//N02", "ATC//N", "DRUG//a"],  # existing vocabulary parents kept
        ["ATC//N", "DRUG//a"],
        ["DRUG//a", "ATC//N"],  # not added twice
        None,
        None,
        None,
        None,
        None,
    ]
    CodeMetadataSchema.validate(pq.read_table(fp))

    # Idempotent: a second run adds no row and changes nothing.
    before = fp.read_bytes()
    assert collapse_unit_metadata(fp) == UnitSummary(7, 1, 3, 0)
    assert fp.read_bytes() == before


def test_stem_row_without_a_shared_description_or_parents(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(fp, ["X//a//mg", "X//a//g", "Y"], [["mg"], ["g"], None])
    pl.read_parquet(fp).with_columns(
        description=pl.Series(["a in mg", "a, other, in g", None])
    ).write_parquet(fp)
    assert collapse_unit_metadata(fp) == UnitSummary(2, 1, 2, 1)
    out = pl.read_parquet(fp)
    # The input was not sorted by code, so the stem row is appended.
    assert out["code"].to_list() == ["X//a//mg", "X//a//g", "Y", "X//a"]
    assert out.schema["parent_codes"] == pl.List(pl.String)
    assert out["parent_codes"].to_list() == [["X//a"], ["X//a"], None, None]
    assert out.row(3, named=True) == {
        "code": "X//a",
        "description": None,
        "unit": None,
        "parent_codes": None,
    }


def test_existing_stem_row_is_left_unchanged(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(fp, ["X//a", "X//a//g", "X//a//mg"], [["%"], ["g"], ["mg"]])
    pl.read_parquet(fp).with_columns(
        description=pl.Series(["own", "a", "a"])
    ).write_parquet(fp)
    assert collapse_unit_metadata(fp) == UnitSummary(3, 1, 2, 0)
    out = pl.read_parquet(fp)
    assert out.row(0, named=True) == {
        "code": "X//a",
        "description": "own",
        "unit": "%",
        "parent_codes": None,
    }
    assert out["parent_codes"].to_list() == [None, ["X//a"], ["X//a"]]


def test_stem_description_without_the_siblings_units(tmp_path):
    fp = tmp_path / "codes.parquet"
    _write(
        fp,
        ["A//x//g", "A//x//mg", "B//y//g", "B//y//mg"],
        [["g"], ["mg"], ["g"], ["mg"]],
    )
    descriptions = ["Dose [g] of x", "Dose [mg] of x", "y in g", "y, other, in mg"]
    pl.read_parquet(fp).with_columns(description=pl.Series(descriptions)).write_parquet(
        fp
    )
    assert collapse_unit_metadata(fp) == UnitSummary(4, 2, 4, 2)
    out = dict(pl.read_parquet(fp).select("code", "description").iter_rows())
    # They differ only by their own unit: the stem gets the description without it.
    assert out["A//x"] == "Dose of x"
    # They differ otherwise too: no description.
    assert out["B//y"] is None
