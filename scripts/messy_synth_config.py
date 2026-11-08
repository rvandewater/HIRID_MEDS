"""Write a copy of the MESSY config that MESSY-synth can read, for the synthetic-ETL workflow.

MESSY-synth has no release for MEDS-Extract 0.8 yet: it requires MEDS-Extract <0.8 and loads the
config with 0.7. So the copy
  - spells `_table.joins` as 0.7's `_table.join` (every table here joins a single target, which
    both spellings express the same way);
  - keeps only the `etl:` keys 0.7 knows, dropping the extension stages (`post_stages`) and the
    dataset.json fields of 0.8.1 (`license`, `description_uri`);
  - drops the `_metadata` blocks of literal-coded events, which 0.7 rejects (0.8.1 accepts them).
None of this changes the raw data MESSY-synth generates: it generates from the tables, columns and
joins the events read. The extraction then runs on the shipped config.

Usage: python scripts/messy_synth_config.py SRC DST
"""

import sys

import yaml

# The `etl:` keys of MEDS-Extract 0.7, as its error message lists them.
ETL_KEYS_0_7 = {
    "dataset_name",
    "description_separator",
    "do_dedup_text_and_numeric",
    "encoding",
    "external_splits_json_fp",
    "n_subjects_per_shard",
    "raw_dataset_version",
    "split_fracs",
}


def main(src: str, dst: str) -> None:
    with open(src) as f:
        spec = yaml.safe_load(f)
    spec["etl"] = {k: v for k, v in spec.get("etl", {}).items() if k in ETL_KEYS_0_7}
    for name, table in spec.items():
        if name in ("sources", "etl", "_defaults") or not isinstance(table, dict):
            continue
        if "joins" in table.get("_table", {}):
            table["_table"]["join"] = table["_table"].pop("joins")
        for event_name, event in table.items():
            if event_name != "_table" and "$" not in str(event["code"]):
                event.pop("_metadata", None)
    with open(dst, "w") as f:
        yaml.safe_dump(spec, f, sort_keys=False, width=1000)


if __name__ == "__main__":
    main(*sys.argv[1:])
