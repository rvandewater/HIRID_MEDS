"""The packaged suMEDS rules file loads in suMEDS and matches the codes this ETL writes.

suMEDS (https://github.com/rvandewater/SumMEDS) reads `src/HIRID_MEDS/sumeds_rules.yaml` to add
LOINC parents to `metadata/codes.parquet`. Its strict loader (`sumeds.load_rules`) validates the
file, and the crosswalk keys come from the rule objects its enrichment uses, so these checks only
state which key each HiRID code is looked up by. The crosswalk table itself is not fetched. The
example codes come from evaluating the code templates of `configs/messy.yaml`, so a change to a
code layout that breaks a rule fails here. The example values are public HiRID variable names (the
HiRID variable sheet and HIRID-ICU-Benchmark `varref.tsv`, MIT) or made up.
"""

from importlib.resources import files
from pathlib import Path

import polars as pl
import yaml
from dftly import Parser
from sumeds import load_rules
from sumeds.ontology import CrosswalkRule

RULES = files("HIRID_MEDS") / "sumeds_rules.yaml"
CONFIG_FP = Path(__file__).parents[1] / "src" / "HIRID_MEDS" / "configs" / "messy.yaml"


def _code(table: str, event: str, rows: dict, schema: dict | None = None) -> list[str]:
    """Evaluate the code template of ``table``/``event`` in the config on ``rows``."""
    expr = yaml.safe_load(CONFIG_FP.read_text())[table][event]["code"]
    df = pl.DataFrame(rows, schema_overrides=schema)
    return df.select(Parser()(expr).polars_expr).to_series().to_list()


def _crosswalk(rule_id: str) -> CrosswalkRule:
    return next(r for r in load_rules(Path(str(RULES))).crosswalk if r.id == rule_id)


def test_rules_load_in_sumeds():
    """The strict suMEDS loader accepts the file; it holds only the two crosswalk rules."""
    rules = load_rules(Path(str(RULES)))
    assert not rules.ontology and not rules.hierarchy
    assert {rule.id for rule in rules.crosswalk} == {
        "hirid-observation-loinc-by-name",
        "hirid-observation-loinc-by-id",
    }


def test_rules_are_package_data():
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert '"sumeds_rules.yaml"' in pyproject


def test_observation_rules_match_the_config_layout():
    names, ids = ["Heart rate", "Sodium [Moles/volume] in Blood"], [200, 20000400]
    codes = _code(
        "raw_stage/observation_tables/parquet",
        "observation",
        {
            "variable_name": [*names, None],
            "variableid": [*ids, 300],
            "unit": ["/min", "mmol/l", None],
        },
        {"variableid": pl.Int32},
    )
    assert codes == [
        "OBSERVATION//Heart rate///min",
        "OBSERVATION//Sodium [Moles/volume] in Blood//mmol/l",
        "OBSERVATION//300//UNK",
    ]
    by_name = _crosswalk("hirid-observation-loinc-by-name")
    by_id = _crosswalk("hirid-observation-loinc-by-id")
    # Named codes: the key is the variable name, even when the unit starts with `/`.
    for name, code in zip(names, codes, strict=False):
        assert by_name.key(code) == name
        assert by_id.key(code) is None
    # Fallback codes of ids without a reference entry: the key is the id.
    assert by_id.key(codes[2]) == "300"


def test_rules_do_not_match_other_codes():
    other = _code(
        "raw_stage/pharma_records/parquet",
        "medication",
        {"variable_name": ["Drug A", None], "pharmaid": [1, 2]},
    )
    other += ["SEX//M", "MEDS_BIRTH", "HOSPITAL_ADMISSION"]
    for rule in load_rules(Path(str(RULES))).crosswalk:
        assert all(rule.key(code) is None for code in other), rule.id
