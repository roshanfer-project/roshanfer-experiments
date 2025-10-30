#!/bin/bash

# Multi-API wrapper script for social benchmark
# Usage: $0 PROTOCOL BASE RATE DURATION "API1,API2,..." OUTPUT_DIR

# Check if required arguments are provided
if [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ] || [ -z "$5" ] || [ -z "$6" ]; then
  echo "Error: Missing required arguments"
  echo "Usage: $0 PROTOCOL BASE RATE DURATION \"API1,API2,...\" OUTPUT_DIR"
  exit 1
fi

protocol=$1
BASE=$2
RATE=$3
DURATION=$4
APIS=$5
output_dir=$6
address="192.168.1.100"

# Split APIs by comma
IFS=',' read -ra API_ARRAY <<< "$APIS"

# Array to store background process PIDs
pids=()

# Function to run RWG for a single API
run_single_api() {
    local api=$1
    local output_file="$output_dir/out-$api.csv"
    
    if [ "$protocol" == "grpc" ]; then
        if [ "$api" = "compose-post" ]; then
            echo "API 'compose-post' gRPC not yet implemented"
            return 1
        elif [ "$api" = "read-home-timeline" ]; then
            echo "API 'read-home-timeline' gRPC not yet implemented"
            return 1
        elif [ "$api" = "read-user-timeline" ]; then
            echo "API 'read-user-timeline' gRPC not yet implemented"
            return 1
        else
            echo "Unknown social API: $api"
            return 1
        fi
    else
        if [ "$api" = "compose-post" ]; then
            char="t"
            num="100"
            repeated_char=$(printf "%0.s$char" $(seq 1 $num))
            url="http://$address:3000/compose?text=$repeated_char"
            echo "url: $url"
        elif [ "$api" = "read-home-timeline" ]; then
            url="http://$address:3008/home"
        elif [ "$api" = "read-user-timeline" ]; then
            url="http://$address:3009/user"
        else
            echo "Unknown social API: $api"
            return 1
        fi
        ./rwg/rwg run --url $url -d exp -D 5,$DURATION -r $BASE,$RATE -w 150 -o $output_file
        exit "$?"
    fi
}

# If only one API, run directly
if [ ${#API_ARRAY[@]} -eq 1 ]; then
    run_single_api "${API_ARRAY[0]}"
    exit $?
fi

# For multiple APIs, run them in parallel
echo "Running ${#API_ARRAY[@]} APIs in parallel: ${APIS}"

for api in "${API_ARRAY[@]}"; do
    echo "Starting RWG for API: $api"
    run_single_api "$api" &
    pids+=($!)
done

# Wait for all background processes to complete
exit_code=0
for pid in "${pids[@]}"; do
    wait $pid
    if [ $? -ne 0 ]; then
        echo "Process $pid failed"
        exit_code=1
    fi
done

echo "All APIs completed with exit code: $exit_code"
exit $exit_code
