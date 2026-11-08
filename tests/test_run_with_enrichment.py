"""`meds-extract-run spec=HIRID` runs the ETL and then suMEDS enrichment, offline over the fixture.

The spec declares suMEDS's `sumeds_enrich` stage in `etl.post_stages`, with the rules this package
registers in suMEDS's `sumeds.rules` entry-point group under the same name, `HIRID`. The ontology
source is a tiny pack repository in suMEDS's `sumeds-ontologies/0.1` layout, built here: a LOINC
pack with made-up titles and a `hirid/observation_loinc` crosswalk for the variables of
``conftest.py``. It is passed to the stage at run time as
`overrides=['++stage_cfg.ontology_source=DIR']`, so the test reads nothing over the network and does
not depend on the published packs.

suMEDS records the enrichment in `metadata/dataset.json`. With an empty pack source, the enrichment
fails without failing the ETL: the plain `codes.parquet` is kept, the failure is recorded, and
`suMEDS-enrich` on the output enriches it later. `skip_stages=[sumeds_enrich]` (the plain build of
``conftest.py``) gives exactly the ETL without the stage.
"""

import csv
import hashlib
import io
import json
import shutil
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml
from conftest import run_command
from meds import CodeMetadataSchema, DatasetMetadataSchema

SPEC_FP = Path(__file__).parents[1] / "src" / "HIRID_MEDS" / "configs" / "messy.yaml"

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
            "meds-extract-run",
            "spec=HIRID",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
            # An option of the `sumeds_enrich` stage. Overrides reach every stage of the pipeline,
            # so `++` adds the key where a stage does not have it.
            f"overrides=['++stage_cfg.ontology_source={ontology_source}']",
        ]
    )
    return output_dir


def enrichment_record(output_dir: Path) -> dict:
    """The `sumeds_enrichment` record of the output's `dataset.json`, which must stay valid MEDS."""
    metadata = json.loads((output_dir / "metadata" / "dataset.json").read_text())
    DatasetMetadataSchema.validate(metadata)
    return metadata["sumeds_enrichment"]


@pytest.fixture(scope="session")
def enriched_dir(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    output_dir = tmp_path_factory.mktemp("hirid_enriched") / "out"
    return run_with_enrichment(output_dir, raw_dir, ontology_source)


@pytest.fixture(scope="session")
def enriched(enriched_dir: Path) -> pa.Table:
    return pq.read_table(enriched_dir / "metadata" / "codes.parquet")


@pytest.fixture(scope="session")
def soft_failed_dir(tmp_path_factory, raw_dir: Path) -> Path:
    """The ETL with an empty pack source, as on a site that cannot reach the packs."""
    output_dir = tmp_path_factory.mktemp("hirid_soft_fail") / "out"
    return run_with_enrichment(output_dir, raw_dir, tmp_path_factory.mktemp("no_packs"))


def _rows(table: pa.Table) -> dict[str, dict]:
    return {row["code"]: row for row in table.to_pylist()}


def test_codes_parquet_stays_valid_meds_metadata(
    enriched_dir: Path, enriched: pa.Table
):
    CodeMetadataSchema.validate(enriched)
    assert not list((enriched_dir / "metadata").glob(".codes.parquet.*")), (
        "suMEDS left its staged output"
    )
    assert pa.types.is_map(enriched.schema.field("parent_names").type)


def test_data_matches_the_plain_etl(enriched_dir: Path, meds: pl.DataFrame):
    events = pl.read_parquet(enriched_dir / "data" / "**" / "*.parquet")
    key = ["subject_id", "time", "code"]
    assert events.sort(key).select(key).equals(meds.sort(key).select(key))


def test_enrichment_is_non_destructive(enriched: pa.Table, meds_dir: Path):
    """Every description, the literal codes' included, is kept, and existing parents stay a prefix."""
    before = _rows(pq.read_table(meds_dir / "metadata" / "codes.parquet"))
    rows = _rows(enriched)
    assert rows.keys() == before.keys()
    for code, row in before.items():
        assert row["description"] is not None, code
        assert rows[code]["description"] == row["description"], code
        old = row.get("parent_codes") or []
        assert (rows[code]["parent_codes"] or [])[: len(old)] == old, code


def test_observation_codes_get_loinc_parents(enriched: pa.Table):
    rows = _rows(enriched)
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


def test_dataset_json_records_the_enrichment(enriched_dir: Path, enriched: pa.Table):
    record = enrichment_record(enriched_dir)
    assert record["enriched"] is True
    assert record["rules"] == "HIRID"
    assert record["ontology_source"] == "local"
    assert record["coverage"]["codes"] == enriched.num_rows
    # The ETL describes every code, the literal ones included, before suMEDS runs.
    assert record["coverage"]["described"] == [enriched.num_rows, enriched.num_rows]
    assert record["coverage"]["with_parent_codes"] == [0, len(EXPECTED_PARENTS)]


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


def test_failed_enrichment_can_be_retried(
    soft_failed_dir: Path, ontology_source: Path, enriched: pa.Table, tmp_path: Path
):
    """`suMEDS-enrich OUT` gives the table of a direct run."""
    output_dir = tmp_path / "out"
    shutil.copytree(soft_failed_dir / "metadata", output_dir / "metadata")
    run_command(
        ["suMEDS-enrich", str(output_dir), "--ontology-source", str(ontology_source)]
    )
    assert enrichment_record(output_dir)["enriched"] is True
    retried = pq.read_table(output_dir / "metadata" / "codes.parquet")
    assert pl.from_arrow(retried).equals(pl.from_arrow(enriched))


def test_skipping_the_stage_gives_the_plain_etl(
    meds_dir: Path, raw_dir: Path, tmp_path: Path
):
    """`skip_stages=[sumeds_enrich]` writes no record, and the same cohort as the spec without the stage."""
    metadata = json.loads((meds_dir / "metadata" / "dataset.json").read_text())
    assert "sumeds_enrichment" not in metadata

    spec = yaml.safe_load(SPEC_FP.read_text())
    del spec["etl"]["post_stages"]
    spec_fp = tmp_path / "messy.yaml"
    spec_fp.write_text(yaml.safe_dump(spec, sort_keys=False))
    output_dir = tmp_path / "out"
    run_command(
        [
            "meds-extract-run",
            f"spec={spec_fp}",
            f"output_dir={output_dir}",
            "do_download=false",
            f"input_dir={raw_dir}",
        ]
    )

    codes = Path("metadata", "codes.parquet")
    assert pq.read_table(output_dir / codes).equals(pq.read_table(meds_dir / codes))
    shards = sorted(
        p.relative_to(output_dir) for p in (output_dir / "data").rglob("*.parquet")
    )
    assert shards == sorted(
        p.relative_to(meds_dir) for p in (meds_dir / "data").rglob("*.parquet")
    )
    for shard in shards:
        assert pq.read_table(output_dir / shard).equals(
            pq.read_table(meds_dir / shard)
        ), shard
