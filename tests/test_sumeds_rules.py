"""The packaged suMEDS rules file is well formed and matches the codes this ETL writes.

suMEDS (https://github.com/rvandewater/SumMEDS) reads `src/HIRID_MEDS/sumeds_rules.yaml` to add
LOINC parents to `metadata/codes.parquet`. These checks mirror its strict `sumeds-rules/0.1` loader
without depending on it, and build the example codes by evaluating the code templates of
`configs/messy.yaml`, so a change to a code layout that breaks a rule fails here. The example
values are public HiRID variable names (the HiRID variable sheet and HIRID-ICU-Benchmark
`varref.tsv`, MIT) or made up.
"""

import re
from importlib.resources import files
from pathlib import Path

import polars as pl
import pytest
import yaml
from dftly import Parser

RULES = files("HIRID_MEDS") / "sumeds_rules.yaml"
CONFIG_FP = Path(__file__).parents[1] / "src" / "HIRID_MEDS" / "configs" / "messy.yaml"

TOP_KEYS = {"schema", "ontology", "crosswalk", "hierarchy"}
ONTOLOGY_KEYS = {"id", "pattern", "vocabulary", "normalize", "validate"}
CROSSWALK_KEYS = {"id", "pattern", "table", "key_column", "code_column", "vocabulary"}
CROSSWALK_KEYS |= {"where", "normalize", "validate"}
HIERARCHY_KEYS = {"id", "pattern", "group", "mode", "separator", "ignore"}
HIERARCHY_KEYS |= {"nearest_only", "from_ontology", "rules"}


def _rules() -> dict:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def _code(table: str, event: str, rows: dict, schema: dict | None = None) -> list[str]:
    """Evaluate the code template of ``table``/``event`` in the config on ``rows``."""
    expr = yaml.safe_load(CONFIG_FP.read_text())[table][event]["code"]
    df = pl.DataFrame(rows, schema_overrides=schema)
    return df.select(Parser()(expr).polars_expr).to_series().to_list()


def _match(rule_id: str, code: str) -> re.Match | None:
    rules = _rules()
    rule = next(
        r
        for section in ("ontology", "crosswalk", "hierarchy")
        for r in rules.get(section) or []
        if r["id"] == rule_id
    )
    return re.search(rule["pattern"], code)


def test_rules_file_is_well_formed():
    rules = _rules()
    assert set(rules) <= TOP_KEYS
    assert rules["schema"] == "sumeds-rules/0.1"
    ids = []
    for rule in rules.get("ontology") or []:
        assert set(rule) <= ONTOLOGY_KEYS
        assert {"id", "pattern", "vocabulary"} <= set(rule)
        assert "code" in re.compile(rule["pattern"]).groupindex
        ids.append(rule["id"])
    for rule in rules.get("crosswalk") or []:
        assert set(rule) <= CROSSWALK_KEYS
        assert {"id", "pattern", "table", "key_column", "code_column"} <= set(rule)
        assert "key" in re.compile(rule["pattern"]).groupindex
        assert re.fullmatch(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*", rule["table"])
        ids.append(rule["id"])
    for rule in (rules.get("ontology") or []) + (rules.get("crosswalk") or []):
        assert "/" not in rule["vocabulary"]
        assert rule.get("validate", "pack") in {"pack", "athena", "none"}
        for step in rule.get("normalize") or []:
            assert set(step) == {"pattern", "replace"}
            re.compile(step["pattern"])
    for rule in rules.get("hierarchy") or []:
        assert set(rule) <= HIERARCHY_KEYS
        if not rule.get("from_ontology"):
            assert rule["group"] in re.compile(rule["pattern"]).groupindex
        ids.append(rule["id"])
    assert len(ids) == len(set(ids)), "rule ids must be unique"


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
    # Named codes: the key is the variable name, even when the unit starts with `/`.
    for name, code in zip(names, codes, strict=False):
        assert _match("hirid-observation-loinc-by-name", code)["key"] == name
        assert _match("hirid-observation-loinc-by-id", code) is None
    # Fallback codes of ids without a reference entry: the key is the id.
    assert _match("hirid-observation-loinc-by-id", codes[2])["key"] == "300"


def test_rules_do_not_match_other_codes():
    other = _code(
        "raw_stage/pharma_records/parquet",
        "medication",
        {"variable_name": ["Drug A", None], "pharmaid": [1, 2]},
    )
    other += ["SEX//M", "MEDS_BIRTH", "HOSPITAL_ADMISSION"]
    for rule in _rules()["crosswalk"]:
        assert not any(re.search(rule["pattern"], code) for code in other), rule["id"]


def test_rules_load_in_sumeds():
    """When suMEDS with rules support is installed, its own strict loader accepts the file."""
    ontology = pytest.importorskip("sumeds.ontology")
    rules = ontology.load_rules(Path(str(RULES)))
    assert {rule.id for rule in rules.crosswalk} == {
        "hirid-observation-loinc-by-name",
        "hirid-observation-loinc-by-id",
    }
