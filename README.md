# HiRID MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/HIRID-MEDS)](https://pypi.org/project/HIRID-MEDS/)
[![tests](https://github.com/rvandewater/HIRID_MEDS/actions/workflows/synthetic-etl.yaml/badge.svg)](https://github.com/rvandewater/HIRID_MEDS/actions/workflows/synthetic-etl.yaml)
[![code-quality](https://github.com/rvandewater/HIRID_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/rvandewater/HIRID_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/rvandewater/HIRID_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/rvandewater/HIRID_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/rvandewater/HIRID_MEDS.svg)](https://github.com/rvandewater/HIRID_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/936180918.svg)](https://doi.org/10.5281/zenodo.17370178)

> [!WARNING]
> **Extracts made with HIRID_MEDS 0.0.1–0.0.4 contain duplicated, mislabeled and missing events.**
> Those releases joined the variable reference on `ID` alone. On HiRID 1.1.1 that adds a second,
> drug-named event to ~24M observation rows (3.1%), mislabels ~123k rows, and drops ~79k. Please
> re-extract with this version. Details:
> [#10](https://github.com/rvandewater/HIRID_MEDS/issues/10).
> **This is fixed in 0.1.0 with the update to MEDS-Extract 0.7**

This pipeline extracts the
[HiRID high time-resolution ICU dataset v1.1.1](https://physionet.org/content/hirid/1.1.1/) from
PhysioNet into the [MEDS format](https://medical-event-data-standard.github.io/). HiRID holds 33,905
admissions to the Department of Intensive Care Medicine of the Bern University Hospital, Switzerland,
between January 2008 and June 2016, with physiological variables, diagnostic test results and
treatment parameters recorded as often as every two minutes. It was built from the
[ETL_MEDS_Template](https://github.com/Medical-Event-Data-Standard/ETL_MEDS_Template) and uses the
config-only layout introduced by [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) 0.7.

The ETL maps the patient table, the variable reference and the two `raw_stage` tables (observations
and pharma records). It never downloads or reads the `merged_stage` / `imputed_stage` derivatives or
the CSV copies of the raw tables.

## Usage

HiRID is a credentialed-access dataset. You need a credentialed PhysioNet account that has completed
the required training and signed the data use agreement.

```bash
pip install HIRID-MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
export POLARS_ENGINE_AFFINITY=streaming  # see "Resources" below
meds-extract-download spec=HIRID output_dir=$RAW_INPUT_DIR
meds-extract-run spec=HIRID output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
hirid-meds-describe-codes $MEDS_OUTPUT_DIR
```

When you run this, the program will:

1. Download the three release files the ETL reads (`reference_data.tar.gz`,
    `raw_stage/observation_tables_parquet.tar.gz`, `raw_stage/pharma_records_parquet.tar.gz`, ~7 GB)
    into `$RAW_INPUT_DIR`, and unpack them.
2. Build the MEDS cohort from the unpacked files and write it to `$MEDS_OUTPUT_DIR` (`data/` and
    `metadata/`). All transformations are declared in
    [`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml).
3. Fill in the descriptions of the four literal codes (`HOSPITAL_ADMISSION`, `MEDS_BIRTH`, …) in
    `metadata/codes.parquet`, which MEDS-Extract cannot describe (see
    [Code metadata](#code-metadata)). The extraction is complete without this step.

`meds-extract-run spec=HIRID output_dir=$MEDS_OUTPUT_DIR` without `do_download=false` does both steps
in one command.

### Using an existing download

If you already have the release on disk, skip the download command and point `input_dir` at a
directory holding the **unpacked** files, laid out the way the download step leaves them:

```
$RAW_INPUT_DIR/
├── general_table.csv                    # from reference_data.tar.gz
├── hirid_variable_reference.csv         # from reference_data.tar.gz
└── raw_stage/
    ├── observation_tables/parquet/part-*.parquet
    └── pharma_records/parquet/part-*.parquet
```

All three archives are required. The two `raw_stage` parquet archives alone are not enough: patients,
demographics and variable names come from `reference_data.tar.gz`. To unpack a manual download:

```bash
cd $RAW_INPUT_DIR
tar -xzf reference_data.tar.gz
(cd raw_stage && tar -xzf observation_tables_parquet.tar.gz && tar -xzf pharma_records_parquet.tar.gz)
```

The download step only unpacks files it fetches itself: archives already present with a matching
checksum are skipped and therefore *not* unpacked.

### Resources

A full run reads ~777M observation rows. Run it with polars' streaming engine
(`export POLARS_ENGINE_AFFINITY=streaming`). With it, a full run takes ~13 minutes and peaks at
~9.5 GB RSS (single worker, 10-core / 24 GB laptop). Without it, `split_and_shard_subjects` is killed
for running out of memory on the same machine
([#9](https://github.com/rvandewater/HIRID_MEDS/issues/9)).

To parallelize the stages, pass a runner file:

```bash
pip install "HIRID-MEDS[local_parallelism]"
printf 'parallelize:\n  n_workers: 8\n  launcher: joblib\n' >runner.yaml
meds-extract-run spec=HIRID output_dir=$MEDS_OUTPUT_DIR do_download=false \
	input_dir=$RAW_INPUT_DIR stage_runner_fp=runner.yaml
```

Run `meds-extract-run --help` for every option.

## How this ETL is defined

The whole ETL is one MESSY (MEDS-Extract Specification Syntax YAML) file,
[`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml). The package contains no
ETL code; its only code is the optional `hirid-meds-describe-codes` step (see
[Code metadata](#code-metadata)). The file declares:

- **`sources:`**: where the raw data lives (the PhysioNet release, restricted to the three archives
    above, unpacked after download).
- **`etl:`**: the dataset name and sharding options.
- **One block per raw table**: how each row becomes one or more MEDS events, and which tables it is
    joined with.

`pyproject.toml` registers the file with MEDS-Extract's `MEDS_extract.pipelines` entry-point group
under the name `HIRID`. That registration is what makes `spec=HIRID` work.

Everything the pre-0.7 `pre_MEDS.py` did is now config:

| Was                                                       | Now                                                      |
| --------------------------------------------------------- | -------------------------------------------------------- |
| `.tar.gz` extraction                                      | `unarchive: auto` on the PhysioNet source                |
| `get_patient_link` — pseudo DOB                           | `_table.cols`: `date_of_birth: $_admitted - $age::years` |
| `save_last_event` — death/discharge from last observation | `_table.join` with `cols: {datetime: max}`               |
| Variable-reference join                                   | `_table.join` on (`ID`, `Source Table`)                  |

## Events

| Table                          | Event codes                            | Notes                                                                                      |
| ------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------ |
| `general_table`                | `HOSPITAL_ADMISSION`, `SEX//{sex}`     | Admission at `admissiontime`; sex is static.                                               |
|                                | `MEDS_BIRTH`                           | `admissiontime` minus `age` years.                                                         |
|                                | `HOSPITAL_DISCHARGE`, `MEDS_DEATH`     | Both at the patient's last observation; death only when `discharge_status` is not `alive`. |
| `raw_stage/observation_tables` | `OBSERVATION//{Variable Name}//{Unit}` | `value` is the `numeric_value`; a non-empty `stringvalue` is the `text_value`.             |
| `raw_stage/pharma_records`     | `MEDICATION//{Variable Name}`          | `givendose` is the `numeric_value`; `doseunit` is the `text_value`.                        |

`Variable Name` and `Unit` come from `hirid_variable_reference`, matched on the raw id **and** its
`Source Table`, since one id is listed as both an observation and a drug. An id with no reference
entry for its own table keeps its raw id in the code (`OBSERVATION//<variableid>//UNK`,
`MEDICATION//<pharmaid>`). A missing sex or unit becomes `UNK`.

A full run of HiRID 1.1.1 yields 33,905 admissions, 33,904 discharges, 2,062 deaths, 16.25M
medication events and 758.65M observation events. See
[`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md) for every modelling choice, the numbers behind it, and
the full-run validation.

### Data notes

- **Deidentified dates.** HiRID shifts dates into the 22nd century while keeping chronology within a
    patient; they are emitted as supplied.
- **ICU, not hospital.** HiRID covers ICU admissions. The `HOSPITAL_ADMISSION` / `HOSPITAL_DISCHARGE`
    codes are kept from earlier releases; discharge and death are the last observation, not a
    recorded time ([#4](https://github.com/rvandewater/HIRID_MEDS/issues/4)).
- **Pseudo date of birth.** `age` is binned (15 values, 20–90), so `MEDS_BIRTH` can be off by up to
    ~5 years.
- **Duplicates are collapsed.** MEDS-Extract drops events identical in every column. This removes
    3.1M exact raw duplicates and merges 15.1M coincident readings of different variable ids that
    share a name and unit ([#8](https://github.com/rvandewater/HIRID_MEDS/issues/8)).
- **Unmapped columns.** Validity and status columns (`status`, `type`, `recordstatus`, `typeid`, …)
    are not extracted ([#5](https://github.com/rvandewater/HIRID_MEDS/issues/5)).

## Code metadata

`metadata/codes.parquet` carries `code`, `description` and `code_template`.
Releases up to 0.1.0 wrote no metadata (0.1.0 writes only `code`; 0.0.x wrote an empty string as
every description), which gives tooling that maps codes across datasets or builds features from
descriptions nothing to work with. The descriptions now come from the release's own variable
dictionary, joined at ETL time. The ETL adds no vocabulary parents (no `parent_codes`); suMEDS adds
LOINC parents from the packaged rules file (see
[Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)).

| Codes                                                      | Route                                                                                                                  | Source                                                                                                                                           |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `OBSERVATION//{Variable Name}//{Unit}`                     | `_metadata` block on `hirid_variable_reference`, joined on the raw `variableid` (its `Observation` rows only)          | `Variable Name`, `Unit` and `Additional information` of your copy of the reference, read at ETL time; nothing from it is shipped                 |
| `MEDICATION//{Variable Name}`                              | `_metadata` block on `hirid_variable_reference`, joined on the raw `pharmaid` (its `Pharma` rows only)                 | `Variable Name` of the reference, read at ETL time                                                                                               |
| `OBSERVATION//<variableid>//UNK`, `MEDICATION//<pharmaid>` | `_metadata: _self:` fallback, non-null only on rows of ids without a reference entry (see D3 in `DESIGN_DECISIONS.md`) | the raw id                                                                                                                                       |
| `SEX//{sex}`                                               | `_metadata: _self:` template                                                                                           | `general_table.sex`, documented as `M` or `F` in the [HiRID data structure docs](https://hirid.intensivecare.ai/structure-of-the-published-data) |

The dictionary is joined rather than evaluated per event because `observation_tables` has ~777M
rows: an external `_metadata` table is read once per shard, while a `_self` value is carried on
every event row. The fallback `_self` blocks are null on all but the fallback rows; on a 5.7M-event
synthetic build they changed neither peak memory nor run time measurably.

`MEDICATION` codes get no vocabulary parent: the drug names are Swiss products, and no public
HiRID drug mapping to RxNorm or ATC was found (WeavEHR maps no pharma ids; the circEWS pharma
reference lists German generic names only).

`tests/test_code_metadata.py` checks that every component-coded event is described and that no
block adds parent codes; `tests/test_etl.py` checks the extracted `codes.parquet` against the
fixture and validates it against `meds.CodeMetadataSchema`.

**Literal codes** (`HOSPITAL_ADMISSION`, `HOSPITAL_DISCHARGE`, `MEDS_BIRTH`, `MEDS_DEATH`) have no
components for a `_metadata` block to join on, and the pre-existing `metadata/codes.parquet` that
MEDS-Extract documents for this case is not read by its standard pipeline (`extract_code_metadata`
reads the output of its prior metadata stage, `split_and_shard_subjects`, which writes no
`codes.parquet`). `hirid-meds-describe-codes $MEDS_OUTPUT_DIR` fills them in after the run from
[`src/HIRID_MEDS/metadata/static_code_descriptions.csv`](src/HIRID_MEDS/metadata/static_code_descriptions.csv),
which records the source of every row (`hirid-docs`: the HiRID data structure docs; this ETL's
config). It only writes descriptions that are null, never adds codes, is idempotent, and validates
the file against `meds.CodeMetadataSchema` before replacing it. Without it, these four codes stay
undescribed.

## Enriching code metadata with suMEDS

Vocabulary work (LOINC tables, ontology checks) lives in
[suMEDS](https://github.com/rvandewater/SumMEDS) and its versioned ontology data (sumeds-ontologies,
pinned release), which maintain one copy for all MEDS ETLs. This package ships a rules file for it,
[`src/HIRID_MEDS/sumeds_rules.yaml`](src/HIRID_MEDS/sumeds_rules.yaml) (package data, format
`sumeds-rules/0.1`). After the ETL, run:

```bash
uvx --from "git+https://github.com/rvandewater/SumMEDS" suMEDS-enrich \
    $MEDS_OUTPUT_DIR/metadata/codes.parquet -o codes-enriched.parquet \
    --rules src/HIRID_MEDS/sumeds_rules.yaml
```

Two crosswalk rules look up the variable name (`OBSERVATION//{Variable Name}//…`) or the raw id
(`OBSERVATION//{variableid}//UNK`) in the `hirid/observation_loinc` crosswalk of suMEDS's versioned
ontology data: the 82 observation variable ids whose public HiRID variable name (HiRID variable
sheet; HIRID-ICU-Benchmark `varref.tsv`, MIT) is exactly the long common name of one LOINC term.
Each match is validated against the LOINC pack there and gets `parent_codes = ["LOINC//<code>"]`,
the LOINC long common name as description where the code had none, and
`vocabulary_id`/`concept_code`. On HiRID 1.1.1, 67 of the 123 observation codes get a LOINC parent. The ETL alone does not emit these parents. HiRID has no
same-dataset hierarchy (no code is a coarser version of another), so the file has no hierarchy
rules. `tests/test_sumeds_rules.py` checks that the file is well formed and that every rule matches
the code layouts of `configs/messy.yaml`.

## Testing

`tests/test_etl.py` builds a small hand-written fixture with the release's layout and dtypes, runs
`meds-extract-run` over it, and checks the shared-id join, dtype alignment, fallback codes, text
values and patient events. The [synthetic ETL workflow](.github/workflows/synthetic-etl.yaml) also
generates raw files straight from the config with
[MESSY-synth](https://github.com/rvandewater/MESSY_synth) and fails if any event block yields no
events.

```bash
uv sync
uv run pytest tests/ -v
uv run pre-commit run --all-files
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development and release workflow. To run the
synthetic ETL locally:

```bash
uv pip install "MESSY_synth @ git+https://github.com/rvandewater/MESSY_synth@de7a888d1045e943aabb218bcf83d2e83f4d3219"
uv run MESSY-synth ./src/HIRID_MEDS/configs/messy.yaml -o raw_synthetic/ --explain
uv run meds-extract-run spec=HIRID output_dir=./HIRID_meds_synth input_dir=raw_synthetic do_download=false
uv run hirid-meds-describe-codes ./HIRID_meds_synth
```

## Citation

If you use this ETL, please cite the HiRID database:

```bibtex
@article{PhysioNet-hirid-1.1.1,
  author = {Faltys, Martin and Zimmermann, Marc and Lyu, Xinrui and Hüser, Matthias and Hyland, Stephanie and Rätsch, Gunnar and Merz, Tobias},
  title = {{HiRID, a high time-resolution ICU dataset}},
  journal = {{PhysioNet}},
  year = {2021},
  month = feb,
  note = {Version 1.1.1},
  doi = {10.13026/nkwc-js72},
  url = {https://doi.org/10.13026/nkwc-js72}
}
```

And its original publication:

```bibtex
@ARTICLE{Hyland2020-vq,
  title     = "Early prediction of circulatory failure in the intensive care
               unit using machine learning",
  author    = "Hyland, Stephanie L and Faltys, Martin and Hüser, Matthias
               and Lyu, Xinrui and Gumbsch, Thomas and Esteban, Cristóbal
               and Bock, Christian and Horn, Max and Moor, Michael and Rieck,
               Bastian and Zimmermann, Marc and Bodenham, Dean and Borgwardt,
               Karsten and Rätsch, Gunnar and Merz, Tobias M",
  journal   = "Nat. Med.",
  publisher = "Springer Science and Business Media LLC",
  volume    =  26,
  number    =  3,
  pages     = "364--373",
  month     =  mar,
  year      =  2020,
  language  = "en"
}
```

And this ETL:

```bibtex
@software{van_de_Water_HIRID_MEDS_ETL_2025,
author = {van de Water, Robin Philippus},
doi = {10.5281/zenodo.17370179},
license = {MIT},
month = oct,
title = {{HIRID MEDS ETL}},
url = {https://github.com/rvandewater/HIRID_MEDS},
year = {2025}
}
```

See also [`CITATION.cff`](CITATION.cff).
