"""HiRID MEDS ETL -- a pure-config MEDS-Extract pipeline.

The entire extraction is `configs/messy.yaml`, registered under the `MEDS_extract.pipelines`
entry-point group, so it runs as:

    meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR
    hirid-meds-describe-codes $OUTPUT_DIR

The second, optional command (`code_descriptions`) only fills in the descriptions of the literal
codes, which MEDS-Extract cannot attach metadata to.
"""

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files

__package_name__ = "HIRID_MEDS"
try:
    __version__ = version(__package_name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

# The name this ETL registers under `MEDS_extract.pipelines`; `meds-extract-run spec=HIRID`
# resolves it, and MEDS-Extract uses it as the default dataset name.
PIPELINE_NAME = "HIRID"

MESSY_CFG = files(__package_name__).joinpath("configs/messy.yaml")

__all__ = ["MESSY_CFG", "PIPELINE_NAME", "__package_name__", "__version__"]
