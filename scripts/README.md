# Scripts

Laptop and control-node helpers: CloudLab SSH/tmux, image builds, and plot regeneration.

## Repository layout

```text
scripts/
├── cloudlab_enter.sh          laptop → control node (SSH + clone + tmux)
├── cloudlab_leave.sh          detach tmux (control → laptop)
├── cloudlab_fetch.sh          laptop ← exp_runs_test from control
├── fetch_manifest.sh          write manifest.xml on the control node
├── pin_k8s_kernel.sh          pin Ubuntu kernel ABI on all manifest hosts
├── elapsed.sh                 sourced helper: print [elapsed] on EXIT
├── config_env.sh              sourced defaults; loads config.env
├── pick_github_ssh_key.sh     pick a normal OpenSSH key for GitHub
├── build.sh                   build and push sidecar + bench images
├── ensure_build_deps.sh       host packages for sidecar Docker bake
├── ensure_rwg.sh              build ./rwg/rwg if the binary is missing
├── regenerate_run_plots.sh    re-plot an existing exp_runs_test run
└── queue_size.py              compute max ingress queue per API
```
