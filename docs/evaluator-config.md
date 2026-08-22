# Evaluator Configuration Design

**Context**: EuroSys 2027 artifact evaluation for Roshanfer (#1195). This artifact currently contains author-specific credentials, paths, and hostnames that prevent evaluators from running experiments as-is.

**Goal**: Centralize all evaluator-customizable settings into a single configuration file that evaluators edit once. Scripts and orchestrators read this file transparently.

---

## Complete Inventory of Author-Specific Values

The following table lists every occurrence of author-specific values found in the artifact:

| Category | File(s) | Current Value | Purpose | Who Sets |
|----------|---------|---------------|---------|----------|
| **SSH Username** | `benchmarks/k8s/config.env` | `SSH_USER=fm224` | SSH user for K8s cluster setup | Evaluator |
| **SSH Username** | All `configs/tests/*/hosts.txt` (17 files) | `fm224@octopus3.doc.res.ic.ac.uk` | Imperial College test host for local experiments | Stay-as-author |
| **SSH Username** | All `configs/tests/*/hosts.txt` (17 files) | `farzad@localhost` | Local SSH user for test experiments | Evaluator (if running locally) |
| **SSH Username** | `configs/social/hosts.txt` | `farzad11@clnode*.clemson.cloudlab.us` (6 hosts) | CloudLab SSH user for social benchmark | Evaluator |
| **SSH Username** | `configs/hotel/hosts.txt` | `farzad11@clnode*.clemson.cloudlab.us` (6 hosts) | CloudLab SSH user for hotel benchmark | Evaluator |
| **SSH Username** | `configs/alibaba-large/hosts.txt` | `farzad11@c220g2-*.wisc.cloudlab.us` (6 hosts) | CloudLab SSH user for alibaba benchmark | Evaluator |
| **SSH Username** | `run_tests.sh` (line 79, 226) | `farzad11` (example), `ubuntu` (default) | Example CloudLab SSH user in documentation/code | Evaluator |
| **Remote Path** | `exec/env-setter.py` | `remote_microservice_user = "farzad"` | SSH user for remote microservice host | Stay-as-author (unused) |
| **Remote Path** | `exec/env-setter.py` | `remote_microservice_host = "192.168.1.100"` | Control plane IP for metrics | Evaluator |
| **Remote Path** | `exec/env-setter.py` | `remote_microservice_path = "/home/farzad/files/ppm/bench/{bench}/exec"` | Remote benchmark execution path | Stay-as-author (unused) |
| **Remote Path** | `configs/social/config.social.json` | `remote_microservice_user: "farzad"` | SSH user for social benchmark remote host | Stay-as-author (unused) |
| **Remote Path** | `configs/social/config.social.json` | `remote_microservice_host: "192.168.1.100"` | Control plane IP | Evaluator |
| **Remote Path** | `configs/social/config.social.json` | `remote_microservice_path: "/home/farzad/files/benchmarks/social/exec"` | Remote path for social benchmark | Stay-as-author (unused) |
| **Remote Path** | `configs/hotel/config.hotel.json` | `remote_microservice_user: "farzad"` | SSH user for hotel benchmark remote host | Stay-as-author (unused) |
| **Remote Path** | `configs/hotel/config.hotel.json` | `remote_microservice_host: "192.168.1.100"` | Control plane IP | Evaluator |
| **Remote Path** | `configs/hotel/config.hotel.json` | `remote_microservice_path: "/home/farzad/files/benchmarks/hotel/exec"` | Remote path for hotel benchmark | Stay-as-author (unused) |
| **Hardcoded Path** | `benchmarks/hotel/exec/utilization_plot.py` (shebang) | `#!/home/farzad/files/venv/bin/python3` | Python interpreter path | Stay-as-author (should use `#!/usr/bin/env python3`) |
| **Hardcoded Path** | `benchmarks/hotel/exec/metrics.py` (shebang) | `#!/home/farzad/files/venv/bin/python3` | Python interpreter path | Stay-as-author (should use `#!/usr/bin/env python3`) |
| **Hardcoded Path** | `benchmarks/hotel/exec/utilization_plot.py` (line 12) | `sys.path.append('/home/farzad/files/ppm/experiments')` | Python path for imports | Stay-as-author (needs refactoring) |
| **Docker Registry** | All benchmark `build.sh` files (40+ files) | `REGISTRY=${REGISTRY:-farzad1132}` | Docker Hub username for container images | Evaluator |
| **Docker Registry** | All benchmark `deploy.sh` files (19 files) | `REGISTRY=${REGISTRY:-farzad1132}` | Docker Hub username for deployment | Evaluator |
| **Docker Registry** | All benchmark `docker-bake.hcl` files (17+ files) | `default = "farzad1132"` | Default registry in Docker bake configs | Evaluator |
| **Docker Registry** | `benchmarks/k8s/create.sh` | `REGISTRY="${REGISTRY:-farzad1132}"` | Docker registry for K8s setup | Evaluator |
| **Docker Registry** | `benchmarks/k8s/cpu-stats-exporter/build.sh` | `REGISTRY="${DOCKER_REGISTRY:-farzad1132}"` | Registry for CPU stats exporter | Evaluator |
| **Docker Registry** | `benchmarks/k8s/cpu-stats-daemonset.yaml` | `image: farzad1132/cpu-stats-exporter:latest` | Hardcoded image reference | Evaluator (via templating) |
| **Docker Registry** | `benchmarks/callgraph-framework/gen/k8s_gen.go` | `default = "farzad1132"` | Template generator default registry | Evaluator |
| **Docker Registry** | `benchmarks/callgraph-framework/gen/generator.go` | `"farzad1132"` | Hardcoded in code generator | Evaluator |
| **GitHub SSH URL** | `.gitmodules` | `git@github.com:farzad1132/rwg.git` | SSH URL for rwg submodule | Stay-as-author (needs HTTPS conversion) |
| **GitHub SSH URL** | `.gitmodules` | `git@github.com:farzad1132/benchmarks.git` | SSH URL for benchmarks submodule | Stay-as-author (needs HTTPS conversion) |
| **GitHub SSH URL** | `benchmarks/.gitmodules` | `git@github.com:farzad1132/roshanfer-sidecar.git` | SSH URL for sidecar nested submodule | Stay-as-author (needs HTTPS conversion) |
| **GitHub SSH URL** | `benchmarks/provisioning/provision.sh` | `git@github.com:farzad1132/roshanfer-experments.git` (typo) | Clone URL for provisioning (typo in repo name) | Stay-as-author (fix typo to `roshanfer-experiments`) |
| **GitHub SSH URL** | `benchmarks/k8s/update_repo.sh` | `cd ~/roshanfer-experments` | Path with typo | Stay-as-author (fix typo) |
| **GitHub SSH URL** | `exec/runner.py` | `remote_repo_path = "~/roshanfer-experments"` | Remote repo path with typo | Stay-as-author (fix typo) |
| **GitHub SSH URL** | `exec/README.md` | `cd ~/files/roshanfer-experments` | Documentation path with typo | Stay-as-author (fix typo) |
| **Module Path** | `rwg/go.mod`, `rwg/**/*.go`, `rwg/**/*.proto` | `github.com/farzad1132/rwg` | Go module path | Stay-as-author (correct for private repo) |
| **CloudLab Hosts** | `configs/social/hosts.txt` | `clnode{248,233,241,230,238,222}.clemson.cloudlab.us` | Author's allocated CloudLab nodes | Evaluator (via manifest) |
| **CloudLab Hosts** | `configs/hotel/hosts.txt` | Same as social | Author's allocated CloudLab nodes | Evaluator (via manifest) |
| **CloudLab Hosts** | `configs/alibaba-large/hosts.txt` | `c220g2-01080{1,4,8,9,11,14,22,25}.wisc.cloudlab.us` | Author's allocated CloudLab nodes | Evaluator (via manifest) |
| **Imperial Hosts** | All `configs/tests/*/hosts.txt` | `octopus3.doc.res.ic.ac.uk` | Author's Imperial College host | Stay-as-author (tests only) |
| **IP Address** | All benchmark `run.sh`, `run-plain.sh` files (40+ files) | `TARGET_ADDR:-192.168.1.100` | Default target address for load tests | Evaluator |
| **IP Address** | `configs/social/config.social.json` | `prometheus_url: "http://192.168.1.100:9091"` | Prometheus pushgateway URL | Evaluator |
| **IP Address** | `configs/hotel/config.hotel.json` | `prometheus_url: "http://192.168.1.100:9091"` | Prometheus pushgateway URL | Evaluator |
| **IP Address** | `benchmarks/hotel/k6/script.js` | `http://192.168.1.100:3000` | Hardcoded test endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/http-test.js` | `http://192.168.1.100:3000` | Hardcoded test endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/grpc-test.js` | `192.168.1.100:3000` | Hardcoded gRPC endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/ov-grpc.js` | `192.168.1.100:3000` | Hardcoded gRPC endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/ov-grpc-2-api.js` | `192.168.1.100:3000` | Hardcoded gRPC endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/run.sh` | `K6_OTEL_GRPC_EXPORTER_ENDPOINT="192.168.1.100:4317"` | OTEL endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/k6/run-2-api.sh` | `K6_OTEL_GRPC_EXPORTER_ENDPOINT="192.168.1.100:4317"` | OTEL endpoint | Evaluator (via env var) |
| **IP Address** | `benchmarks/hotel/wrk2/scripts/mixed.lua` | `http://192.168.1.100:2000` | Hardcoded test endpoint | Evaluator (requires script templating) |
| **IP Address** | `rwg/testgrpcclient/main.go` | `192.168.1.100:3000` | Test client endpoint | Evaluator (test code only) |
| **IP Address** | `benchmarks/callgraph-framework/gen/k8s_gen.go` | `TARGET_ADDR:-192.168.1.100` | Template default address | Evaluator |
| **SSH Key Paths** | `benchmarks/provisioning/provision.sh` (lines 88-95) | `~/.ssh/id_ed25519`, `~/.ssh/id_rsa` | Local SSH keys to copy to remote nodes | Evaluator (automatic from ~/.ssh) |
| **Kubeconfig** | `.envrc`, `benchmarks/k8s/create.sh` | `$PWD/benchmarks/k8s/kubeconfig` | Per-clone kubeconfig path (direnv managed) | Stay-as-author (correct design) |

**Notes:**
- **Typo**: `roshanfer-experments` should be `roshanfer-experiments` (not fixed by evaluator config; requires code fix)
- **Unused fields**: `remote_microservice_user`, `remote_microservice_path` appear unused in current orchestration
- **Stay-as-author**: Test configs referencing `octopus3.doc.res.ic.ac.uk` and `farzad@localhost` are for local development; evaluators won't run these
- **SSH keys**: `provision.sh` automatically detects keys in `~/.ssh`; evaluators only need to ensure keys exist

---

## Proposed Central Evaluator Config

**File**: `evaluator.env`  
**Location**: Repository root (same level as `run_tests.sh`)  
**Format**: Shell-sourceable `.env` file (compatible with existing infrastructure)

### Why `.env` format?
1. **Existing mechanism**: Scripts already use `source` for configs (e.g., `benchmarks/k8s/config.env`)
2. **Shell-native**: No new dependencies (Python, YAML parsers)
3. **direnv compatible**: Can be loaded automatically via `.envrc` if needed
4. **Simple**: Key=value format is familiar and easy to edit

### Full Example `evaluator.env`

```bash
# Roshanfer Artifact Evaluator Configuration
# EuroSys 2027 Artifact Evaluation
#
# Instructions:
# 1. Copy this file to the repository root (same directory as run_tests.sh)
# 2. Edit the values below to match your CloudLab/local setup
# 3. Source this file before running experiments: source evaluator.env
# 4. Or add to .envrc for automatic loading: source_env evaluator.env

# =============================================================================
# CloudLab Configuration
# =============================================================================

# Your CloudLab username (used for SSH and parsing manifests)
EVALUATOR_CLOUDLAB_USER="your_cloudlab_username"

# CloudLab manifest path (download from CloudLab portal after instantiation)
# This is passed to --cloudlab-manifest when using --remote mode
EVALUATOR_CLOUDLAB_MANIFEST="$HOME/cloudlab-manifest.xml"

# =============================================================================
# Docker Registry Configuration
# =============================================================================

# Docker Hub username or registry prefix for container images
# If using DockerHub: set to your DockerHub username (e.g., "myuser")
# If using private registry: set to registry URL prefix (e.g., "myregistry.io/myuser")
# Note: You must have push access to this registry
EVALUATOR_DOCKER_REGISTRY="your_dockerhub_username"

# =============================================================================
# Control Node Configuration
# =============================================================================

# The "control" machine IP/hostname where Prometheus, OTEL, and ingress run
# In CloudLab mode: typically the first generator node's IP
# In local mode: typically 192.168.1.100 or your local machine
# This is used for:
# - Prometheus pushgateway (port 9091)
# - OTEL collector (port 4317)
# - K6/wrk2 target endpoints (ports 2000, 3000)
EVALUATOR_CONTROL_IP="192.168.1.100"

# =============================================================================
# SSH Configuration (Optional)
# =============================================================================

# SSH user for Kubernetes cluster setup (K3s installation)
# Default: "ubuntu" (common CloudLab image default)
# Override if your CloudLab profile uses a different user
EVALUATOR_SSH_USER="${EVALUATOR_CLOUDLAB_USER:-ubuntu}"

# SSH key path (optional, auto-detected from ~/.ssh/id_ed25519 or id_rsa)
# EVALUATOR_SSH_KEY="$HOME/.ssh/id_cloudlab"

# =============================================================================
# Advanced (usually no changes needed)
# =============================================================================

# Number of generator nodes (for --remote mode; overridden by --num-generators flag)
# EVALUATOR_NUM_GENERATORS=3

# Image tag for Docker builds (default: "ae" for artifact evaluation)
# EVALUATOR_IMAGE_TAG="ae"
```

---

## How Existing Code Will Consume It

### 1. **Root-level orchestration** (`run_tests.sh`)

**Current state**: Accepts `--cloudlab-ssh-user` and `--cloudlab-manifest` flags

**Proposed change**: Source `evaluator.env` at script start (if exists), use as defaults for flags

```bash
# Near top of run_tests.sh (after shebang and before argument parsing)
if [ -f "$SCRIPT_DIR/evaluator.env" ]; then
    source "$SCRIPT_DIR/evaluator.env"
    CLOUDLAB_SSH_USER="${CLOUDLAB_SSH_USER:-$EVALUATOR_SSH_USER}"
    CLOUDLAB_MANIFEST="${CLOUDLAB_MANIFEST:-$EVALUATOR_CLOUDLAB_MANIFEST}"
fi
```

Evaluators can still override via CLI flags (e.g., `--cloudlab-ssh-user override_user`).

### 2. **Docker build/deploy scripts** (40+ files)

**Current state**: `REGISTRY=${REGISTRY:-farzad1132}`

**Proposed change**: Fall back to `EVALUATOR_DOCKER_REGISTRY` if `REGISTRY` unset

```bash
# In each build.sh and deploy.sh
REGISTRY=${REGISTRY:-${EVALUATOR_DOCKER_REGISTRY:-farzad1132}}
```

Evaluators can set `REGISTRY` directly or rely on `evaluator.env`.

### 3. **Kubernetes config** (`benchmarks/k8s/config.env`)

**Current state**: `SSH_USER=fm224` (hardcoded)

**Proposed change**: Replace with:

```bash
SSH_USER=${SSH_USER:-${EVALUATOR_SSH_USER:-ubuntu}}
```

### 4. **Config JSONs** (`configs/{social,hotel}/config.*.json`)

**Current state**: Hardcoded `"remote_microservice_host": "192.168.1.100"`, `"prometheus_url": "http://192.168.1.100:9091"`

**Proposed change**: Templating or runtime substitution via Python `exec/config.py`:

```python
# In exec/config.py, after loading JSON
if "EVALUATOR_CONTROL_IP" in os.environ:
    control_ip = os.environ["EVALUATOR_CONTROL_IP"]
    config.remote_microservice_host = control_ip
    config.prometheus_url = f"http://{control_ip}:9091"
```

Alternatively, use `envsubst` before loading JSON (requires pre-processing step).

### 5. **Load test scripts** (k6, wrk2, run.sh)

**Current state**: `TARGET_ADDR:-192.168.1.100` in shell scripts; hardcoded IPs in `.js`/`.lua` files

**Proposed change**:
- Shell scripts: `address="${TARGET_ADDR:-${EVALUATOR_CONTROL_IP:-192.168.1.100}}"`
- K6 scripts: Replace hardcoded IPs with `__ENV.EVALUATOR_CONTROL_IP` (K6 reads env vars as `__ENV.*`)
  ```javascript
  const targetIP = __ENV.EVALUATOR_CONTROL_IP || '192.168.1.100';
  const res = http.get(`http://${targetIP}:3000/hotels?...`);
  ```
- Lua scripts: Use shell wrapper to template before running (e.g., `sed` or `envsubst`)

### 6. **Provisioning** (`benchmarks/provisioning/provision.sh`)

**Current state**: Hardcoded repo URL `git@github.com:farzad1132/roshanfer-experments.git`

**No change**: This is author's repository; evaluators clone via HTTPS already. The typo fix is separate.

### 7. **direnv integration** (`.envrc`)

**Current state**: Only sets `KUBECONFIG`

**Proposed addition**:

```bash
export KUBECONFIG="$PWD/benchmarks/k8s/kubeconfig"

# Load evaluator config if present
if [ -f "$PWD/evaluator.env" ]; then
    source_env evaluator.env
fi
```

---

## What Must NOT Go in `evaluator.env`

**Secrets and credentials that evaluators manage separately:**

1. **SSH private keys**: Stored in `~/.ssh/`, referenced by path only (if needed)
2. **CloudLab passwords**: Not used (SSH key-based auth via CloudLab portal)
3. **Docker Hub login tokens**: Managed via `docker login` (outside repo)
4. **GitHub personal access tokens**: Not needed (public read access to private repos granted by author to AEC chairs)
5. **Kubeconfig contents**: Generated per-clone by `benchmarks/k8s/create.sh` (stays in `benchmarks/k8s/kubeconfig`)

**Rationale**: These are either auto-detected (SSH keys), managed by external tools (Docker login), or dynamically generated (kubeconfig).

---

## Migration Steps (Not Implemented in This PR)

This PR provides the **design and inventory only**. Full migration requires:

1. **Create template `evaluator.env.example`** (checked into repo)
2. **Update `run_tests.sh`**: Source `evaluator.env` and use as defaults
3. **Update all `build.sh` / `deploy.sh`**: Add `EVALUATOR_DOCKER_REGISTRY` fallback
4. **Update `benchmarks/k8s/config.env`**: Add `EVALUATOR_SSH_USER` fallback
5. **Update Python configs**: Add runtime substitution for `EVALUATOR_CONTROL_IP`
6. **Template K6 scripts**: Replace hardcoded IPs with `__ENV.EVALUATOR_CONTROL_IP`
7. **Template Lua scripts**: Add shell wrapper with `envsubst`
8. **Update `.envrc`**: Auto-load `evaluator.env`
9. **Fix typo**: Rename `roshanfer-experments` → `roshanfer-experiments` everywhere
10. **Fix shebangs**: Change `/home/farzad/files/venv/bin/python3` → `#!/usr/bin/env python3`
11. **Convert SSH URLs to HTTPS**: Update `.gitmodules` (pending author decision on public/private access)
12. **Update README.md**: Document `evaluator.env` setup in "Getting Started" and "Reproducing Evaluation" sections
13. **Test with clean CloudLab allocation**: Verify evaluator can run with only `evaluator.env` customization

---

## Open Questions for Author

1. **Submodule access**: Should `.gitmodules` use HTTPS URLs for evaluator access, or will AEC chairs grant SSH key access?
2. **Remote microservice fields**: Are `remote_microservice_user` / `remote_microservice_path` used? Can we remove them?
3. **Test configs**: Should `configs/tests/*/hosts.txt` be templated, or documented as "author-only, not for evaluators"?
4. **Hardcoded hotel paths**: Is `sys.path.append('/home/farzad/...')` in `hotel/exec/*.py` safe to remove, or does it need refactoring?
5. **Wrk2 Lua templating**: Prefer shell wrapper or pre-generate multiple `.lua` files per deployment?

---

## Summary

- **72+ occurrences** of author-specific values across 100+ files
- **Centralized solution**: Single `evaluator.env` file (shell-sourceable)
- **Minimal code changes**: Fallback checks (`${VAR:-${EVALUATOR_VAR:-default}}`) preserve existing behavior
- **Evaluator experience**: Edit one file, source it, run experiments
- **Secrets**: Stay out of repo (SSH keys, Docker tokens, CloudLab passwords)

This design prioritizes **simplicity** (reuse existing shell sourcing) and **backward compatibility** (CLI flags override config file).
