# HIRID MEDS ETL

[![PyPI - Version](https://img.shields.io/pypi/v/HIRID_MEDS)](https://pypi.org/project/HIRID_MEDS)
[![codecov](https://codecov.io/gh/mmcdermott/ETL_MEDS_Template/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/mmcdermott/ETL_MEDS_Template)
[![tests](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/tests.yaml/badge.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/tests.yml)
[![code-quality](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/mmcdermott/ETL_MEDS_Template#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/pulls)
[![contributors](https://img.shields.io/github/contributors/mmcdermott/ETL_MEDS_Template.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/graphs/contributors)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)
[![DOI](https://zenodo.org/badge/936180918.svg)](https://doi.org/10.5281/zenodo.17370178)

Warning: This ETL needs a lot of memory unless polars' streaming engine is enabled; see
[Resources and parallelism](#resources-and-parallelism).

This repository contains the ETL (Extract, Transform, Load) code to convert the HIRID dataset
into the [MEDS](https://medical-event-data-standard.github.io/) ecosystem.

HiRID is a freely accessible critical care dataset containing data relating to more than 33 thousand patient admissions to the Department of Intensive Care Medicine of the Bern University Hospital, Switzerland (ICU), an interdisciplinary 60-bed unit admitting >6,500 patients per year. The ICU offers the full range of modern interdisciplinary intensive care medicine for adult patients. The dataset was developed in cooperation between the Swiss Federal Institute of Technology (ETH) Zürich, Switzerland and the ICU.

The dataset contains de-identified demographic information and a total of 712 routinely collected physiological variables, diagnostic test results and treatment parameters from more than 33 thousand admissions during the period from January 2008 to June 2016. Data is stored with a uniquely high time resolution of one entry every two minutes.

source: https://hirid.intensivecare.ai/

```bash
pip install HIRID_MEDS
export DATASET_DOWNLOAD_USERNAME=... DATASET_DOWNLOAD_PASSWORD=...
export POLARS_ENGINE_AFFINITY=streaming  # see "Resources and parallelism"

meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR
```

This downloads only the three release files the ETL reads (`reference_data.tar.gz`,
`raw_stage/observation_tables_parquet.tar.gz`, `raw_stage/pharma_records_parquet.tar.gz`, ~7 GB),
unpacks them, and runs the extraction.

### Using an existing download

If you already have the release on disk, point the ETL at it and skip the download:

```bash
meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR do_download=false input_dir=$HIRID_DIR
```

`$HIRID_DIR` must contain the **unpacked** files, laid out the way the download step leaves them:

```
$HIRID_DIR/
├── general_table.csv                    # from reference_data.tar.gz
├── hirid_variable_reference.csv         # from reference_data.tar.gz
└── raw_stage/
    ├── observation_tables/parquet/part-*.parquet
    └── pharma_records/parquet/part-*.parquet
```

All three archives are required; the two `raw_stage` parquet archives alone are not enough, as
patients, demographics and variable names come from `reference_data.tar.gz`. To unpack a manual
download:

```bash
cd $HIRID_DIR
tar -xzf reference_data.tar.gz
(cd raw_stage && tar -xzf observation_tables_parquet.tar.gz && tar -xzf pharma_records_parquet.tar.gz)
```

Note that the download step only unpacks files it fetches itself: archives already present with a
matching checksum are skipped and therefore *not* unpacked.

## Configuration

**This package contains no ETL code.** The entire pipeline is one file,
[`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml), registered under the
`MEDS_extract.pipelines` entry-point group.

Everything the old `pre_MEDS.py` did is now config:

| Was                                                       | Now                                                      |
| --------------------------------------------------------- | -------------------------------------------------------- |
| `.tar.gz` extraction                                      | `unarchive: auto` on the PhysioNet source                |
| `get_patient_link` — pseudo DOB                           | `_table.cols`: `date_of_birth: $_admitted - $age::years` |
| `save_last_event` — death/discharge from last observation | `_table.join` with `cols: {datetime: max}`               |
| Variable-reference join                                   | `_table.join` on (`Source Table`, `ID`)                  |

The modelling choices behind the config (codes, null handling, deduplication, what counts as
death/discharge) and the numbers from a full run are documented in
[`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md).

## Output

| Code                                   | Time                                                   | `numeric_value` | `text_value`                   |
| -------------------------------------- | ------------------------------------------------------ | --------------- | ------------------------------ |
| `HOSPITAL_ADMISSION`                   | `admissiontime`                                        |                 |                                |
| `SEX//{sex}`                           | static                                                 |                 |                                |
| `MEDS_BIRTH`                           | `admissiontime - age` (age is binned)                  |                 |                                |
| `HOSPITAL_DISCHARGE`                   | last observation                                       |                 |                                |
| `MEDS_DEATH`                           | last observation, if `discharge_status` is not `alive` |                 |                                |
| `OBSERVATION//{Variable Name}//{Unit}` | `datetime`                                             | `value`         | `stringvalue` (non-empty only) |
| `MEDICATION//{Variable Name}`          | `givenat`                                              | `givendose`     | `doseunit`                     |

## Resources and parallelism

A full run reads ~777M observation rows. Run it with polars' streaming engine:

```bash
export POLARS_ENGINE_AFFINITY=streaming
```

With it, a full run takes ~13 minutes and peaks at ~9.5 GB RSS (single worker, 10-core / 24 GB
laptop). Without it, `split_and_shard_subjects` is killed for running out of memory on the same
machine: MEDS-Extract applies each table's variable-reference join before collecting subject ids,
so the in-memory engine materializes the join over every observation row.

Stages can also run in parallel through a MEDS-transforms stage-runner file:

```bash
pip install "HIRID_MEDS[local_parallelism]"
cat > runner.yaml <<'YAML'
parallelize:
  n_workers: 8
  launcher: joblib
YAML
meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR stage_runner_fp=runner.yaml
```

`etl.n_subjects_per_shard` in the config (default 1000) controls the shard size.

## Citation

If you use this dataset, please cite the original publication below and the ETL (see cite this repository):

```
Faltys, M., Zimmermann, M., Lyu, X., Hüser, M., Hyland, S., Rätsch, G., & Merz, T. (2021). HiRID, a high time-resolution ICU dataset (version 1.1.1). PhysioNet. https://doi.org/10.13026/nkwc-js72.

Hyland, S.L., Faltys, M., Hüser, M. et al. Early prediction of circulatory failure in the intensive care unit using machine learning. Nat Med 26, 364–373 (2020). https://doi.org/10.1038/s41591-020-0789-4
```
