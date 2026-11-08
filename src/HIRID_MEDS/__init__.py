"""HiRID MEDS ETL -- a pure-config MEDS-Extract pipeline.

The entire ETL is `configs/messy.yaml`, registered under the `MEDS_extract.pipelines` entry-point
group, and `sumeds_rules.yaml` is registered under suMEDS's `sumeds.rules` group with the same name.
The config declares suMEDS's `sumeds_enrich` stage after the pipeline, so the ETL and its suMEDS
enrichment run as:

    meds-extract-run spec=HIRID output_dir=$OUTPUT_DIR

and `skip_stages=[sumeds_enrich]` runs the ETL alone.
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
