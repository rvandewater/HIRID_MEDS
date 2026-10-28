# HiRID → MEDS: design decisions

This file records the modelling choices made in
[`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml) for the MEDS-Extract 0.7
migration ([PR #2](https://github.com/rvandewater/HIRID_MEDS/pull/2)), why they were made, and
which ones are still open. Open questions link to their GitHub issue.

All numbers are from HiRID **1.1.1** (`reference_data.tar.gz`,
`raw_stage/observation_tables_parquet.tar.gz`, `raw_stage/pharma_records_parquet.tar.gz`, checksums
matching the release `SHA256SUMS`), processed with MEDS-Extract 0.7.1 / dftly 0.7.0 / polars
1.44.2. They were produced by aggregate-only profiling scripts; no row-level data is reproduced
here.

## Inputs

| File                                          | Used for                                | Size                                                |
| --------------------------------------------- | --------------------------------------- | --------------------------------------------------- |
| `reference_data/general_table.csv`            | admission, sex, birth, death, discharge | 33,905 patients                                     |
| `reference_data/hirid_variable_reference.csv` | variable names and units                | 712 rows (147 Observation, 565 Pharma)              |
| `raw_stage/observation_tables` (parquet)      | `OBSERVATION//…` events                 | 776,921,131 rows, 33,904 patients, 148 variable ids |
| `raw_stage/pharma_records` (parquet)          | `MEDICATION//…` events                  | 16,270,399 rows, 33,815 patients, 533 drug ids      |

**All three archives are required.** The two `raw_stage` parquet archives alone are not enough:
without `reference_data.tar.gz` there are no admission/birth/death/discharge/sex events and no
variable names. The CSV variants of the raw tables and the `merged_stage` / `imputed_stage`
derivatives are not used. The `sources.include` list stages exactly these three files.

`reference_data.tar.gz` unpacks flat (`general_table.csv`, `hirid_variable_reference.csv`, … at the
archive root), so with `unarchive: auto` the tables land at the input root and are addressed as
`general_table` / `hirid_variable_reference` (not `reference_data/…`).

## Impact on extracts from releases 0.0.1–0.0.4

Every released version (`pre_MEDS.py`) inner-joins the variable reference on `ID` alone (fixed by
D1). Applying those join semantics to the raw HiRID 1.1.1 tables gives:

| Effect     | Table       |          Raw rows | Ids | Result in the old output                                           |
| ---------- | ----------- | ----------------: | --: | ------------------------------------------------------------------ |
| duplicated | observation | 23,953,744 (3.1%) |   1 | correct event **plus** `OBSERVATION//<drug name>//UNK`             |
| duplicated | pharma      |                12 |   1 | extra `MEDICATION//<observation name>`                             |
| mislabeled | observation |           112,977 |   4 | `OBSERVATION//<drug name>//UNK`                                    |
| mislabeled | pharma      |            10,074 |   2 | `MEDICATION//<observation name>`, observation unit as `text_value` |
| dropped    | observation |            79,105 |   1 | no event (inner join)                                              |

MEDS-Transforms 0.2.4 fills null code components with `UNK` and only null-filters a leading column
component, so the extra rows did become events. In addition, correctly matched medication events
have no unit (D4), and most observation events carry `text_value = ""` (D5). Anyone with an older
extract should re-run; tracked in
[#10](https://github.com/rvandewater/HIRID_MEDS/issues/10).

## Decisions made in this PR

### D1. Variable-reference join is keyed on (`ID`, `Source Table`) — *bug fix*

`hirid_variable_reference.ID` is unique only within a `Source Table`: one id is listed as both an
`Observation` and a `Pharma` variable. The previous config (and the pre-0.7 `pre_MEDS.py`) joined
on `ID` alone, which fans every row with that id out into two events, one carrying the wrong
table's name. The join now adds a literal `Source Table` value on the left
(`_ref_table: "'Observation'"` / `"'Pharma'"`) and joins on both columns. The id is listed
first because MESSY-synth keeps the first right-hand key unique per row, and the literal key has to
repeat; the order does not change the ETL output (verified identical on a 5-part real-data sample).

### D2. Join keys are cast to matching dtypes — *bug fix*

Polars panics (`cannot get ref Int32 from Int64`) when the two sides of a join have different
integer types, and MEDS-Extract 0.7.1 does not align them. In HiRID the raw parquet ids
(`patientid`, `variableid`, `pharmaid`) are `Int32`, while the CSV-derived reference tables are
inferred as `Int64`. The config therefore joins on derived, cast keys (`_ref_id: $variableid::int64`,
`_obs_patientid: $patientid::int32`). This ties the config to the release's dtypes. MESSY-synth
generates matching dtypes for these derived keys as of
[mmcdermott/MESSY_synth#12](https://github.com/mmcdermott/MESSY_synth/pull/12), which the synthetic
CI workflow pins ([#7](https://github.com/rvandewater/HIRID_MEDS/issues/7)).

### D3. Unreferenced ids fall back to the raw id in the code

Some ids in the raw tables have no reference entry under their own `Source Table`:

| Table       | Ids without own-table entry | …of which listed under the other table | Raw rows affected |
| ----------- | --------------------------- | -------------------------------------- | ----------------- |
| observation | 5                           | 4                                      | 192,082           |
| pharma      | 2                           | 2                                      | 10,074            |

Their codes become `OBSERVATION//<variableid>//UNK` / `MEDICATION//<pharmaid>`, so they stay
distinct. Previously they either took the *other* table's name (via the id-only join) or were
dropped (inner join, when the id was in neither). Whether to borrow the other table's name is open:
[#3](https://github.com/rvandewater/HIRID_MEDS/issues/3).

### D4. Medication unit comes from `pharma_records.doseunit`

The reference `Unit` column is null for **all 565** Pharma rows, so the old
`text_value: Unit` was always null for medications. The raw `doseunit` column is never null and
has exactly one value per `pharmaid`, so it is used instead (still as `text_value`, preserving the
old event shape).

### D5. Empty `stringvalue` becomes a null `text_value`

`stringvalue` is an empty string — not null — on almost every numeric observation (98% of rows in a
5-shard sample). Passing it through would give nearly every observation `text_value = ""`. Only
non-empty strings are kept (`text_value: '$stringvalue if $stringvalue != ""'`).

### D6. Admission time format is `%Y-%m-%d %H:%M:%S`

dftly needs an explicit format where the old code used polars' inference. All 33,905
`admissiontime` values match it (0 parse failures). The cast stays lenient (`::?`), so a future
release with a different format drops rows with a logged WARNING rather than failing.

### D7. Raw timestamps are used as-is

`datetime` / `givenat` are native `Datetime[ns]` in the parquet release, so no format cast is
applied (the PR's original `::?"%Y-%m-%d %H:%M:%S"` on these columns was removed). MEDS stores
microseconds; no raw timestamp has a sub-microsecond component, so nothing is lost.

### D8. Patient-level events (kept from the previous ETL)

- `HOSPITAL_ADMISSION` at `admissiontime`; `SEX//{sex}` as a static event.
- `MEDS_BIRTH` at `admissiontime − age` years. HiRID `age` is binned (15 distinct values, 20–90),
    so this is a pseudo date of birth with up to ~5 years of error.
- `HOSPITAL_DISCHARGE` at the patient's **last observation** (`max(datetime)` over
    `observation_tables`). One `general_table` patient has no observations and so no discharge.
- `MEDS_DEATH` at the same time, iff `discharge_status != "alive"`. 2,062 patients are `dead`,
    31,604 `alive`, and 239 have a null status, which is treated as *not* dead.

### D9. Joins are left joins (behaviour change)

MEDS-Extract 0.7 joins are always left joins. The old pipeline inner-joined every event table to
the patient table and to the variable reference, silently dropping rows for unknown patients or
unknown variables. Now every raw row is kept: unknown variables get the fallback code (D3).
Every observation/pharma patient is present in `general_table`, so no rows depend on this for
patients.

### D10. Byte-identical events are deduplicated (MEDS-Extract behaviour)

MEDS-Extract drops events that are identical in every output column. In HiRID this removes:

- exact duplicate raw rows (same patient, time, variable, value, string);
- readings from *different* variable ids that share one code (see the open question on code
    granularity) and coincide in time and value;
- a handful of pairs that differ only in a null vs non-null `stringvalue` (507 in the full run; the
    root cause inside MEDS-Extract was not pinned down).

|                                            |        Observation |         Pharma |
| ------------------------------------------ | -----------------: | -------------: |
| raw rows                                   |        776,921,131 |     16,270,399 |
| exact raw duplicates removed               |  3,147,756 (0.41%) | 18,483 (0.11%) |
| merged because distinct ids share a code   | 15,121,467 (1.95%) |             31 |
| null-vs-present `stringvalue` pairs merged |                507 |              0 |
| **MEDS events**                            |    **758,651,401** | **16,251,885** |

The second row is the cost of the current code granularity: [#8](https://github.com/rvandewater/HIRID_MEDS/issues/8).

### D11. Null code components are filled with `UNK`

As before, a null `sex` or observation unit becomes `UNK` rather than dropping the row. Six
observation variables have no unit in the reference (dimensionless scores), so their codes end in
`//UNK`.

### D12. Polars streaming engine for full runs

Under the default in-memory engine, `split_and_shard_subjects` is killed for running out of memory on
a 24 GB machine: it applies each table's join before collecting subject ids, which materializes the
variable-reference join over all 777M observation rows. `POLARS_ENGINE_AFFINITY=streaming` avoids
this (full run ~13 min, ~9.5 GB peak RSS). The README sets it; the upstream fix is tracked in
[#9](https://github.com/rvandewater/HIRID_MEDS/issues/9).

## Open questions

Each has a GitHub issue with the options and the data behind them.

- **Code granularity** ([#8](https://github.com/rvandewater/HIRID_MEDS/issues/8)). 45 observation variable ids share a (`Variable Name`, `Unit`) pair with
    another id (16 shared pairs) and carry 330M of the 777M raw rows. Their readings get the same
    code, and coincident readings are deduplicated (D10). Should the code include the variable id?
- **Ids missing from their own `Source Table`** ([#3](https://github.com/rvandewater/HIRID_MEDS/issues/3), D3): fall back to the id, or borrow the other
    table's name?
- **Discharge and death times** ([#4](https://github.com/rvandewater/HIRID_MEDS/issues/4), D8): last observation only, or also the last medication? What
    should a null `discharge_status` mean?
- **Unused validity and status columns** ([#5](https://github.com/rvandewater/HIRID_MEDS/issues/5)). `observation_tables.status`/`type` and
    `pharma_records.recordstatus`/`typeid`/`infusionid` are ignored, as before. Some of these likely
    mark invalidated or corrected records.
- **`UNK` placeholders** ([#6](https://github.com/rvandewater/HIRID_MEDS/issues/6), D11): keep `UNK` for "no unit", or omit the component?
- **CI with synthetic data** ([#7](https://github.com/rvandewater/HIRID_MEDS/issues/7)). The synthetic workflow pins an unmerged MESSY_synth commit
    ([mmcdermott/MESSY_synth#12](https://github.com/mmcdermott/MESSY_synth/pull/12)); move the pin
    to a release once it lands. `tests/test_etl.py` checks specific values on a hand-written fixture.

## Validation

Full run over the complete release on a 10-core / 24 GB laptop, single worker,
`POLARS_ENGINE_AFFINITY=streaming`: 13 min wall time, 9.5 GB peak RSS, 4.8 GB of MEDS data in
28 train / 4 tuning / 4 held-out shards. All eight stages succeeded.

| Code                 |      Events | Subjects / notes                                                              |
| -------------------- | ----------: | ----------------------------------------------------------------------------- |
| `HOSPITAL_ADMISSION` |      33,905 | every `general_table` patient                                                 |
| `SEX//…`             |      33,905 | static                                                                        |
| `MEDS_BIRTH`         |      33,905 |                                                                               |
| `HOSPITAL_DISCHARGE` |      33,904 | one patient has no observations                                               |
| `MEDS_DEATH`         |       2,062 | equals the number of `dead` statuses                                          |
| `MEDICATION//…`      |  16,251,885 | 525 codes (2 raw-id fallbacks); all have `numeric_value` and `text_value`     |
| `OBSERVATION//…`     | 758,651,401 | 123 codes (5 raw-id fallbacks, 11 ending `//UNK`); 33,643 with a `text_value` |

Consistency checks (counts of subjects violating each):

| Check                                      |                                                                                              Violations |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------: |
| birth after admission                      |                                                                                                       0 |
| discharge before admission                 |                                                                                                       0 |
| death time ≠ discharge time (for the dead) |                                                                                                       0 |
| ≠ 1 admission or ≠ 1 sex event             |                                                                                                       0 |
| first observation before admission         |                                                      9,345 (all within 60 min; present in the raw data) |
| last medication after discharge            | 2 (discharge is defined on observations only, [#4](https://github.com/rvandewater/HIRID_MEDS/issues/4)) |

Dropped-row warnings in the run log are only the expected ones: `general_table/death` for patients
who are not dead, and one `general_table/discharge` for the patient without observations. No event
had rows dropped for an unparsable time.

`tests/test_etl.py` reproduces the shared-id, dtype, fallback-code, text-value and patient-event
behaviour on a small fixture and runs in CI.
