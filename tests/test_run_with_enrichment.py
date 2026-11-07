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

suMEDS records the enrichment in `metadata/dataset.json`, and `hirid-meds-describe-codes` rewrites
only `codes.parquet`, so the record survives it. With an empty pack source, the enrichment fails
without failing the ETL: the plain `codes.parquet` is kept, the failure is recorded, and
`suMEDS-enrich` on the output enriches it later, before or after `hirid-meds-describe-codes`.
"""

import csv
import hashlib
import io
import json
import shutil
from pathlib import Path
from typing import NamedTuple

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import run_command
from meds import CodeMetadataSchema, DatasetMetadataSchema

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


def run_with_enrichment(output_dir: Path, raw_dir: Path, ontology_source: Path) -> Path:
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
    return output_dir


def enrichment_record(output_dir: Path) -> dict:
    """The `sumeds_enrichment` record of the output's `dataset.json`, which must stay valid MEDS."""
    metadata = json.loads((output_dir / "metadata" / "dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    return metadata["sumeds_enrichment"]


class Enriched(NamedTuple):
    output_dir: Path
    after_sumeds: pa.Table
    after_describe: pa.Table
    #: `metadata/dataset.json` after `suMEDS-extract`, before `hirid-meds-describe-codes`.
    dataset_json_after_sumeds: bytes


@pytest.fixture(scope="session")
def enriched(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Enriched:
    """The output directory, and its metadata after each of the two steps."""
    output_dir = tmp_path_factory.mktemp("hirid_enriched") / "out"
    run_with_enrichment(output_dir, raw_dir, ontology_source)
    codes_fp = output_dir / "metadata" / "codes.parquet"
    after_sumeds = pq.read_table(codes_fp)
    dataset_json = (output_dir / "metadata" / "dataset.json").read_bytes()
    run_command(["hirid-meds-describe-codes", str(output_dir)])
    return Enriched(output_dir, after_sumeds, pq.read_table(codes_fp), dataset_json)


@pytest.fixture(scope="session")
def soft_failed_dir(tmp_path_factory, raw_dir: Path) -> Path:
    """`suMEDS-extract` with an empty pack source, as on a site that cannot reach the packs."""
    output_dir = tmp_path_factory.mktemp("hirid_soft_fail") / "out"
    return run_with_enrichment(output_dir, raw_dir, tmp_path_factory.mktemp("no_packs"))


def _rows(table: pa.Table) -> dict[str, dict]:
    return {row["code"]: row for row in table.to_pylist()}


def test_codes_parquet_stays_valid_meds_metadata(enriched):
    output_dir, after_sumeds, after_describe, _ = enriched
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
    output_dir = enriched.output_dir
    events = pl.read_parquet(output_dir / "data" / "**" / "*.parquet")
    key = ["subject_id", "time", "code"]
    assert events.sort(key).select(key).equals(meds.sort(key).select(key))


def test_enrichment_is_non_destructive(enriched, meds_dir: Path):
    _, after_sumeds, after_describe, _ = enriched
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
    _, after_sumeds, after_describe, _ = enriched
    static = dict(read_static_code_descriptions().iter_rows())
    undescribed = {
        c for c, r in _rows(after_sumeds).items() if r["description"] is None
    }
    assert undescribed == set(static)
    after = _rows(after_describe)
    assert {code: after[code]["description"] for code in static} == static
    assert all(row["description"] is not None for row in after.values())


def test_observation_codes_get_loinc_parents(enriched):
    rows = _rows(enriched.after_describe)
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


def test_dataset_json_records_the_enrichment(enriched):
    record = enrichment_record(enriched.output_dir)
    assert record["enriched"] is True
    assert record["rules"] == "HIRID"
    assert record["ontology_source"] == "local"
    described = pl.from_arrow(enriched.after_sumeds)["description"].is_not_null().sum()
    assert record["coverage"]["codes"] == enriched.after_sumeds.num_rows
    assert record["coverage"]["described"][1] == described
    assert record["coverage"]["with_parent_codes"] == [0, len(EXPECTED_PARENTS)]


def test_describe_codes_keeps_the_enrichment_record(enriched):
    """`hirid-meds-describe-codes` rewrites only `codes.parquet`, never `dataset.json`."""
    dataset_json = enriched.output_dir / "metadata" / "dataset.json"
    assert dataset_json.read_bytes() == enriched.dataset_json_after_sumeds


def test_failed_enrichment_keeps_the_plain_etl_output(
    soft_failed_dir: Path, meds_dir: Path
):
    codes = Path("metadata", "codes.parquet")
    assert (soft_failed_dir / codes).read_bytes() == (meds_dir / codes).read_bytes()
    record = enrichment_record(soft_failed_dir)
    assert record["enriched"] is False
    assert record["rules"] == "HIRID"
    # The pack directory is not recorded, so the retry asks for it again.
    assert record["retry"] == "suMEDS-enrich <MEDS_ROOT> --ontology-source <PACKS_DIR>"


@pytest.mark.parametrize("describe_first", [False, True])
def test_failed_enrichment_can_be_retried(
    soft_failed_dir: Path,
    ontology_source: Path,
    enriched,
    tmp_path: Path,
    describe_first: bool,
):
    """`suMEDS-enrich OUT` gives the table of a direct run, before or after describing the codes."""
    output_dir = tmp_path / "out"
    shutil.copytree(soft_failed_dir / "metadata", output_dir / "metadata")
    dataset_json = output_dir / "metadata" / "dataset.json"
    if describe_first:
        failed = dataset_json.read_bytes()
        run_command(["hirid-meds-describe-codes", str(output_dir)])
        assert dataset_json.read_bytes() == failed
        assert enrichment_record(output_dir)["enriched"] is False
    run_command(
        ["suMEDS-enrich", str(output_dir), "--ontology-source", str(ontology_source)]
    )
    assert enrichment_record(output_dir)["enriched"] is True
    expected = enriched.after_describe if describe_first else enriched.after_sumeds
    retried = pq.read_table(output_dir / "metadata" / "codes.parquet")
    assert pl.from_arrow(retried).equals(pl.from_arrow(expected))
