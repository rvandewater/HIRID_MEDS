"""`suMEDS-extract spec=HIRID`, then `hirid-meds-describe-codes`, run offline over the fixture.

This package registers its rules in suMEDS's `sumeds.rules` entry-point group under the same name
as its MEDS-Extract spec, so `suMEDS-extract` finds them from `spec=HIRID` alone. The ontology
source is a tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, built here: a LOINC
pack with made-up titles and a `hirid/observation_loinc` crosswalk for the variables of
``conftest.py``. The test reads nothing over the network and does not depend on the published
packs.

Both steps only fill null descriptions, so on a code both could describe the first would win. The
test checks that no code is in both: suMEDS leaves the literal codes undescribed, and the static
descriptions fill exactly those.
"""

import csv
import hashlib
import io
import json
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import run_command
from meds import CodeMetadataSchema

from HIRID_MEDS.code_descriptions import read_static_code_descriptions

# LOINC code -> made-up title. 9269-2 is deliberately missing, so the crosswalk row that names it
# must not enrich its code (the rules validate every crosswalk code against the pack).
LOINC = {
    "8867-4": "Synthetic LOINC title 8867-4",
    "2160-0": "Synthetic LOINC title 2160-0",
}

# The `hirid/observation_loinc` rows: (variableid, variable_name, loinc_code). Heart rate is found
# by its name, the unreferenced id 999 by its id. `Lab A` has no row.
CROSSWALK = [
    ("100", "Heart rate", "8867-4"),
    ("999", "Synthetic variable 999", "2160-0"),
    ("200", "GCS", "9269-2"),
]

# Code -> the parents suMEDS must add; every other code must get none.
EXPECTED_PARENTS = {
    "OBSERVATION//Heart rate///min": ["LOINC//8867-4"],
    "OBSERVATION//999//UNK": ["LOINC//2160-0"],
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode()


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local `sumeds-ontologies/0.1` repository: the LOINC pack and the HiRID crosswalk."""
    root = tmp_path_factory.mktemp("ontologies")

    pack = root / "LOINC" / "synthetic"
    pack.mkdir(parents=True)
    columns = ["code", "description"]
    shards = {}
    for key in sorted({code[:1] for code in LOINC}):
        data = _csv([columns, *([c, t] for c, t in LOINC.items() if c[:1] == key)])
        (pack / f"{key}.csv").write_bytes(data)
        shards[key] = {"file": f"{key}.csv", "sha256": _sha256(data)}
    manifest = json.dumps(
        {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": "LOINC",
            "release": "synthetic",
            "columns": columns,
            # As in the published LOINC pack, terms have no parent column.
            "fields": {"code": "code", "description": "description", "parent": None},
            "shard_key_length": 1,
            "shards": shards,
        }
    ).encode()
    (pack / "manifest.json").write_bytes(manifest)

    crosswalks = root / "crosswalks" / "hirid"
    crosswalks.mkdir(parents=True)
    table = _csv([["variableid", "variable_name", "loinc_code"], *map(list, CROSSWALK)])
    (crosswalks / "observation_loinc.csv").write_bytes(table)
    crosswalk_manifest = json.dumps(
        {
            "schema": "sumeds-crosswalk/0.1",
            "dataset": "hirid",
            "tables": {
                "observation_loinc": {
                    "file": "observation_loinc.csv",
                    "sha256": _sha256(table),
                }
            },
        }
    ).encode()
    (crosswalks / "manifest.json").write_bytes(crosswalk_manifest)

    index = {
        "schema": "sumeds-ontologies/0.1",
        "vocabularies": {
            "LOINC": {
                "manifest": "LOINC/synthetic/manifest.json",
                "sha256": _sha256(manifest),
            }
        },
        "crosswalks": {
            "hirid": {
                "manifest": "crosswalks/hirid/manifest.json",
                "sha256": _sha256(crosswalk_manifest),
            }
        },
    }
    (root / "index.json").write_text(json.dumps(index))
    return root


@pytest.fixture(scope="session")
def enriched(tmp_path_factory, raw_dir: Path, ontology_source: Path):
    """The output directory, and its `codes.parquet` after each of the two steps."""
    output_dir = tmp_path_factory.mktemp("hirid_enriched") / "out"
    codes_fp = output_dir / "metadata" / "codes.parquet"
    run_command(
        [
            "suMEDS-extract",
            "spec=HIRID",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
            "--",
            "--ontology-source",
            str(ontology_source),
        ]
    )
    after_sumeds = pq.read_table(codes_fp)
    run_command(["hirid-meds-describe-codes", str(output_dir)])
    return output_dir, after_sumeds, pq.read_table(codes_fp)


def _rows(table: pa.Table) -> dict[str, dict]:
    return {row["code"]: row for row in table.to_pylist()}


def test_codes_parquet_stays_valid_meds_metadata(enriched):
    output_dir, after_sumeds, after_describe = enriched
    CodeMetadataSchema.validate(after_sumeds)
    CodeMetadataSchema.validate(after_describe)
    metadata = output_dir / "metadata"
    assert not list(metadata.glob(".codes.parquet.*")), "suMEDS left its staged output"
    assert not (metadata / "codes.parquet.tmp").exists()
    # `hirid-meds-describe-codes` rewrites only the descriptions: the names stay a
    # map<string, string> and every other column keeps its type.
    assert after_describe.schema.equals(after_sumeds.schema)
    assert pa.types.is_map(after_describe.schema.field("parent_names").type)


def test_data_matches_the_plain_etl(enriched, meds: pl.DataFrame):
    output_dir, _, _ = enriched
    events = pl.read_parquet(output_dir / "data" / "**" / "*.parquet")
    key = ["subject_id", "time", "code"]
    assert events.sort(key).select(key).equals(meds.sort(key).select(key))


def test_enrichment_is_non_destructive(enriched, meds_dir: Path):
    _, after_sumeds, after_describe = enriched
    before = _rows(pq.read_table(meds_dir / "metadata" / "codes.parquet"))
    for after in (after_sumeds, after_describe):
        rows = _rows(after)
        assert rows.keys() == before.keys()
        for code, row in before.items():
            # Existing descriptions are unchanged, and existing parents stay a prefix.
            if row["description"] is not None:
                assert rows[code]["description"] == row["description"], code
            old = row.get("parent_codes") or []
            assert (rows[code]["parent_codes"] or [])[: len(old)] == old, code


def test_static_and_sumeds_descriptions_do_not_overlap(enriched):
    """suMEDS describes none of the literal codes, so the run order of the two steps is free."""
    _, after_sumeds, after_describe = enriched
    static = dict(read_static_code_descriptions().iter_rows())
    undescribed = {
        c for c, r in _rows(after_sumeds).items() if r["description"] is None
    }
    assert undescribed == set(static)
    after = _rows(after_describe)
    assert {code: after[code]["description"] for code in static} == static
    assert all(row["description"] is not None for row in after.values())


def test_observation_codes_get_loinc_parents(enriched):
    _, _, after_describe = enriched
    rows = _rows(after_describe)
    parents = {
        code: row["parent_codes"] for code, row in rows.items() if row["parent_codes"]
    }
    assert parents == EXPECTED_PARENTS

    hr = rows["OBSERVATION//Heart rate///min"]
    # The variable reference's own description is kept; the LOINC name goes into the names map.
    assert hr["description"].startswith("Heart rate [/min] (HiRID observation_tables")
    assert dict(hr["parent_names"]) == {"LOINC//8867-4": LOINC["8867-4"]}
    assert (hr["vocabulary_id"], hr["concept_code"]) == ("LOINC", "8867-4")
    unreferenced = rows["OBSERVATION//999//UNK"]
    assert unreferenced["description"].startswith("HiRID observation variableid 999")
    assert dict(unreferenced["parent_names"]) == {"LOINC//2160-0": LOINC["2160-0"]}

    # A crosswalk code that is not in the pack, and codes without a crosswalk row, stay
    # unenriched.
    for code in (
        "OBSERVATION//GCS//UNK",
        "OBSERVATION//Lab A//mmol/l",
        "MEDICATION//Drug A",
    ):
        assert rows[code]["vocabulary_id"] is None, code
