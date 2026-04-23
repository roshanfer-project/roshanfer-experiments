# Exec - Experiment Execution and Plotting

Framework for running experiments and generating plots, designed for CloudLab environments.

## Architecture

The execution framework orchestrates experiments across a set of remote hosts ("CloudLab nodes").
It follows a **Tune -> Deploy -> Run -> Collect** cycle.

1.  **Partition**: Hosts are split into **Generators** and **Deployment** nodes.
2.  **Tune**: System-specific tuners find optimal parameters (e.g., resource limits).
3.  **Deploy**: The system is deployed ONCE per benchmark/system.
4.  **Run**: Workload generators run remotely on generator nodes.
5.  **Collect**: Logs and metrics are pulled to the local machine.

## Prerequisites

1.  **Submodules**: From repo root, run `git submodule update --init benchmarks rwg` (or clone with `--recurse-submodules`). Provisioning/K8s scripts live under `benchmarks/`.
2.  **Hosts File**: Create `hosts.txt` with a list of SSH-accessible hosts (one per line).
    ```text
    user@node1.cloudlab.us
    user@node2.cloudlab.us
    ...
    ```
3.  **Provisioning**: Ensure `benchmarks/provisioning/provision.sh` exists and is idempotent.
4.  **direnv** (for kubeconfig isolation): Install direnv and enable the repo's `.envrc`:
    ```bash
    sudo apt install direnv
    echo 'eval "$(direnv hook zsh)"' >> ~/.zshrc   # or ~/.bashrc
    source ~/.zshrc
    cd /path/to/this/repo && direnv allow
    ```
    The `.envrc` sets `KUBECONFIG` to `benchmarks/k8s/kubeconfig` so each clone/worktree targets its own cluster. `run_tests.sh` will refuse to start if direnv is not active.

## Running Experiments

Run experiments defined in a JSON file:

```bash
python -m exec.executor \
  --experiments-file configs/tests/chain-2/experiments.json \
  --config configs/tests/chain-2/config.json
```

### Experiment spec (`experiments.json`)

Every entry under **`experiments`** must include **`load_generator`**:

- **`two_step_sweep`** — sweeps the second-phase rate; one **RunUnit** (one RWG process) per sweep value. Fields: **`base_duration_sec`**, **`load_duration_sec`**, **`base_rps`**, **`sweep`** `{start,end,step}` (same inclusive end semantics as `range(start, end + 1, step)` in the executor).
- **`piecewise`** — one **RunUnit**, one RWG run with **`phases`**: `[{ "rps", "duration_sec" }, ...]` (e.g. base → spike → base).

Top-level **`loads`**, **`base_rate`**, and **`duration_sec`** are not used anymore for load shape. Other fields (**`type`**, **`apis`**, **`system`**, **`repeat`**, **`fault_tolerance`** / **`fault-tolerance`**, **`deploy_env`**, …) are unchanged.

The remote **`run.sh`** / **`run-plain.sh`** wrappers expect **`RWG_RATES`** and **`RWG_DURATIONS`** (comma-separated lists, equal length) plus arguments **`PROTOCOL API OUTPUT_DIR`** (optional **`--ignore-errors`** on plain). The executor sets the env vars from each unit’s phase lists.

**Options:**
- `--hosts-file PATH`: Override `hosts_file` from config (first `num_generators` lines are generators, rest are K8s nodes).
- `--num-generators N`: Override `num_generators` from config.
- `--only-names "exp1,exp2"`: Run specific experiments (use derived or explicit names).
- `--only-types "type1"`: Run specific types.
- `--name-contains "substring"`: Filter by name substring.

### Derived experiment names and optional `tag`

If an entry has no top-level **`name`**, the executor assigns one (same logic as `exec.merged_plot_runner.load_experiment_configs` — shared in `exec/experiment_naming.py`):

- **Format:** `{type}-{bench_basename}-{api_slug}[-{tag}]-{system}`  
  - **`bench_basename`**: last path segment of `config.json`’s `bench` (or per-experiment `bench` when set).  
  - **`api_slug`**: API ids in JSON order, joined with `-` (e.g. `f1-g1`); if there are no APIs, `none`.  
  - **`tag`**: optional string; if set, it is **slugified** and inserted before `system` to disambiguate colliding entries (e.g. different load or fault params). **Ignored** when `name` is set.  
- **Explicit `name`**: If `name` is set, it is used as-is; `tag` is ignored.  
- **Duplicate bases:** if two rows still get the same base string, a numeric suffix is added: `...-1`, `...-2`, …

Merged figure configs (`merged.yaml` **`include`** keys) must use these final names. Use `python scripts/validate_merged_includes.py` to cross-check a tests tree.

**Examples:**
```bash
--only-names "latency-vs-throughput-one-service-f1-plain,latency-vs-throughput-one-service-f1-sidecar"
--name-contains "sidecar"
--only-types "latency-vs-throughput" --name-contains "plain"
```

### Profiles (`run_tests.sh`)

For a test dir `configs/tests/<name>/`, `./run_tests.sh --profile P` (with `P` not `default`) uses:

- **`experiments-P.json`** if that file exists, else **`experiments.json`**
- **`merged-P.yaml`** if that file exists, else **`merged.yaml`**

`configs/hotel` uses **`hotel_experiments.json`** / **`hotel_experiments-P.json`**; `configs/social` uses **`social_experiments.json`**, with **`merged_social.yaml`** as fallback when **`merged.yaml`** is absent.

After a run, `python -m exec.merge_plot_pdfs --profile P plots/` should use the same profile so merged-figure headers match the merged spec (or set **`MERGE_PLOTS_PROFILE`**).

## CloudLab manifest → hosts

Download the experiment **manifest** (XML) from the CloudLab portal, then:

```bash
python -m exec.cloudlab_hosts --manifest ./manifest.xml -o ./cloudlab_hosts.txt
```

Uses `<login hostname="..." username="..."/>`. If several usernames share the same host (shared project), pass **`--ssh-user YOUR_USERNAME`** so the correct `<login>` is chosen. With a single user per host, `--ssh-user` is optional. Host order follows **`<node>` elements in the manifest** (CloudLab node0, node1, …). If there are no `<node>` wrappers with nested logins, falls back to a flat `<login>` scan sorted by hostname. Re-download the manifest if nodes change after swap-in.

## Batch: `run_tests.sh`

From repo root, run all `configs/tests/*` benchmarks (and optionally hotel/social):

```bash
./run_tests.sh
./run_tests.sh --profile fault
./run_tests.sh --also-hotel-social
./run_tests.sh --remote --cloudlab-manifest ~/manifest.xml --num-generators 3 --cloudlab-ssh-user ubuntu
```

`--remote` writes `exp_runs_test/<timestamp>/cloudlab_hosts.txt` and passes `--hosts-file` / `--num-generators` to each executor run.

`--remote-clean` (with the same `--cloudlab-manifest` and `--num-generators`) removes `~/.roshanfer_provisioned` on **every** listed host, then runs `benchmarks/k8s/delete.sh` using **deployment** hosts only (all lines after the first `num_generators`). Use alone to reset infra and exit, or add `--remote` to clean and then run tests.

### Local vs Remote Host Resolution

Hosts are always read from a plain-text file (one `[user@]host` per line, `#` comments ignored) via `InfraBuilder`. The first `num_generators` lines become generator nodes; the rest become deployment (K8s) nodes. The two modes differ only in *which* file is used:

- **Local mode** — each test's `config.json` has a `hosts_file` field pointing to a static per-test file, e.g. `configs/tests/one-service/hosts.txt`. There is no auto-discovery; you edit that file to match your local setup.
- **Remote mode** (`--remote`) — `run_tests.sh` parses the CloudLab `manifest.xml` into a generated `cloudlab_hosts.txt` (via `exec.cloudlab_hosts`) and passes it with `--hosts-file`, overriding whatever `hosts_file` the config specifies.

`benchmarks/k8s/hosts.txt` and `benchmarks/provisioning/hosts.txt` are only used as defaults when running those shell scripts *manually*; the executor always overrides them by setting the `HOSTS_FILE` env var.

## Tuning

The orchestrator automatically looks for a tuner script in `tuner/<system>_tuner.py` or `exec/<system>_tuner.py`.
If found, it runs the tuner before deploying the system.
Results are saved to `exp_runs/exp-<id>/tuning/<system>.json`.

To create a new tuner, copy `exec/tuner_template.py` to `exec/<system>_tuner.py` and implement your optimization logic.

### Rajomon tuner (`exec/rajomon_tuner.py`)

The executor calls `optimize_system` with the same `config.json` path as the experiment run. Optional top-level **`tuner`** object:

- **`tune_api`** (string): entry API used for every Bayesian trial (one API only). Required for benchmarks whose `bench` path is not in the legacy map (`hotel` → `search-hotel`, `social` → `compose-post`); otherwise tuning raises a clear error.
- **Optional knobs** (integers; omit to keep defaults): `initial_point`, `n_iter`, `maximum_goodput`, `tuner_base`, `tuner_rate`, `trial_duration_sec`, `trial_warmup_sec`, `trial_cooldown_sec`, `trial_collector_freq`.

Example:

```json
"tuner": {
  "tune_api": "f1",
  "tuner_base": 200,
  "tuner_rate": 2000,
  "trial_duration_sec": 15
}
```

Standalone: `python -m exec.rajomon_tuner --config ... --system rajomon --bench tests/my-bench --output tuning.json`.

## Config (`config.json`)

Ensure your config includes:
```json
{
  "hosts_file": "hosts.txt",
  "provisioning_script": "benchmarks/provisioning/provision.sh",
  "experiment_index": "001",
  "output_base_dir": "experiment_runs"
}
```

## Generating Merged Plots

Generate combined plots (e.g., comparing multiple experiments) defined in a YAML file:

```bash
python -m exec.merged_plot_runner \
  --merged-config configs/chain1/merged.yaml \
  --experiments-file configs/chain1/experimnts.json \
  --config configs/chain1/config.json \
  --experiments-root exp_runs \
  --output-dir merged_plots \
  --experiment-index 001
```

## Directory Structure

```
experiment_runs/
└── exp-001/
    ├── run_summary.jsonl
    ├── tuning/
    │   └── <system>.json
    └── experiment_name/
        └── unit_name/
            └── repeat_000/
                ├── output/            # CSV results and parsed JSON
                │   ├── overall-{api}.json
                │   └── realtime-{api}.csv
                ├── metrics/           # JSON copies for Plotting
                ├── raw/               # Service Logs & Raw Output
                └── run_details.json
```

## Parallel Dev/Test with Git Worktrees

To work on two clusters simultaneously (e.g., dev and test), use git worktrees. Each worktree is a separate checkout with its own `benchmarks/k8s/kubeconfig`, and the committed `.envrc` automatically points `KUBECONFIG` to the right one via `$PWD`.

```bash
# Create a worktree for dev on a new branch
cd ~/files/roshanfer-experments
git worktree add ../local-experiments dev

# Allow direnv in the new worktree
cd ../local-experiments && direnv allow
```

After running `benchmarks/k8s/create.sh` in each worktree, each gets its own kubeconfig. Open two terminals — `cd` into each directory and kubectl talks to the corresponding cluster automatically.
