# Evaluator Configuration Design

**Paper**: Roshanfer, EuroSys 2027 #1195  
**Author**: Farzad Mohammadi (`farzad1132`)  
**Rescan**: after `566e5a4` (`remove unused fields in configs`) on `artifact-evaluation`  
**Goal**: evaluators edit one file; scripts read it. No experiment-logic change in this pass.

This note is a design + inventory, not a full caller migration.

---

## What changed since the first inventory

`566e5a4` stripped unused keys from per-bench JSON and from `exec/config.py`. Those keys are **gone**; do not put them back into a central config.

| Removed from configs / `Config` | Why it no longer belongs in `evaluator.env` |
|---|---|
| `prometheus_url` | Executor no longer reads it |
| `remote_microservice_host` / `_user` / `_path` | Removed from `Config`. Only leftover: `exec/env-setter.py` (orphaned; see below) |
| `rwg_binary_path` in JSON | Dataclass default `./rwg/rwg` is enough |
| `output_base_dir`, `ssh_binary`, `git_root`, `docker_compose_binary` | Not evaluator identity; caller/CLI concern |
| `experiment_defaults`, `expansion`, `metrics`, `report`, `notes` | Unused |

Also already fixed on `artifact-evaluation` (do not treat as open typos):

- `benchmarks/provisioning/provision.sh` clones `farzad1132/roshanfer-experiments` (correct spelling)
- `exec/runner.py` default remote path is `~/roshanfer-experiments`
- `benchmarks/k8s/update_repo.sh` uses `~/roshanfer-experiments`

`configs/tests/leaf-diverse/` was replaced by `leaf-1-2`, `leaf-1-10`, `leaf-1-2-p-2-1`.

`benchmarks/sidecar` still fails to clone here (`roshanfer-sidecar` not found over this environment’s GitHub token). The sidecar tree was searched on GitHub instead.

---

## Live `Config` fields (do not centralize these)

After the cleanup, `exec/config.py` only keeps fields the executor actually reads. Per-bench JSON now looks like:

```json
{
  "experiment_index": "one-service",
  "num_generators": 1,
  "bench": "tests/one-service",
  "hosts_file": "configs/tests/one-service/hosts.txt",
  "slos": { "f1": "20" }
}
```

Optional JSON: `post_deploy_wait_sec`, `tuner`.  
Campaign output dir is `--output-base-dir` / dataclass default, not evaluator identity.

**Leave these in the per-bench JSON.** They are experiment parameters, not “who is running this.”

---

## Complete inventory (current tree)

Grouped. “Who sets” is **evaluator** (must change to run as themselves) vs **stay-as-author** (author/code fix, or not on the AE path).

### A. SSH users and hosts (evaluator)

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| `benchmarks/k8s/config.env` | `SSH_USER=fm224` | Default SSH user when a hosts line has no `user@`. Sourced by `k8s/create.sh`, `k8s/delete.sh`, `provisioning/provision.sh`, and `run_tests.sh --remote-clean` | Evaluator |
| `run_tests.sh` usage example | `--cloudlab-ssh-user farzad11` | Docs only; real path is `--cloudlab-ssh-user` | Evaluator (via flag or central file) |
| `run_tests.sh` / `provision.sh` fallback | `ubuntu` | Generic CloudLab image user when `SSH_USER` unset and the hosts line has no user | Keep as fallback (not author-specific) |
| 18× `configs/tests/*/hosts.txt` | `farzad@localhost` | Local-mode generator line. Tutorial README already says “put `user@localhost` twice” | Evaluator for the one-machine tutorial |
| 18× `configs/tests/*/hosts.txt` | `fm224@octopus3.doc.res.ic.ac.uk` | Author Imperial host as the second (deploy) line | Stay-as-author for AE paper runs (`--remote` overrides). Evaluator must replace if they use local mode |
| `configs/hotel/hosts.txt`, `configs/social/hosts.txt` | `farzad11@clnode{248,233,241,230,238,222}.clemson.cloudlab.us` | Author Clemson allocation | Evaluator if they run **without** `--remote`; `--remote` overrides via manifest |
| `configs/alibaba-large/hosts.txt` | `farzad11@c220g2-0108{01,25,04,22,14,11}.wisc.cloudlab.us` | Author Wisconsin allocation | Same as hotel/social |

Test `hosts.txt` dirs: `chain-2`, `chain-2-bimodal`, `dynamic-large`, `fan-out`, `fan-out-3`, `fan-out-4`, `fan-out-dynamic-0-9`, `fan-out-fan-in`, `fan-out-fan-in-heavy`, `ingress-stress`, `intermediate-diverse`, `multi-api`, `one-service`, `pfanout-2`, `pfanout-4`, `leaf-1-2`, `leaf-1-10`, `leaf-1-2-p-2-1`.

Paper path: `run_tests.sh --remote --cloudlab-manifest …` writes `exp_runs_test/<run>/cloudlab_hosts.txt` and passes `--hosts-file`, so hotel/social/alibaba/test `hosts.txt` are **not** used in `--remote` mode.

### B. Docker registry (evaluator)

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| Every bench `build.sh` / `deploy.sh` (21 benches × 2) | `REGISTRY=${REGISTRY:-farzad1132}` | Image name prefix | Evaluator (`REGISTRY` already overrides) |
| 19× `docker-bake.hcl` | `default = "farzad1132"` | bake default if `REGISTRY` unset | Evaluator |
| `benchmarks/k8s/create.sh` | `REGISTRY="${REGISTRY:-farzad1132}"` | cpu-stats image | Evaluator |
| `benchmarks/k8s/cpu-stats-exporter/build.sh` | `DOCKER_REGISTRY:-farzad1132` | Build/push exporter | Evaluator |
| `benchmarks/k8s/cpu-stats-daemonset.yaml` | `image: farzad1132/cpu-stats-exporter:latest` | Manifest literal; `create.sh` already `sed`s it | Evaluator via `REGISTRY` |
| `benchmarks/callgraph-framework/gen/k8s_gen.go` | bake/script default `farzad1132` | Regenerating test benches | Stay-as-author unless regenerating graphs |
| `benchmarks/callgraph-framework/gen/generator.go` | `"farzad1132"` hardcoded | Same | Stay-as-author |

Benches with `REGISTRY`: `alibaba-large`, `hotel`, `social`, and all 18 `benchmarks/tests/*` listed above.

### C. Git remotes / clone URLs (stay-as-author)

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| `.gitmodules` | `git@github.com:farzad1132/rwg.git`, `…/benchmarks.git` | Submodule clone | Stay-as-author: switch to HTTPS if evaluators have HTTPS access |
| `benchmarks/.gitmodules` | `git@github.com:farzad1132/roshanfer-sidecar.git` | Nested sidecar | Same |
| `benchmarks/provisioning/provision.sh` | `git@github.com:farzad1132/roshanfer-experiments.git` | Clone onto CloudLab nodes | Stay-as-author: HTTPS or “repo already present”. URL spelling is already correct |
| sidecar `.gitmodules` (`farzad1132/roshanfer-sidecar`, searched on GitHub) | `git@github.com:farzad1132/rwg.git`; `https://github.com/farzad1132/NanoLog.git` | Nested test/rwg + NanoLog fork | Stay-as-author |

`github.com/farzad1132/rwg` in `rwg/go.mod` and generated protobufs is the Go module path. Leave it. It is not an evaluator setting.

### D. Hardcoded home paths (stay-as-author / leftover)

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| `benchmarks/hotel/exec/utilization_plot.py` shebang | `#!/home/farzad/files/venv/bin/python3` | Leftover hotel util script; not on `run_tests.sh` path | Stay-as-author: `#!/usr/bin/env python3` |
| `benchmarks/hotel/exec/metrics.py` shebang | same | Same | Stay-as-author |
| `benchmarks/hotel/exec/utilization_plot.py` | `sys.path.append('/home/farzad/files/ppm/experiments')` | Same leftover | Stay-as-author |
| `exec/env-setter.py` | sets `remote_microservice_user="farzad"`, host `192.168.1.100`, path `/home/farzad/files/ppm/bench/...` | **Orphan**. Nothing imports it. Those attributes are no longer on `Config` | Stay-as-author: delete or ignore. Do **not** revive in `evaluator.env` |
| `exec/README.md` | `cd ~/files/roshanfer-experiments` | Author machine in a worktree example | Stay-as-author (docs) |

### E. `192.168.1.100` (mostly leftover / fallback)

Orchestrated runs already set `TARGET_ADDR` in `exec/runner.py` to a `nodeN` alias from the hosts file (or the deploy host’s first DNS label). Evaluators on the `run_tests.sh` path do **not** need a control-plane IP in JSON anymore (`prometheus_url` is gone).

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| All `benchmarks/**/run.sh` and `run-plain.sh` | `TARGET_ADDR:-192.168.1.100` | Fallback only if `TARGET_ADDR` unset (manual script, not executor) | Optional evaluator fallback |
| `benchmarks/callgraph-framework/gen/k8s_gen.go` | same default in generated wrappers | Regenerating benches | Stay-as-author |
| `benchmarks/hotel/k6/*.js`, `hotel/k6/run*.sh` | hardcoded `192.168.1.100` | Old k6 drivers; **not** referenced by `exec/` or `configs/` | Stay-as-author leftover |
| `benchmarks/hotel/wrk2/scripts/mixed.lua` | `http://192.168.1.100:2000` | Old wrk2; not on AE path | Stay-as-author leftover |
| `rwg/testgrpcclient/main.go` | `192.168.1.100:3000` | rwg unit test client | Stay-as-author (tests) |

### F. SSH keys, kubeconfig, direnv (already generic)

| File(s) | Current value | Used for | Who sets |
|---|---|---|---|
| `benchmarks/provisioning/provision.sh` | copies `~/.ssh/id_ed25519` or `id_rsa` if present | Node-to-GitHub / node-to-node | Evaluator’s own keys; do not put key material in the central file |
| `.envrc` | `KUBECONFIG=$PWD/benchmarks/k8s/kubeconfig` | Per-clone kubeconfig | Stay-as-author (correct) |
| `run_tests.sh` | refuses to run unless direnv set that `KUBECONFIG` | Isolation | Stay-as-author (correct) |

No author emails appear in tracked source (commit metadata only).

---

## Proposed central file

**Name**: `evaluator.env` at repo root  
**Template**: `evaluator.env.example` (committed)  
**Gitignore**: `evaluator.env` (already listed)

**Format**: POSIX `source`-able assignments. This is the mechanism the repo already uses (`benchmarks/k8s/config.env`, `init_env.sh`, `.envrc`). Do not add a second Python/YAML config loader.

### Keys

| Key | Required? | Meaning |
|---|---|---|
| `EVALUATOR_SSH_USER` | Yes | CloudLab / node SSH user. Feeds `SSH_USER`, `--cloudlab-ssh-user` default |
| `EVALUATOR_CLOUDLAB_MANIFEST` | Yes for `--remote` | Path to portal `manifest.xml` |
| `EVALUATOR_DOCKER_REGISTRY` | Yes if building/pushing | Docker Hub user or `registry/user` prefix. Already matches existing `REGISTRY` |
| `EVALUATOR_LOCAL_USER` | Yes for one-machine tutorial | Local SSH user for `user@localhost` hosts lines |
| `EVALUATOR_TARGET_ADDR` | No | Fallback only when `TARGET_ADDR` is unset (manual `run.sh`) |
| `EVALUATOR_REMOTE_REPO_PATH` | No | Override `~/roshanfer-experiments` on generator nodes |

Do **not** add keys for deleted fields (`prometheus_url`, `remote_microservice_*`, SLOs, `num_generators`, `bench`). `num_generators` stays a CLI flag (`--num-generators`) plus per-bench JSON.

### Full example (`evaluator.env.example`)

See the committed `evaluator.env.example` at repo root. Contents:

```bash
# Copy: cp evaluator.env.example evaluator.env
# Then edit. Do not commit evaluator.env.

EVALUATOR_SSH_USER="your_cloudlab_username"
EVALUATOR_CLOUDLAB_MANIFEST="$HOME/cloudlab-manifest.xml"
EVALUATOR_DOCKER_REGISTRY="your_dockerhub_username"
EVALUATOR_LOCAL_USER="$(whoami)"

# Optional. Executor already sets TARGET_ADDR=nodeN.
# EVALUATOR_TARGET_ADDR="node0"
# EVALUATOR_REMOTE_REPO_PATH="~/roshanfer-experiments"
```

---

## How existing code should consume it (one mechanism)

1. **direnv** (already required by `run_tests.sh`):

```bash
# .envrc
export KUBECONFIG="$PWD/benchmarks/k8s/kubeconfig"
[ -f "$PWD/evaluator.env" ] && source_env evaluator.env
```

2. **`run_tests.sh`** (after `cd` to repo root, before flag defaults finish):

```bash
[ -f ./evaluator.env ] && source ./evaluator.env
CLOUDLAB_SSH_USER="${CLOUDLAB_SSH_USER:-${EVALUATOR_SSH_USER:-}}"
CLOUDLAB_MANIFEST="${CLOUDLAB_MANIFEST:-${EVALUATOR_CLOUDLAB_MANIFEST:-}}"
export REGISTRY="${REGISTRY:-${EVALUATOR_DOCKER_REGISTRY:-}}"
export SSH_USER="${SSH_USER:-${EVALUATOR_SSH_USER:-ubuntu}}"
```

CLI flags still win (`--cloudlab-ssh-user`, `--cloudlab-manifest`).

3. **`benchmarks/k8s/config.env`**: replace the hardcoded user with

```bash
SSH_USER="${SSH_USER:-${EVALUATOR_SSH_USER:-ubuntu}}"
```

`provision.sh` already does `SSH_USER=${SSH_USER:-ubuntu}` after sourcing this file.

4. **Build/deploy**: they already honor `REGISTRY`. Sourcing `evaluator.env` (via direnv or `run_tests.sh`) is enough. Optional later: change the default from `farzad1132` to `"${EVALUATOR_DOCKER_REGISTRY:?set evaluator.env}"` so a missing registry fails closed.

5. **Local tutorial hosts**: either document “overwrite the two lines in `configs/tests/one-service/hosts.txt`” (README already does), or have `run_tests.sh` in local mode synthesize

```text
${EVALUATOR_LOCAL_USER}@localhost
${EVALUATOR_LOCAL_USER}@localhost
```

when `evaluator.env` is present. Do not invent a second hosts-file format.

6. **`TARGET_ADDR`**: leave the executor as-is. Optional one-line fallback in wrappers:

```bash
address="${TARGET_ADDR:-${EVALUATOR_TARGET_ADDR:-node0}}"
```

Do not wire Prometheus/k6/Lua templating for the AE path; those drivers are leftover.

---

## What must not go in `evaluator.env`

- SSH private keys or `authorized_keys` blobs
- CloudLab account passwords
- Docker Hub tokens (`docker login` stays outside the repo)
- GitHub PATs
- Generated kubeconfig
- Experiment knobs: SLOs, loads, `bench`, `tuner`, `num_generators`
- Deleted fields: `prometheus_url`, `remote_microservice_*`

---

## Suggested later work (not this PR)

1. Source `evaluator.env` from `.envrc` and `run_tests.sh`
2. `SSH_USER=${EVALUATOR_SSH_USER:-ubuntu}` in `k8s/config.env`
3. Export `REGISTRY` from `evaluator.env`; optionally fail if unset
4. HTTPS `.gitmodules` (experiments, benchmarks, sidecar, sidecar’s `test/rwg`) if AEC has HTTPS access
5. `provision.sh`: clone HTTPS or skip clone when the repo is already on the node
6. Replace `farzad@localhost` / `fm224@octopus3` in test `hosts.txt` with placeholders
7. Delete or quarantine leftovers: `exec/env-setter.py`, hotel `k6/` / `wrk2/` / `exec/*.py` shebangs
8. Drop `farzad11` from `run_tests.sh --help`

Do not force-push. Do not migrate every caller in this pass.

---

## Open questions

1. HTTPS vs SSH for private submodules (including sidecar + NanoLog fork)?
2. Should local-mode `hosts.txt` be generated from `EVALUATOR_LOCAL_USER`, or only documented?
3. Confirm `exec/env-setter.py` can be deleted.
4. Confirm hotel `k6/` and `wrk2/` are out of the AE critical path (nothing in `exec/` or `configs/` calls them).
