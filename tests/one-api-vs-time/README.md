# Run from root

Running the workload generator (from the repo root). The hotel wrapper expects **`RWG_RATES`**, **`RWG_DURATIONS`**, and **`RWG_BINARY`**; arguments are **`protocol`**, **`api`**, **`output_dir`** (see **`benchmarks/hotel/run.sh`**).

```bash
export RWG_BINARY=./rwg/rwg
# Example: 3000 rps for 2s, then 8000 rps for 10s (matches old two-phase shape)
RWG_RATES=3000,8000 RWG_DURATIONS=2,10 ./benchmarks/hotel/run.sh http search-hotel ./tests/one-api-vs-time
```

Parse the overall results:

```bash
./rwg/rwg parse --rwg_output ./tests/one-api-vs-time/out-hotel-search.csv --slo 60 --version 2 --overall_output ./tests/one-api-vs-time/overall.json --warmup 5
```

Parse the realtime output:

```bash
./rwg/rwg parse --rwg_output ./tests/one-api-vs-time/out-hotel-search.csv --slo 60 --version 2 --realtime_output ./tests/one-api-vs-time/realtime.csv --freq 200
```


Plot the results (make sure the virtual environment is active):

```bash
python -m tests.one-api-vs-time.plot
```