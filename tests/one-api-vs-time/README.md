# Run from root

Running the workload generator (from the root of the project):

```bash
./wrapper/hotel/run.sh grpc 3000 8000 10 hotel-search ./tests/one-api-vs-time
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