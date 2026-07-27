# Exec - Experiment Execution and Plotting

Framework for running experiments and generating plots, designed for CloudLab environments.

## Architecture

The execution framework orchestrates experiments across a set of remote hosts ("CloudLab nodes").
It follows a **Tune -> Deploy -> Run -> Collect** cycle.

1.  **Partition**: Hosts are split into **Generators** and **Deployment** nodes.
2.  **Tune**: System-specific tuners find optimal parameters (e.g., resource limits).
3.  **Deploy**: The system is deployed ONCE per benchmark/system. Supported systems: `plain`, `p2c`, `wrr`, `sidecar`, `approx`, `approx-fcfs`, `approx-edf`, `envoy`, `rajomon`, `rajomon-lb`, `dagor`, `dagor-lb`.
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
3.  **Provisioning**: Ensure `benchmarks/provisioning/provision.sh` exists and is idempotent. It clones/checks out one branch name on remotes for both `roshanfer-experments` and `benchmarks` (passed as `BRANCH` / `--branch`; default = local active branch). Both GitHub repos must publish that same branch name. If a remote checkout is on a different branch, provision wipes `~/roshanfer-experments` and re-clones.
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
- `--branch NAME`: Git branch to provision on remotes for both `roshanfer-experments` and `benchmarks` (same name required). Default: local active branch of the parent repo. Passed to `provision.sh` as `BRANCH`.
- `--only-names "exp1,exp2"`: Run specific experiments (use derived names).
- `--only-types "type1"`: Run specific types.
- `--name-contains "substring"`: Filter by name substring.

Experiment names are derived from `type`, `bench`, and `system`: `{type}-{bench}-{system}` (or `{type}-{bench}-{n}-{system}` for multi-API). Examples:
```bash
--only-names "latency-vs-throughput-one-service-plain,latency-vs-throughput-one-service-sidecar"
--name-contains "sidecar"
--only-types "latency-vs-throughput" --name-contains "plain"
```

## Experiment load schema

Each entry in `experiments.json` controls how RWG drives load. There are three styles:

| Style | When | Per-API config |
|-------|------|----------------|
| **Legacy** | `load_mode` omitted | Not required — global `loads` / `base_rate` / `duration_sec` apply to all APIs |
| **`load_mode: "sweep"`** | Explicit 2-phase sweep | **Required** — every API must have `api_loads.<api>.loads` |
| **`load_mode: "phases"`** | Arbitrary multi-phase profile | **Required** — every API must have `api_loads.<api>.phases` |

When `load_mode` is set, every API listed in `apis[]` must appear in `api_loads` (no fallback to top-level `loads`).

RWG runs a sequence of rate/duration **phases** per API. Legacy and sweep modes always produce two phases: warmup then steady. The experiment-level `warmup` field (passed to `rwg parse`) is separate from the RWG warmup phase.

### Legacy (backward compatible)

```json
{
  "loads": { "start": 1600, "end": 1600, "step": 1000 },
  "base_rate": 1000,
  "duration_sec": 15,
  "apis": ["f1", "f2"]
}
```

Runtime phases per API: `[{rate: 1000, duration_sec: 2}, {rate: 1600, duration_sec: 15}]`. Warmup duration defaults to 2s (`warmup_duration_sec`).

### Explicit sweep (`load_mode: "sweep"`)

Global timing: `warmup_duration_sec` (default 2), `base_rate` (warmup rate default), `duration_sec` (steady duration).

Per-API steady-rate sweep in `api_loads`. Sweeps are zip-aligned — all APIs must produce the same number of load steps.

```json
{
  "load_mode": "sweep",
  "warmup_duration_sec": 2,
  "base_rate": 1000,
  "duration_sec": 15,
  "apis": ["f1", "f2"],
  "api_loads": {
    "f1": { "loads": { "start": 1600, "end": 2000, "step": 200 } },
    "f2": { "loads": { "start": 800, "end": 1200, "step": 200 } }
  }
}
```

Run units are named `{experiment}-rate-{N}` where `N` is the max steady rate across APIs at that step.

Optional per-API warmup override: `api_loads.<api>.base_rate`.

### Multi-phase (`load_mode: "phases"`)

Single run unit with arbitrary phases per API:

```json
{
  "load_mode": "phases",
  "apis": ["f1", "f2"],
  "api_loads": {
    "f1": { "phases": [
      { "rate": 1000, "duration_sec": 2 },
      { "rate": 1600, "duration_sec": 15 }
    ]},
    "f2": { "phases": [
      { "rate": 1000, "duration_sec": 2 },
      { "rate": 800, "duration_sec": 20 }
    ]}
  }
}
```

Top-level `loads` / `base_rate` / `duration_sec` are ignored in this mode.

### Validation

- `load_mode` set → every `apis[]` entry must have `api_loads[api]`
- Unknown keys in `api_loads` → error
- `load_mode: "sweep"` → all per-API `loads` ranges must yield the same step count
- `load_mode: "phases"` → each API must have at least one phase

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
./run_tests.sh --also-hotel-social
./run_tests.sh --remote --cloudlab-manifest ~/manifest.xml --num-generators 3 --cloudlab-ssh-user ubuntu
./run_tests.sh --remote --branch lb-explore --cloudlab-manifest ~/manifest.xml --num-generators 3
```

`--remote` writes `exp_runs_test/<timestamp>/cloudlab_hosts.txt` and passes `--hosts-file` / `--num-generators` to each executor run.

`--branch NAME` selects the git branch provisioned on remotes for **both** `roshanfer-experments` and `benchmarks` (one name; both remotes must have it). Default is the local active branch. Before starting exec, `run_tests.sh` requires the local parent and `benchmarks` checkouts to be on the **same** named branch; if they differ (or are detached), it errors and does not invoke exec. If `--branch` is set, it must match that local pair.

`--namespace NS` selects namespace-specific config files (`config-<ns>.json`, `experiments-<ns>.json`) instead of the default `config.json` / `experiments.json`. The namespace is stored in `exp_runs_test/<run_id>/.namespace` for plot regeneration. See [Config namespaces](#config-namespaces) below.

`--remote-clean` (with the same `--cloudlab-manifest` and `--num-generators`) removes `~/.roshanfer_provisioned` on **every** listed host, then runs `benchmarks/k8s/delete.sh` using **deployment** hosts only (all lines after the first `num_generators`). Use alone to reset infra and exit, or add `--remote` to clean and then run tests.

### Local vs Remote Host Resolution

Hosts are always read from a plain-text file (one `[user@]host` per line, `#` comments ignored) via `InfraBuilder`. The first `num_generators` lines become generator nodes; the rest become deployment (K8s) nodes. The two modes differ only in *which* file is used:

- **Local mode** — each test's `config.json` has a `hosts_file` field pointing to a static per-test file, e.g. `configs/tests/one-service/hosts.txt`. There is no auto-discovery; you edit that file to match your local setup.
- **Remote mode** (`--remote`) — `run_tests.sh` parses the CloudLab `manifest.xml` into a generated `cloudlab_hosts.txt` (via `exec.cloudlab_hosts`) and passes it with `--hosts-file`, overriding whatever `hosts_file` the config specifies.

`benchmarks/k8s/hosts.txt` and `benchmarks/provisioning/hosts.txt` are only used as defaults when running those shell scripts *manually*; the executor always overrides them by setting the `HOSTS_FILE` env var (and `BRANCH` when `--branch` / local default is set).

## Tuning

The orchestrator automatically looks for a tuner script in `tuner/<system>_tuner.py` or `exec/<system>_tuner.py`.
If found, it runs the tuner before deploying the system (unless cached parameters are available).
Results are saved to `exp_runs/exp-<id>/tuning/<system>/best_params.json`.

**Parameter lookup order** (for systems with a tuner module):

1. Run cache: `{output_base_dir}/exp-<id>/tuning/<system>/best_params.json`
2. Config suite cache: `{config_file_dir}/tuning/<system>/best_params.json` (e.g. `configs/tests/fanin-lb/tuning/rajomon-lb/best_params.json` next to `config-lb.json`)
3. Live tuning via `exec/<system>_tuner.py`

When params are loaded from the config suite cache, they are copied into the run cache for resume/audit. Both flat JSON objects and `{"parameters": {...}}` wrappers are accepted.

To create a new tuner, copy `exec/tuner_template.py` to `exec/<system>_tuner.py` and implement your optimization logic.

### Rajomon tuner (`exec/rajomon_tuner.py`)

Used for **`rajomon`** and **`rajomon-lb`**. The executor maps `rajomon-lb` to this module (same five knobs); deploy still uses `SYSTEM=rajomon-lb` so the benchmark deploy script picks the LB branch.

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

Standalone:

```bash
python -m exec.rajomon_tuner --config configs/tests/fanin-lb/config-lb.json --system rajomon-lb --bench tests/fanin-lb --output tuning.json
python -m exec.rajomon_tuner --config ... --system rajomon --bench tests/my-bench --output tuning.json
```

Cached params per run: `exp-<id>/tuning/rajomon-lb/best_params.json` (or `.../rajomon/...` for single-replica rajomon).

Pre-tuned params can also live under the test suite config directory, e.g. `configs/tests/fanin-lb/tuning/rajomon-lb/best_params.json` (used when the run cache is missing).

## Config namespaces

Use namespaces to try alternate experiment setups without changing existing configs. The **default** namespace (no flag, or `--namespace default`) uses today's filenames unchanged.

| Suite | Default | Namespace `<ns>` |
|-------|---------|------------------|
| `configs/tests/<name>/` | `config.json`, `experiments.json`, `merged.yaml` | `config-<ns>.json`, `experiments-<ns>.json`, `merged-<ns>.yaml` |
| `configs/hotel/` | `config.hotel.json`, `hotel_experiments.json`, `merged.yaml` | `config.hotel-<ns>.json`, `hotel_experiments-<ns>.json`, `merged-<ns>.yaml` |
| `configs/social/` | `config.social.json`, `social_experiments.json`, `merged_social.yaml` | `config.social-<ns>.json`, `social_experiments-<ns>.json`, `merged_social-<ns>.yaml` |
| `configs/alibaba-large/` | `config.alibaba.json`, `experiments.json`, `merged.yaml` | `config.alibaba-<ns>.json`, `experiments-<ns>.json`, `merged-<ns>.yaml` |

A suite is run for namespace `<ns>` only when both config and experiments files exist. Merged plots are optional (skipped if the merged file is missing).

```bash
./run_tests.sh --namespace newsys --bench leaf-diverse
python -m exec.namespace resolve --kind tests --dir configs/tests/leaf-diverse --namespace newsys
python -m exec.namespace list-tests --namespace newsys
```

Run dirs record the namespace in `.namespace`; `scripts/regenerate_run_plots.sh` reads it automatically.

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
