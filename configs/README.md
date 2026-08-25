# Configs

A **benchmark** is a pair: a config tree here and a service graph under `benchmarks/`. `run_tests.sh` runs that pair.

Each `configs/<bench>/` directory has `config.json` (which graph, SLOs), `experiments.json` (what to measure), and often `merged.yaml` (how to overlay systems on one plot). `system` in `experiments.json` is `plain`, `roshanfer`, `rajomon`, or `dagor`. Hotel, social, and alibaba use the same trio with slightly different filenames (`config.hotel.json`, `hotel_experiments.json`, …).

## Repository layout

```text
configs/
├── hotel/                     Hotel Reservation (Figs. 7–11, 14)
├── social/                    Social Network (Figs. 7, 9–11)
├── alibaba-large/             Alibaba / DGG 30-MS (Fig. 13)
└── tests/                     synthetic graphs
    ├── one-service/           tutorial
    ├── dynamic-large/         dynamic graphs (Fig. 12)
    ├── fan-out-dynamic-0-9/   dynamic graphs (Fig. 12)
    ├── leaf-1-2/              overcommitment (Fig. 15)
    ├── leaf-1-10/             overcommitment (Fig. 15)
    ├── leaf-1-2-p-2-1/        overcommitment (Fig. 15)
    └── …                     other synthetic graphs (chain, fan-out, …)
```
