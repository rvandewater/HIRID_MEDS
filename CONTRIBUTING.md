# Contributing

This repository is a **MEDS-Extract 0.7 ETL**. All of it is one declarative file; there is no
Python to change.

```bash
pip install HIRID-MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
export POLARS_ENGINE_AFFINITY=streaming

meds-extract-run spec=HIRID output_dir=$ROOT_OUTPUT_DIR
```

## What the 0.7 layout looks like

Under MEDS-Transforms 0.2.x an ETL was a Python package: `pre_MEDS.py` reshaped the raw data,
`dataset.yaml` described the download, `ETL.yaml` listed the pipeline stages, and
`event_configs.yaml` described the events. **0.7 replaces all four with a single MESSY file**
(MEDS-Extract Specification Syntax YAML):

| Used to be                     | Now                                                                       |
| ------------------------------ | ------------------------------------------------------------------------- |
| `dataset.yaml` + `download.py` | the `sources:` block                                                      |
| `configs/ETL.yaml`             | nothing — the stage sequence is canonical and not configurable            |
| `configs/event_configs.yaml`   | the event tables, written in [dftly](https://github.com/mmcdermott/dftly) |
| `pre_MEDS.py`                  | `_table.join` / `_table.cols`                                             |

The file lives at [`src/HIRID_MEDS/configs/messy.yaml`](src/HIRID_MEDS/configs/messy.yaml) and is
registered under the `MEDS_extract.pipelines` entry-point group in `pyproject.toml`:

```toml
[project.entry-points."MEDS_extract.pipelines"]
HIRID = "HIRID_MEDS.configs:messy.yaml"
```

That value is **parsed, not imported** — it names a package and a data file inside it, which is
why it does not look like a normal `module:attribute` target.

## Editing the config

Three things bite newcomers, in rough order of how often:

1. **`$` is load-bearing.** `$value` reads the `value` column; a bare `value` is the string
    literal `"value"`. This fails silently — wrong data, no error.
2. **A null code component drops the row.** `f"LAB//{$item}"` discards every row with a null
    `item`. Coalesce with `?? 'UNK'` when the row must be kept. 0.6.x rendered nulls as `UNK`
    automatically; 0.7 does not.
3. **There is no null test.** dftly has no `is_null`. The working idiom is `X if $c == $c`, which
    is true for any present value (including `0`, `inf` and `NaN`) and null for a missing one.
    A bare `null` parses as the *string* `"null"`. Tracked in
    [dftly#113](https://github.com/mmcdermott/dftly/issues/113).

Also worth knowing: a table may declare **exactly one** `_table.join`, `_table.cols` entries are
evaluated in order so later ones may reference earlier ones, and adding a Duration to a Datetime
must be written as `anchor - (-$offset)::minutes` because `+` does not lower correctly.

Specific to HiRID:

- **Join keys must have identical dtypes on both sides**, or polars panics. The raw parquet ids
    are `Int32` and the CSV reference tables are inferred as `Int64`, so every join uses a cast,
    derived key (`_ref_id: $variableid::int64`, `_obs_patientid: $patientid::int32`).
- **The variable reference is joined on (`ID`, `Source Table`)**, not `ID` alone: one id is both
    an observation and a drug. Keep the id first — MESSY-synth keeps the first right-hand key
    unique per row, and the `Source Table` literal has to repeat.
- **Columns with spaces** (`Variable Name`, `Unit`) cannot be written as `$name`; alias them once in
    `_table.cols` with the explicit form `{column: Variable Name}`.

Why each choice was made, and what it changes in the output, is recorded in
[`DESIGN_DECISIONS.md`](DESIGN_DECISIONS.md).

## No Python

Everything the old `pre_MEDS.py` did is expressible in MESSY: archive extraction is the download
layer's `unarchive: auto`, the pseudo date of birth is a `_table.cols` expression, and the
last-observation discharge/death time is an aggregated join (`cols: {datetime: max}`). Users call
`meds-extract-run spec=HIRID` directly. Keep it that way unless a transformation genuinely cannot
be written in MESSY (for example, reducing a table over its own rows, which MESSY rejects).

## Testing

```bash
uv sync
uv run pytest tests/
uv run pre-commit run --all-files
```

There is no demo release for HiRID, and the credentialed download cannot run in CI. CI instead
runs two checks that need no real data:

- [`tests/test_etl.py`](tests/test_etl.py) runs the whole ETL over a small hand-written fixture
    with the release's layout and dtypes, and checks specific values (the shared-id join, dtype
    alignment, fallback codes, text values, patient events).
- The [synthetic ETL workflow](.github/workflows/synthetic-etl.yaml) generates raw files straight
    from the config with [MESSY-synth](https://github.com/rvandewater/MESSY_synth) and fails if
    any event yields no rows.

Changes that affect the output should also be validated by running the ETL over the real release
and comparing cohort statistics before and after. HiRID is credentialed data: compare aggregate
statistics only, and do not paste rows into issues, PRs or logs.

## Changing what the ETL emits

Any change to codes, timestamps or which rows are emitted is a change to everybody's downstream
cohort. State the measured effect in the PR — row counts, subject counts, code-vocabulary size
before and after — rather than only describing the intent. Several defects in this repository were
found precisely because those numbers were compared
([#10](https://github.com/rvandewater/HIRID_MEDS/issues/10)).

# Development

## Environment

The project uses [uv](https://docs.astral.sh/uv/) for its environment, lockfile,
build, and commands.

```bash
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
uv run pytest
```

A plain `pre-commit run` checks only staged files, so hooks legitimately skip
when nothing matching is staged. Use `--all-files` for a full repository check.

Build the distribution with:

```bash
uv build
```

## Release

Bump and commit the project version before creating the matching tag:

```bash
uv version --bump patch
uv run pytest
uv build --clear
git add pyproject.toml uv.lock
git commit -m "Release $(uv version --short)"
VERSION=$(uv version --short)
git tag -a "$VERSION" -m "Release $VERSION"
git push origin main "$VERSION"
```

The tag workflow rejects mismatched versions, then publishes the distributions
to PyPI and creates the GitHub release.

```bash
uv sync
uv version --bump patch   # 0.0.4 → 0.0.5
uv version --bump minor   # 0.0.4 → 0.1.0
uv version --bump major   # 0.0.4 → 1.0.0
```
