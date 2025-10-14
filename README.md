# Extract your custom dataset via MEDS-Transforms

[![codecov](https://codecov.io/gh/mmcdermott/ETL_MEDS_Template/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/mmcdermott/ETL_MEDS_Template)
[![tests](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/tests.yaml/badge.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/tests.yml)
[![code-quality](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/mmcdermott/ETL_MEDS_Template#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/pulls)
[![contributors](https://img.shields.io/github/contributors/mmcdermott/ETL_MEDS_Template.svg)](https://github.com/mmcdermott/ETL_MEDS_Template/graphs/contributors)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)

Warning: This ETL currently needs a lot of resources to run.

A template repository for a MEDS-Transforms powered extraction pipeline for a custom dataset. Once you have
customized the repository to your dataset (see instructions below), you will be able to run your extraction
pipeline with a few simple command-line commands, such as:

```bash
pip install HIRID_MEDS # you can do this locally or via PyPI
# Download your data or set download credentials
MEDS_extract-HIRID root_output_dir=$ROOT_OUTPUT_DIR do_download=true raw_input_dir=$RAW_INPUT_DIR
```

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

## The MIMIC-IV OMOP Dataset

We use the demo dataset for MIMIC-IV in the OMOP format, which is a subset of the MIMIC-IV dataset.
This dataset downloaded from Physionet does not include the standard dictionary linking definitions but should otherwise
be functional

## Particularities

- Care site is added to the visit as text
- Add support for care_site table (visit_detail)

## Citation

If you use this dataset, please cite the original publication below and the ETL (see cite this repository):

```
Faltys, M., Zimmermann, M., Lyu, X., Hüser, M., Hyland, S., Rätsch, G., & Merz, T. (2021). HiRID, a high time-resolution ICU dataset (version 1.1.1). PhysioNet. https://doi.org/10.13026/nkwc-js72.

Hyland, S.L., Faltys, M., Hüser, M. et al. Early prediction of circulatory failure in the intensive care unit using machine learning. Nat Med 26, 364–373 (2020). https://doi.org/10.1038/s41591-020-0789-4
```
