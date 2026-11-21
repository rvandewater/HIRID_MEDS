"""HiRID codes that end in a unit, tested with suMEDS's shared `UnitComponentContract`.

The contract finds every code of the spec whose last component is a unit and evaluates it with
dftly, as MEDS-Extract does: the component is the unit stripped, `UNK` where a row records none
or a blank one, and the `unit` metadata stays the raw column. `expected_unit_events` pins the
events it finds, so a new code that ends in a unit is a conscious change here.
`require_unit_in_code` (suMEDS 0.6.11) also checks that every unit a row records is in its code.
"""

from sumeds.testing.contracts import UnitComponentContract


class TestUnitComponents(UnitComponentContract):
    spec = "HIRID"
    expected_unit_events = (
        "raw_stage/pharma_records/parquet/medication",
        "raw_stage/observation_tables/parquet/observation",
    )
    require_unit_in_code = True
