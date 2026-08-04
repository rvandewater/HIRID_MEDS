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

Warning: This ETL currently needs a lot of resources to run.

This repository contains the ETL (Extract, Transform, Load) code to convert the HIRID dataset
into the [MEDS](https://medical-event-data-standard.github.io/) ecosystem.

HiRID is a freely accessible critical care dataset containing data relating to more than 33 thousand patient admissions to the Department of Intensive Care Medicine of the Bern University Hospital, Switzerland (ICU), an interdisciplinary 60-bed unit admitting >6,500 patients per year. The ICU offers the full range of modern interdisciplinary intensive care medicine for adult patients. The dataset was developed in cooperation between the Swiss Federal Institute of Technology (ETH) Zürich, Switzerland and the ICU.

The dataset contains de-identified demographic information and a total of 712 routinely collected physiological variables, diagnostic test results and treatment parameters from more than 33 thousand admissions during the period from January 2008 to June 2016. Data is stored with a uniquely high time resolution of one entry every two minutes.

source: https://hirid.intensivecare.ai/

```bash
pip install HIRID_MEDS
export DATASET_DOWNLOAD_USERNAME=... DATASET_DOWNLOAD_PASSWORD=...

meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR
```

## Configuration

**This package contains no ETL code.** The entire pipeline is one file,
[`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml), registered under the
`MEDS_extract.pipelines` entry-point group.

Everything the old `pre_MEDS.py` did is now config:

| Was | Now |
| --- | --- |
| `.tar.gz` extraction | `unarchive: auto` on the PhysioNet source |
| `get_patient_link` — pseudo DOB | `_table.cols`: `date_of_birth: $_admitted - $age::years` |
| `save_last_event` — death/discharge from last observation | `_table.join` with `cols: {datetime: max}` |
| Variable-reference join | `_table.join` on `pharmaid`/`variableid` → `ID` |

The reference table's `Variable Name` / `Unit` columns contain spaces, which dftly's `$name`
shorthand cannot express; `_table.cols` aliases them once using the explicit `{column: ...}` form
(see [dftly#96](https://github.com/mmcdermott/dftly/issues/96)).


## MEDS-transforms settings

If you want to convert a large dataset, you can use parallelization with MEDS-transforms
(the MEDS-transformation step that takes the longest).

Using local parallelization with the `hydra-joblib-launcher` package, you can set the number of workers:

```
pip install hydra-joblib-launcher --upgrade
```

Then, you can set the number of workers as environment variable:

```bash
export N_WORKERS=8
```

Moreover, you can set the number of subjects per shard to balance the parallelization overhead based on how many
subjects you have in your dataset:

```bash
export N_SUBJECTS_PER_SHARD=100000
```

## Citation

If you use this dataset, please cite the original publication below and the ETL (see cite this repository):

```
Faltys, M., Zimmermann, M., Lyu, X., Hüser, M., Hyland, S., Rätsch, G., & Merz, T. (2021). HiRID, a high time-resolution ICU dataset (version 1.1.1). PhysioNet. https://doi.org/10.13026/nkwc-js72.

Hyland, S.L., Faltys, M., Hüser, M. et al. Early prediction of circulatory failure in the intensive care unit using machine learning. Nat Med 26, 364–373 (2020). https://doi.org/10.1038/s41591-020-0789-4
```
