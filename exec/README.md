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
2.  **Hosts File**: Create repo-root `hosts.txt` (from `hosts.txt.example`) with `user@host` lines.
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
  --experiments-file configs/chain1/experimnts.json \
  --config configs/chain1/config.json
```

**Options:**
- `--hosts-file PATH`: Override `hosts_file` from config (first `num_generators` lines are generators, rest are K8s nodes).
- `--num-generators N`: Override `num_generators` from config.
- `--only-names "exp1,exp2"`: Run specific experiments (use derived names).
- `--only-types "type1"`: Run specific types.
- `--name-contains "substring"`: Filter by name substring.

Experiment names are derived from `type`, `bench`, and `system`: `{type}-{bench}-{system}` (or `{type}-{bench}-{n}-{system}` for multi-API). Examples:
```bash
--only-names "latency-vs-throughput-one-service-plain,latency-vs-throughput-one-service-sidecar"
--name-contains "sidecar"
--only-types "latency-vs-throughput" --name-contains "plain"
```

## CloudLab manifest → hosts

On a cluster node (`CONTROL_ON_CLUSTER=1` in `config.env`), fetch the experiment **manifest** with:

```bash
./scripts/fetch_manifest.sh
```

That runs `geni-get manifest` and writes `./manifest.xml` (or `CLOUDLAB_MANIFEST`). If `CONTROL_ON_CLUSTER=0`, place the portal XML at that path yourself. Then:

```bash
python -m exec.cloudlab_hosts --manifest ./manifest.xml -o ./cloudlab_hosts.txt
```

Uses `<login hostname="..." username="..."/>`. If several usernames share the same host (shared project), pass **`--ssh-user YOUR_USERNAME`** so the correct `<login>` is chosen. With a single user per host, `--ssh-user` is optional. Host order follows **`<node>` elements in the manifest** (CloudLab node0, node1, …). The first node is the control machine and is dropped. If there are no `<node>` wrappers with nested logins, falls back to a flat `<login>` scan sorted by hostname. Re-fetch the manifest if nodes change after swap-in.

## Batch: `run_tests.sh`

From repo root, run all `configs/tests/*` benchmarks (and optionally hotel/social):

```bash
./run_tests.sh
./run_tests.sh --also-hotel-social
./run_tests.sh --remote --num-generators 3
```

`--remote` writes `exp_runs_test/<timestamp>/cloudlab_hosts.txt` and passes `--hosts-file` / `--num-generators` to each executor run. `CLOUDLAB_MANIFEST` and `CLOUDLAB_SSH_USER` come from `config.env` unless you pass `--cloudlab-manifest` / `--cloudlab-ssh-user`. `CLOUDLAB_SSH_USER` is required.

`--remote-clean` (with the same manifest and `--num-generators`) removes `~/.roshanfer_provisioned` on **every** listed host, then runs `benchmarks/k8s/delete.sh` (that script skips the first `NUM_GENERATORS` lines). Use alone to reset infra and exit, or add `--remote` to clean and then run tests.

### Local vs Remote Host Resolution

Hosts are always read from a plain-text file (one `user@host` per line, `#` comments ignored) via `InfraBuilder`. A line without `@` is an error. The first `num_generators` lines become generator nodes; the rest become deployment (K8s) nodes.

- **Local mode** — repo-root `hosts.txt` (copy `hosts.txt.example`). `REQUIRE_REMOTE=0` in `config.env`. `create.sh` / `delete.sh` read the same file and skip the first `NUM_GENERATORS` lines; `provision.sh` uses every line.
- **Remote mode** (`--remote`) — `run_tests.sh` parses the CloudLab `manifest.xml` into a generated `cloudlab_hosts.txt` (first node / control machine dropped) and passes it with `--hosts-file`. Root `hosts.txt` is not read. If the manifest file is missing, it exits and points at `./scripts/fetch_manifest.sh`.

`IMAGE_TAG` in `config.env` overrides the executor’s path-hash image tag. `SKIP_BUILD=1` skips `build.sh`. Populate Hub images first with `./scripts/build.sh --bench …` (tag defaults to `IMAGE_TAG`).

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

Per-bench keys the executor reads:
```json
{
  "experiment_index": "001",
  "num_generators": 1,
  "bench": "tests/one-service",
  "hosts_file": "hosts.txt",
  "slos": { "f1": "20" }
}
```

Output layout is a caller concern: `run_tests.sh` passes `--output-base-dir`; a standalone executor run uses that flag or the dataclass default (`./experiment_runs`). Optional JSON keys: `post_deploy_wait_sec`, `tuner`.

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
cd ~/files/roshanfer-experiments
git worktree add ../local-experiments dev

# Allow direnv in the new worktree
cd ../local-experiments && direnv allow
```

After running `benchmarks/k8s/create.sh` in each worktree, each gets its own kubeconfig. Open two terminals — `cd` into each directory and kubectl talks to the corresponding cluster automatically.
