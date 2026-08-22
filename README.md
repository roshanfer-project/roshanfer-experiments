# Roshanfer artifact — EuroSys 2027

This repository is the experiment harness for:

**Roshanfer: Achieving Performance Resilience in Cloud Microservices.** Farzad Mohammadi et al. EuroSys 2027 (paper #1195).

We are submitting this artifact for the ACM / EuroSys 2027 badges **Available**, **Functional**, and **Reproduced**.

This README has two independent parts:

1. **Tutorial** — two machines. Explains the repository layout and how a benchmark is built and run. It is not a reproduction of the paper figures.
2. **Reproducing the paper** — the CloudLab configuration used in the evaluation. Use this part only when the goal is to regenerate the paper results.

Artifact-evaluation work is on the `artifact-evaluation` branch.

---

## Time and resource overview

> **Author TODO.** Fill in measured human time and compute time. Please do not estimate. Keep this section incomplete until those measurements exist.
>
> Tutorial (two machines):
>
> - Install, walk through `one-service`, inspect a plot:
>
> Paper reproduction:
>
> - Instantiate the paper CloudLab parameter set:
> - Provision K3s and build the sidecar:
> - Hotel Reservation sweep:
> - Social Network sweep:
> - Alibaba / DGG 30-MS sweep:
> - Disk space for a full campaign:

---

# Part 1 — Tutorial (two machines)

Purpose: understand the project structure and the roles of the main components. Two machines are enough: one generator and one workload node. The control process (this repository) can run on either machine.

This part does **not** reproduce the paper figures and does **not** require the paper CloudLab parameter set.

## Roles in a run

Every experiment, including this tutorial, uses three roles. On two machines the control role shares a host with one of the others.

| Role | Tutorial | Purpose |
| --- | --- | --- |
| **Control** | colocated with one of the two machines | Clone of this repository. Runs `run_tests.sh` / `exec.executor`, holds `KUBECONFIG`, collects logs, and produces plots. |
| **Generator** | 1 machine | Runs the open-loop load generator (`rwg`). Not part of the Kubernetes cluster. |
| **Workload** | 1 machine | Kubernetes node. Runs the microservice(s) and, when requested, a Roshanfer sidecar. |

```mermaid
flowchart LR
  C["Control<br/>this repository"]
  G["Generator<br/>rwg"]
  W["Workload<br/>K3s + service + sidecar"]
  C -->|SSH, kubeconfig| W
  C -->|SSH, start rwg| G
  G -->|RPCs| W
```

In every hosts file, the first `num_generators` lines are generators and the remaining lines are workload nodes. For this tutorial that is one generator and one workload node (`--num-generators 1`).

## Repository layout

| Path | Role |
| --- | --- |
| `run_tests.sh` | Batch entry point. Runs suites under `configs/tests/*`, and optionally hotel, social, or alibaba. |
| `exec/` | Orchestrator: provision, deploy, generate, collect, and plot. |
| `configs/` | Per-benchmark `config.json`, `experiments.json`, and optional `merged.yaml`. |
| `benchmarks/` | Submodule. Service graphs, DeathStarBench wrappers, K3s/Cilium scripts, provisioning. |
| `rwg/` | Submodule. Go open-loop HTTP/gRPC generator. |
| `benchmarks/sidecar/` | Nested submodule. Roshanfer C++ sidecar. |

A **benchmark** is a pair: a tree under `benchmarks/` that builds and deploys the service graph, and a tree under `configs/` that describes which experiments to run.

## Build and run `one-service`

### 1. Two machines

Any two hosts with SSH access are sufficient (for example a two-node CloudLab experiment). They do not need to match the paper hardware.

Prepare a hosts file with the generator first:

```text
user@generator
user@workload
```

### 2. Clone on the control machine

```bash
git clone --recurse-submodules -b artifact-evaluation <this-repo-url>
cd roshanfer-experiments
git submodule update --init --recursive
```

If the clone was created without `--recurse-submodules`:

```bash
git submodule update --init --recursive
```

The required submodules are `benchmarks`, `rwg`, and `benchmarks/sidecar`.

### 3. Python environment and direnv

`run_tests.sh` prefers `.venv/bin/python` when it exists, otherwise `python`. It exits unless direnv has set `KUBECONFIG` to this clone’s `benchmarks/k8s/kubeconfig`.

```bash
sudo apt install direnv
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc   # or zsh
source ~/.bashrc
cd /path/to/roshanfer-experiments
direnv allow

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`init_env.sh` currently creates a directory named `env` and leaves `pip install` commented out. Please use the commands above until that script is updated.

### 4. What a benchmark directory contains

`configs/tests/one-service/` is the smallest example:

| File | Purpose |
| --- | --- |
| `config.json` | Bench name (`bench: tests/one-service`), SLOs, `num_generators`, paths to `rwg` and hosts. |
| `experiments.json` | List of runs: `type`, `system`, `loads`, `duration_sec`, `apis`, `repeat`. |
| `merged.yaml` | How to overlay systems on one figure (Plain vs Roshanfer). |
| `hosts.txt` | Local-mode hosts. With `--remote`, hosts come from a CloudLab manifest instead. |

Relevant fields in `config.json`:

```json
{
  "bench": "tests/one-service",
  "num_generators": 1,
  "slos": { "f1": "20" },
  "rwg_binary_path": "./rwg/rwg",
  "hosts_file": "configs/tests/one-service/hosts.txt"
}
```

Copy the two-machine hosts into `configs/tests/one-service/hosts.txt` (generator on the first line).

A small sidecar latency sweep in `experiments.json` looks like this:

```json
{
  "type": "latency-vs-throughput",
  "system": "sidecar",
  "apis": ["f1"],
  "loads": { "start": 500, "end": 2000, "step": 500 },
  "base_rate": 500,
  "duration_sec": 10,
  "repeat": 1,
  "warmup": 2
}
```

`system` is one of `plain` (no sidecar), `sidecar` (Roshanfer), `rajomon`, or `dagor`.

To add another example, copy the directory, edit `config.json` and `experiments.json`, and run `--bench my-example`. A new service graph belongs under `benchmarks/`; point `bench` at it.

### 5. Experiment types

These names appear in `experiments.json` and in `run_tests.sh --type`. They describe *what* a run measures. They are reused later for paper figures, but the tutorial only needs to show that each type is a different measurement.

| Type | Measurement |
| --- | --- |
| `latency-vs-throughput` | Latency versus offered load |
| `latency-and-goodput-vs-load` | P99 latency and goodput as load increases |
| `latency-and-rate-vs-time` | Time series after a load step (200 ms windows) |
| `max-queue` / `max-queue-motivation` | Per-service queue depth |
| `resource-waste` | Fraction of completed work that is later dropped or misses its SLO |

Fields that usually matter: `system`, `apis`, `loads.start` / `end` / `step`, `duration_sec`, `warmup`, `repeat`, and `slos` in `config.json`.

### 6. Run the example

```bash
./run_tests.sh \
  --bench one-service \
  --num-generators 1 \
  --comment tutorial
```

This uses `configs/tests/one-service/hosts.txt`. If the two machines came from a CloudLab manifest instead:

```bash
python -m exec.cloudlab_hosts --manifest ./manifest.xml -o ./cloudlab_hosts.txt --ssh-user YOUR_CLOUDLAB_USER

./run_tests.sh \
  --bench one-service \
  --remote \
  --cloudlab-manifest ./manifest.xml \
  --num-generators 1 \
  --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --comment tutorial
```

The script writes `exp_runs_test/<run_id>/one-service/` and plots under `exp_runs_test/<run_id>/plots/one-service/`. A later invocation creates a new timestamped directory.

### 7. Inspect output

```bash
ls exp_runs_test/*/one-service/exp-one-service/
ls exp_runs_test/*/plots/one-service/
```

Per-repeat metrics are under `…/repeat_000/output/` (`overall-*.json`, `realtime-*.csv`). Overlay plots are under `plots/one-service/merged/` when `merged.yaml` is present.

If filters exclude every experiment, plotting is skipped and a short message is printed. Missing or unknown flags print the usage text and exit.

---

# Part 2 — Reproducing the paper

Purpose: regenerate the evaluation in the paper. This is separate from the tutorial. It requires the CloudLab configuration below. We have not validated other hardware types or node counts.

For AEC access we will collect SSH public keys (please omit the `user@host` comment). CloudLab account passwords are not required. Discussion should go through HotCRP.

## Paper environment

| Item | Value used in the paper |
| --- | --- |
| CloudLab profile | [PortalProfiles/small-lan](https://www.cloudlab.us/p/PortalProfiles/small-lan) |
| Parameter set | [f369c1b9-2eff-425f-b5ce-d7493a17fd76](https://www.cloudlab.us/p/PortalProfiles/small-lan&rerun_paramset=f369c1b9-2eff-425f-b5ce-d7493a17fd76) |
| Hardware | CloudLab `c220g2` |
| Roles | 1 control, 3 generators, 22 workload |
| Cluster | K3s and Cilium, via `benchmarks/k8s/` (`K3S_VERSION` and `CILIUM_VERSION` in `benchmarks/k8s/config.env`) |
| Sidecar | C++, `ubuntu:noble` in the sidecar Dockerfile |
| Python | versions pinned in `requirements.txt` |

```mermaid
flowchart LR
  C["Control<br/>this repository"]
  G["3 generators<br/>rwg"]
  W["22 workload nodes<br/>K3s + services + sidecars"]
  C -->|SSH, kubeconfig| W
  C -->|SSH, start rwg| G
  G -->|RPCs| W
```

## Instantiate and clone

1. Open the parameter set above and instantiate it so the hardware type and node count match the paper.
2. Wait until all nodes are ready, then download the experiment manifest XML.
3. On the control machine:

```bash
git clone --recurse-submodules -b artifact-evaluation <this-repo-url>
cd roshanfer-experiments
git submodule update --init --recursive

sudo apt install direnv
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
source ~/.bashrc
direnv allow

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m exec.cloudlab_hosts --manifest ./manifest.xml -o ./cloudlab_hosts.txt --ssh-user YOUR_CLOUDLAB_USER
```

With `--num-generators 3`, the first three hosts are generators and the remaining 22 are workload nodes.

## Artifact map

| Component | Location | Paper |
| --- | --- | --- |
| Roshanfer sidecar (Agent, Ingress, and credit protocol) | `benchmarks/sidecar/` | Design and implementation (§3–§5) |
| Experiment orchestrator | `exec/`, `run_tests.sh` | Evaluation (§6) |
| Open-loop generator | `rwg/` | §6.1 |
| Hotel Reservation and Social Network | `configs/hotel/`, `configs/social/`, plus `benchmarks/` | Figures 7–11, 14 |
| Alibaba / DGG 30-MS and dynamic graphs | `configs/alibaba-large/`, `configs/tests/dynamic-large/`, `fan-out-dynamic-*` | Figures 12–13 |
| Rajomon and Dagor baselines | `system: rajomon` / `dagor` in `experiments.json` | §6 |
| TLA+ model | **Author TODO.** Add the specification link. | §5 (Request Bound, Deadlock Freedom, Work Conservation) |

## How to run the paper experiments

The following commands assume `manifest.xml` is in the repository root and the virtualenv from the previous section is active.

All paper benches in one invocation:

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --also-hotel-social --also-alibaba
```

Or the three paper benches separately:

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench social --also-hotel-social
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench alibaba-large --also-alibaba
```

Dynamic-graph benches used for Figure 12:

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench dynamic-large
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench fan-out-dynamic-0-9
```

Plots are produced by `exec.plot_runner` and `exec.merged_plot_runner` using each bench’s `merged.yaml` (labels Roshanfer, Rajomon, Dagor, Plain). Each invocation writes a new `exp_runs_test/<timestamp>/` directory.

## Figures and commands

Each figure lists the full command(s). Shared flags are repeated so a command can be copied on its own. `--type` and `--system` restrict `experiments.json` to the runs that figure uses.

**Figure 7** (`latency-and-goodput-vs-load`; hotel `search-hotel` and social `compose-post`; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench social --also-hotel-social \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor
```

**Figure 8** (`latency-and-rate-vs-time`; hotel; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type latency-and-rate-vs-time --system sidecar,rajomon,dagor
```

**Figure 9** (`resource-waste`; hotel and social; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type resource-waste --system sidecar,rajomon,dagor
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench social --also-hotel-social \
  --type resource-waste --system sidecar,rajomon,dagor
```

**Figure 10** (`max-queue`; hotel and social; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type max-queue --system sidecar,rajomon,dagor
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench social --also-hotel-social \
  --type max-queue --system sidecar,rajomon,dagor
```

**Figure 11** (`latency-and-goodput-vs-load`; social, three APIs; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench social --also-hotel-social \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor --num-apis 3
```

**Figure 12** (`latency-and-goodput-vs-load`; dynamic-graph tests; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench dynamic-large \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench fan-out-dynamic-0-9 \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor
```

**Figure 13** (`latency-and-goodput-vs-load`; alibaba-large; sidecar, rajomon, dagor):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench alibaba-large --also-alibaba \
  --type latency-and-goodput-vs-load --system sidecar,rajomon,dagor
```

**Figure 14** (`latency-vs-throughput`; hotel; plain, sidecar):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type latency-vs-throughput --system plain,sidecar
```

**Figures 1–2** (motivation; hotel; rajomon):

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type latency-and-rate-vs-time --system rajomon
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type max-queue-motivation --system rajomon
```

```bash
./run_tests.sh --remote --cloudlab-manifest ./manifest.xml \
  --num-generators 3 --cloudlab-ssh-user YOUR_CLOUDLAB_USER \
  --bench hotel --also-hotel-social \
  --type resource-waste --system rajomon
```

> **Author TODO.** Confirm these commands against the paper. Figure 15 (overcommitment / WRR) does not yet have a command.

## Expected warnings and errors

> **Author TODO.** Careful pass over the paper-reproduction commands (CloudLab instantiate, clone, direnv, virtualenv, manifest parsing, hotel, social, alibaba). For each step, record expected messages, genuine failures and the recovery step, and what a successful run prints. Please write this from observed output.

## Troubleshooting

> **Author TODO.** Same pass. Please cover at least: direnv / `KUBECONFIG`, SSH user mismatch, uninitialized submodules, the `~/.roshanfer_provisioned` marker, `--remote-clean`, skipped plots, and Rajomon tuner duration.

---

## License

License files are not yet in the tree. We plan to add a license that permits comparison and extension (MIT or CC-BY), as required for Available.

`exec/README.md` describes tuners, git worktrees, and additional plot flags.
