#!/bin/bash

# receive SLO as the first required argument
if [ -z "$1" ]; then
    echo "Error: SLO is required"
    exit 1
fi

SLO=$1

# overall report
./rwg/rwg parse --rwg_output ./tests/one-api-vs-time/out-search-hotel.csv --slo $SLO --version 1 --overall_output ./tests/one-api-vs-time/overall.json --warmup 5

# realtime report
./rwg/rwg parse --rwg_output ./tests/one-api-vs-time/out-search-hotel.csv --slo $SLO --version 1 --realtime_output ./tests/one-api-vs-time/realtime.csv --freq 200

# plot the results
python ./tests/one-api-vs-time/plot.py