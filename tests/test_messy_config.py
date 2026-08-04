"""Validate the bundled MESSY config against the installed MEDS-Extract.

These tests need no raw data and no credentials, so they run in CI on every push. They are the
regression net for the 0.7 migration: a MESSY file that stops parsing (a bad dftly expression, a
stray 0.6.x key, an `etl:` option MEDS-Extract does not accept) fails here rather than several
stages into a multi-hour extraction run.
"""

import pytest

from MEDS_extract.config import MessyConfig

from HIRID_MEDS import MESSY_CFG, PIPELINE_NAME


@pytest.fixture(scope="module")
def cfg() -> MessyConfig:
    return MessyConfig.load(MESSY_CFG)


def test_messy_config_parses(cfg: MessyConfig):
    """Every event table, code expression, and time cast in the config is valid dftly."""
    tables = cfg.event_tables
    assert tables, "MESSY config declares no event tables."
    for table in tables:
        assert table.events, f"Table {table.input_prefix!r} declares no events."


def test_expected_tables_and_events(cfg: MessyConfig):
    """The migrated config covers exactly the tables the 0.6.x event config covered."""
    by_prefix = {t.input_prefix: {e.name for e in t.events} for t in cfg.event_tables}
    assert by_prefix == {
        "patient": {"admission", "sex", "birth", "death", "discharge"},
        "raw_stage/pharma_records_parquet": {"medication"},
        "raw_stage/observation_tables": {"observation"},
    }


def test_subject_id_is_patientid(cfg: MessyConfig):
    """Every table inherits the global `_defaults.subject_id`."""
    for table in cfg.event_tables:
        assert table.subject_id_node is not None, table.input_prefix
        assert table.subject_id_node.referenced_columns == {"patientid"}


def test_spaced_reference_columns_are_aliased(cfg: MessyConfig):
    """The reference table's space-containing columns are aliased via `_table.cols`.

    dftly's `$name` shorthand cannot express `Variable Name` (it fails to lex), but the explicit
    node form `{column: Variable Name}` bypasses the string grammar. Aliasing once in
    `_table.cols` keeps the event expressions readable and avoids any pre-MEDS renaming.
    """
    by_prefix = {t.input_prefix: t for t in cfg.event_tables}
    for prefix in ("raw_stage/pharma_records_parquet", "raw_stage/observation_tables"):
        cols = by_prefix[prefix].cols
        assert cols["variable_name"].referenced_columns == {"Variable Name"}, prefix
        assert cols["unit"].referenced_columns == {"Unit"}, prefix


def test_etl_block(cfg: MessyConfig):
    """The reserved `etl:` block carries the dataset identity and stage options."""
    assert cfg.etl.dataset_name == "hirid"
    assert cfg.etl.stage_options["n_subjects_per_shard"] == 1000


def test_sources_declare_dataset_version(cfg: MessyConfig):
    """`sources.dataset_version` is what stamps `etl_metadata.dataset_version` on the output."""
    assert cfg.sources_version == "1.1.1"


def test_registered_pipeline_name_resolves():
    """The `MEDS_extract.pipelines` entry point resolves to the bundled MESSY file.

    This is what makes `meds-extract-run spec=HIRID output_dir=...` work.
    """
    cfg = MessyConfig.load(PIPELINE_NAME)
    assert cfg.registered_name == PIPELINE_NAME
    assert [t.input_prefix for t in cfg.event_tables]
