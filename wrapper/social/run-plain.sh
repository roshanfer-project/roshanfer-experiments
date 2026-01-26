#!/bin/bash

# Single API wrapper script for social benchmark
# Usage: $0 PROTOCOL BASE RATE DURATION API OUTPUT_DIR

# Check if required arguments are provided
if [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ] || [ -z "$5" ] || [ -z "$6" ]; then
  echo "Error: Missing required arguments"
  echo "Usage: $0 PROTOCOL BASE RATE DURATION API OUTPUT_DIR"
  exit 1
fi

# check if --ignore-errors is provided
if [ "$7" == "--ignore-errors" ]; then
    ignore_errors=true
else
    ignore_errors=false
fi

protocol=$1
BASE=$2
RATE=$3
DURATION=$4
API=$5
output_dir="$6/out-$API.csv"
address="${TARGET_ADDR:-192.168.1.100}"

if [ "$protocol" == "grpc" ]; then
    echo "GRPC is not supported"
    exit 1
else
    if [ "$API" = "compose-post" ]; then
        char="t"
        num="100"
        repeated_char=$(printf "%0.s$char" $(seq 1 $num))
        url="http://$address:3000/compose?text=$repeated_char"
        echo "url: $url"
    elif [ "$API" = "read-home-timeline" ]; then
        url="http://$address:3000/home"
    elif [ "$API" = "read-user-timeline" ]; then
        url="http://$address:3000/user"
    else
        echo "Unknown social API: $API"
        exit 1
    fi
    if [ "$ignore_errors" = true ]; then
        "$RWG_BINARY" run --url $url -d exp -D 2,$DURATION -r $BASE,$RATE -w 10000 -o $output_dir -t 30 --ignore-errors
        exit 0
    else
        "$RWG_BINARY" run --url $url -d exp -D 2,$DURATION -r $BASE,$RATE -w 10000 -o $output_dir -t 30
        exit "$?"
    fi
fi
