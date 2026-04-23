# Test benchmarks (`configs/tests/<name>/`)

Each benchmark directory typically has:

- `config.json` — `bench`, `hosts_file`, `experiment_index`, …
- `experiments.json` — experiment list (see `exec/README.md` for derived names and optional `tag`)
- `merged.yaml` — merged figure definitions; `figures.*.include` keys must match experiment **names** (derived or explicit)
- Optional **`experiments-<profile>.json`** / **`merged-<profile>.yaml`** — selected by `./run_tests.sh --profile <profile>` when those files exist

`run_tests.sh` uses the profile for **both** the experiment file and the merged file so figures stay consistent with what ran.
