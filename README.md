# Roshanfer artifact — EuroSys 2027

This repository is the experiment harness for:

**Roshanfer: Achieving Performance Resilience in Cloud Microservices.** Farzad Mohammadi et al. EuroSys 2027 (paper #1195).

We are submitting this artifact for the ACM / EuroSys 2027 badges **Available**, **Functional**, and **Reproduced**.

This README has two independent parts:

1. **Tutorial** — one machine. Explains the repository layout and how a benchmark is built and run. It is not a reproduction of the paper figures.
2. **Reproducing the paper** — the CloudLab configuration used in the evaluation. Use this part only when the goal is to regenerate the paper results.

Artifact-evaluation work is on the `artifact-evaluation` branch.

---

## Time and resource overview

> **Author TODO.** Fill in measured human time and compute time. Please do not estimate. Keep this section incomplete until those measurements exist.
>
> Tutorial (one machine):
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
> - Dynamic-graph sweep (`dynamic-large`, `fan-out-dynamic-0-9`):
> - Figure 15 leaf benches (`leaf-1-2`, `leaf-1-10`, `leaf-1-2-p-2-1`):
> - Disk space for a full campaign:

---

# Part 1 — Tutorial (one machine)

Purpose: understand the project structure and the roles of the main components. One machine is enough. Control, generator, and workload all run on it.

This part does **not** reproduce the paper figures and does **not** require CloudLab.

## Roles in a run

Every experiment, including this tutorial, uses three roles. Here they share a single host.

| Role | Tutorial | Purpose |
| --- | --- | --- |
| **Control** | this machine | Clone of this repository. Runs `run_tests.sh` / `exec.executor`, holds `KUBECONFIG`, collects logs, and produces plots. |
| **Generator** | this machine | Runs the open-loop load generator (`rwg`). Not part of the Kubernetes cluster. |
| **Workload** | this machine | Kubernetes node. Runs the microservice(s) and, when requested, a Roshanfer sidecar. |

```mermaid
flowchart LR
  subgraph M["One machine"]
    C["Control<br/>this repository"]
    G["Generator<br/>rwg"]
    W["Workload<br/>K3s + service + sidecar"]
  end
  C -->|kubeconfig| W
  C -->|start rwg| G
  G -->|RPCs| W
```

In every hosts file, the first `num_generators` lines are generators and the remaining lines are workload nodes. The harness needs at least one generator line and one workload line (`num_generators` + 1). On one machine that is the same host written twice, with `--num-generators 1`.

## Repository layout

Each folder below is an artifact component. Comments say what it is and how it relates to the paper.

```text
roshanfer-experiments/
├── README.md                      this document
├── LICENSE                        MIT (original Roshanfer code)
├── THIRD_PARTY.md                 DeathStarBench, NanoLog, and other third-party licenses
├── run_tests.sh                   batch entry: configs/tests/*, plus hotel/social/alibaba when asked
├── requirements.txt               Python packages for exec/ and plotting
├── .envrc                         direnv: KUBECONFIG + config.env
├── init_env.sh                    venv + loads config.env; sourced by run_tests.sh
├── scripts/fetch_manifest.sh      geni-get manifest when CONTROL_ON_CLUSTER=1
├── scripts/build.sh               push sidecar + bench images (tag + --bench); then SKIP_BUILD=1
├── config.env.example             copy to config.env (gitignored)
├── hosts.txt.example              copy to hosts.txt for local mode (gitignored)
├── compare_sidecar_branch.sh      author helper: run the same bench on two sidecar git refs
│
├── exec/                          orchestrator for the evaluation (§6)
│   ├── executor.py                provision, deploy, generate, collect
│   ├── cloudlab_hosts.py          CloudLab manifest.xml → hosts file
│   ├── plot_runner.py             per-bench plots
│   ├── merged_plot_runner.py      overlay plots (Roshanfer / Rajomon / Dagor / Plain)
│   ├── rajomon_tuner.py           Rajomon parameter search before a run
│   └── sidecar_tuner.py           sidecar tuner hook
│
├── configs/                       what to run (config.json, experiments.json, merged.yaml)
│   ├── hotel/                     Hotel Reservation experiments (Figs. 7–11, 14)
│   ├── social/                    Social Network experiments (Figs. 7, 9–11)
│   ├── alibaba-large/             Alibaba / DGG 30-MS (Fig. 13)
│   └── tests/                     synthetic graphs
│       ├── one-service/           tutorial example
│       ├── dynamic-large/         Fig. 12
│       ├── fan-out-dynamic-0-9/   Fig. 12
│       ├── leaf-1-2/              Fig. 15, first subfigure
│       ├── leaf-1-10/             Fig. 15, second subfigure
│       ├── leaf-1-2-p-2-1/        Fig. 15, third subfigure
│       └── …                      other synthetic graphs (chain, fan-out, multi-api, …)
│
├── benchmarks/                    git submodule: service graphs and cluster scripts
│   ├── hotel/                     DeathStarBench Hotel Reservation
│   ├── social/                    DeathStarBench Social Network
│   ├── alibaba-large/             generated 30-MS graph
│   ├── tests/                     generated synthetic graphs (pairs with configs/tests/)
│   ├── callgraph-framework/       generates those graphs from callgraph.json
│   ├── k8s/                       K3s + Cilium create/delete (config.env versions)
│   ├── provisioning/              host bootstrap (Go, sysctls)
│   └── sidecar/                   nested submodule: Roshanfer C++ sidecar (§3–§5)
│       ├── src/                   Agent, Ingress, credit queues, RPC mapping
│       ├── include/               public headers for the same
│       └── Dockerfile             ubuntu:noble image used in the paper
│
├── rwg/                           git submodule: open-loop HTTP/gRPC generator (§6.1)
│
├── scripts/                       extra helpers (queue-size notes, plot regen); not the paper entry
└── tests/                         small standalone runs, separate from configs/tests/
    └── one-api-vs-time/
```

A **benchmark** is a pair: a tree under `benchmarks/` that builds and deploys the service graph, and a tree under `configs/` that describes which experiments to run. `run_tests.sh` and `exec/` sit above both.

## Build and run `one-service`

### 1. One machine

Any Linux host with SSH to itself is sufficient. It does not need to match the paper hardware.

The harness reads a hosts file and treats the first line as the generator and the second as the workload node. List the same host twice:

```text
user@localhost
user@localhost
```

`ssh user@localhost` should succeed without a password prompt (for example with an SSH key added to `~/.ssh/authorized_keys`).

### 2. Clone

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

### 3. Configure once

Copy the example env file and, for a local run, the hosts file. `run_tests.sh` re-reads `config.env` on every start (direnv also loads it for the interactive shell).

```bash
cp config.env.example config.env
# Local tutorial: allow local mode and list machines
# In config.env: REQUIRE_REMOTE=0
cp hosts.txt.example hosts.txt   # edit to user@host; first lines are generators
```

Every hosts line must be `user@host`. One `hosts.txt` is shared by every bench, by `create.sh` / `delete.sh` (they skip the first `NUM_GENERATORS` lines), and by `provision.sh` (all lines).

To push images to `REGISTRY` under `IMAGE_TAG` (then set `SKIP_BUILD=1` so `run_tests.sh` pulls instead of rebuilding):

```bash
./scripts/build.sh --bench one-service
# or: ./scripts/build.sh --tag latest --bench one-service,hotel,social
```

### 4. Python environment and direnv

`run_tests.sh` sources `init_env.sh` before any experiment, which creates `.venv` if needed, installs `requirements.txt`, and activates the venv. It exits unless direnv has set `KUBECONFIG` to this clone’s `benchmarks/k8s/kubeconfig`.

```bash
sudo apt install direnv
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc   # or zsh
source ~/.bashrc
cd /path/to/roshanfer-experiments
direnv allow

./init_env.sh   # optional; run_tests.sh does this automatically
```

### 5. What a benchmark directory contains

`configs/tests/one-service/` is the smallest example:

| File | Purpose |
| --- | --- |
| `config.json` | Bench name (`bench: tests/one-service`), SLOs, `num_generators`. Local hosts are repo-root `hosts.txt`. |
| `experiments.json` | List of runs: `type`, `system`, `loads`, `duration_sec`, `apis`, `repeat`. |
| `merged.yaml` | How to overlay systems on one figure (Plain vs Roshanfer). |

Relevant fields in `config.json`:

```json
{
  "bench": "tests/one-service",
  "num_generators": 1,
  "slos": { "f1": "20" },
  "hosts_file": "hosts.txt"
}
```

Also used: `experiment_index`. Optional: `post_deploy_wait_sec`, `tuner`.

Copy `hosts.txt.example` to `hosts.txt` and set `user@host` lines (see Configure once).

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

### 6. Experiment types

These names appear in `experiments.json` and in `run_tests.sh --type`. They describe *what* a run measures. They are reused later for paper figures, but the tutorial only needs to show that each type is a different measurement.

| Type | Measurement |
| --- | --- |
| `latency-vs-throughput` | Latency versus offered load |
| `latency-and-goodput-vs-load` | P99 latency and goodput as load increases |
| `latency-and-rate-vs-time` | Time series after a load step (200 ms windows) |
| `max-queue` / `max-queue-motivation` | Per-service queue depth |
| `resource-waste` | Fraction of completed work that is later dropped or misses its SLO |
| `throughput-vs-overcommitment` | Throughput as the overcommitment factor varies (Fig. 15) |

Fields that usually matter: `system`, `apis`, `loads.start` / `end` / `step`, `duration_sec`, `warmup`, `repeat`, and `slos` in `config.json`. For `throughput-vs-overcommitment`, the overcommitment values are in `overcommitments`.

### 7. Run the example

```bash
./run_tests.sh \
  --bench one-service \
  --num-generators 1 \
  --comment tutorial
```

This uses repo-root `hosts.txt` (local mode, no CloudLab manifest). Requires `REQUIRE_REMOTE=0` in `config.env`. The script writes `exp_runs_test/<run_id>/one-service/` and plots under `exp_runs_test/<run_id>/plots/one-service/`. A later invocation creates a new timestamped directory.

### 8. Inspect output

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
2. Wait until all nodes are ready.
3. From this repository on your laptop, SSH to the control node (`node0`), clone branch `artifact-evaluation` with `--recurse-submodules` into `~/roshanfer-experiments`, and attach tmux session `roshanfer` (created if needed):

```bash
./scripts/cloudlab_enter.sh --name NAME --project PROJECT --user USER
# default --url wisc.cloudlab.us
```

`NAME` and `PROJECT` are **Name** and **Project** on the CloudLab experiment page. Detach with `./scripts/cloudlab_leave.sh` inside tmux (or **Ctrl-b d**); that also ends SSH. The session and clone stay on the node.

4. On the control machine:

```bash
# Skip git clone if you used cloudlab_enter.sh (~/roshanfer-experiments is already on artifact-evaluation with submodules).
git clone --recurse-submodules -b artifact-evaluation <this-repo-url>
cd roshanfer-experiments
git submodule update --init --recursive

sudo apt install direnv
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
source ~/.bashrc
direnv allow

source ./init_env.sh   # optional; run_tests.sh does this automatically

cp config.env.example config.env
# Set CLOUDLAB_SSH_USER. Leave REQUIRE_REMOTE=1 and CONTROL_ON_CLUSTER=1
./scripts/fetch_manifest.sh   # geni-get → ./manifest.xml
```

If the control machine is **not** on the cluster, set `CONTROL_ON_CLUSTER=0` in `config.env` and copy the portal experiment manifest to `./manifest.xml` yourself. `run_tests.sh --remote` exits if that file is missing.

The first node in the manifest is the control machine and is dropped when building the hosts list. With `--num-generators 3`, the next three hosts are generators and the remaining 22 are workload nodes.

## Artifact map

| Component | Location | Paper |
| --- | --- | --- |
| Roshanfer sidecar (Agent, Ingress, and credit protocol) | `benchmarks/sidecar/` | Design and implementation (§3–§5) |
| Experiment orchestrator | `exec/`, `run_tests.sh` | Evaluation (§6) |
| Open-loop generator | `rwg/` | §6.1 |
| Hotel Reservation and Social Network | `configs/hotel/`, `configs/social/`, plus `benchmarks/` | Figures 7–11, 14 |
| Alibaba / DGG 30-MS and dynamic graphs | `configs/alibaba-large/`, `configs/tests/dynamic-large/`, `fan-out-dynamic-*` | Figures 12–13 |
| Overcommitment (Fig. 15) | `configs/tests/leaf-1-2/`, `leaf-1-10/`, `leaf-1-2-p-2-1/` | Figure 15 |
| Rajomon and Dagor baselines | `system: rajomon` / `dagor` in `experiments.json` | §6 |
| TLA+ model | **Author TODO.** Add the specification link. | §5 (Request Bound, Deadlock Freedom, Work Conservation) |

## How to run the paper experiments

The paper results come from a full run of each benchmark below. Each command executes every experiment in that bench’s `experiments.json`. After the runs finish, the paper figures are taken from those outputs (see the next section).

The following commands assume `config.env` has `CLOUDLAB_SSH_USER` and `CLOUDLAB_MANIFEST` (typically `./manifest.xml`, from `./scripts/fetch_manifest.sh` or a portal download) and the virtualenv from the previous section is active. Flags still override those values.

**Hotel Reservation**

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench hotel --also-hotel-social
```

**Social Network**

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench social --also-hotel-social
```

**Alibaba / DGG 30-MS**

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench alibaba-large --also-alibaba
```

**Dynamic graphs**

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench dynamic-large
```

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench fan-out-dynamic-0-9
```

**Figure 15**

These three benches set `num_generators` to 2 in `config.json`. Override with `--num-generators 3` (the first three manifest hosts are generators).

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench leaf-1-2
```

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench leaf-1-10
```

```bash
./run_tests.sh --remote --num-generators 3 \
  --bench leaf-1-2-p-2-1
```

Each invocation writes a new `exp_runs_test/<timestamp>/` directory. Plots are under `exp_runs_test/<timestamp>/plots/<bench>/`. Overlay plots use each bench’s `merged.yaml` (labels Roshanfer, Rajomon, Dagor, Plain) when that file is present. The Fig. 15 leaf benches do not include `merged.yaml`.

Hotel, social, and alibaba in one invocation (this does not include the dynamic-graph or Fig. 15 leaf benches):

```bash
./run_tests.sh --remote --num-generators 3 \
  --also-hotel-social --also-alibaba
```

## Which figures come from which run

The paper figures were selected from the full bench outputs above, not from a separate command per figure.

| Paper figure | Full run | Measurement in that output |
| --- | --- | --- |
| Figs. 1–2 (motivation) | Hotel | `latency-and-rate-vs-time`, `max-queue-motivation`, `resource-waste` (rajomon) |
| Fig. 7 | Hotel and Social | `latency-and-goodput-vs-load` (hotel `search-hotel`, social `compose-post`) |
| Fig. 8 | Hotel | `latency-and-rate-vs-time` |
| Fig. 9 | Hotel and Social | `resource-waste` |
| Fig. 10 | Hotel and Social | `max-queue` |
| Fig. 11 | Social | `latency-and-goodput-vs-load` (three APIs) |
| Fig. 12 | Dynamic graphs (`dynamic-large` and `fan-out-dynamic-0-9`) | `latency-and-goodput-vs-load` |
| Fig. 13 | Alibaba / DGG 30-MS | `latency-and-goodput-vs-load` |
| Fig. 14 | Hotel | `latency-vs-throughput` (plain, sidecar) |
| Fig. 15 (first subfigure) | `leaf-1-2` | `throughput-vs-overcommitment` |
| Fig. 15 (second subfigure) | `leaf-1-10` | `throughput-vs-overcommitment` |
| Fig. 15 (third subfigure) | `leaf-1-2-p-2-1` | `throughput-vs-overcommitment` |

> **Author TODO.** Confirm this mapping against the paper.

## Expected warnings and errors

> **Author TODO.** Careful pass over the paper-reproduction commands (CloudLab instantiate, clone, direnv, virtualenv, manifest parsing, hotel, social, alibaba). For each step, record expected messages, genuine failures and the recovery step, and what a successful run prints. Please write this from observed output.

## Troubleshooting

> **Author TODO.** Same pass. Please cover at least: direnv / `KUBECONFIG`, SSH user mismatch, uninitialized submodules, the `~/.roshanfer_provisioned` marker, `--remote-clean`, skipped plots, and Rajomon tuner duration.

---

## License

Original code in this repository, and in the `rwg`, `benchmarks` (except as noted below), and `benchmarks/sidecar` submodules, is under the [MIT License](LICENSE). That license allows comparison and extension, as required for the Available badge.

Third-party components keep their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md). `benchmarks/hotel/` and `benchmarks/social/` are derived from [DeathStarBench](https://github.com/delimitrou/DeathStarBench) (Apache License 2.0).

`exec/README.md` describes tuners, git worktrees, and additional plot flags.
