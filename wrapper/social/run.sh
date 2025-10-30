#!/bin/bash

# Single API wrapper script for social benchmark
# Usage: $0 PROTOCOL BASE RATE DURATION API OUTPUT_DIR

# Check if required arguments are provided
if [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ] || [ -z "$5" ] || [ -z "$6" ]; then
  echo "Error: Missing required arguments"
  echo "Usage: $0 PROTOCOL BASE RATE DURATION API OUTPUT_DIR"
  exit 1
fi

protocol=$1
BASE=$2
RATE=$3
DURATION=$4
API=$5
output_dir="$6/out-$API.csv"
address="192.168.1.100"

if [ "$protocol" == "grpc" ]; then
    if [ "$API" = "compose-post" ]; then
        # Add gRPC configuration for compose-post when available
        echo "API 'compose-post' gRPC not yet implemented"
        exit 1
    elif [ "$API" = "read-home-timeline" ]; then
        # Add gRPC configuration for read-home-timeline when available
        echo "API 'read-home-timeline' gRPC not yet implemented"
        exit 1
    elif [ "$API" = "read-user-timeline" ]; then
        # Add gRPC configuration for read-user-timeline when available
        echo "API 'read-user-timeline' gRPC not yet implemented"
        exit 1
    else
        echo "Unknown social API: $API"
        exit 1
    fi
else
    if [ "$API" = "compose-post" ]; then
        char="t"
        num="100"
        repeated_char=$(printf "%0.s$char" $(seq 1 $num))
        url="http://$address:3000/compose?text=$repeated_char"
        echo "url: $url"
    elif [ "$API" = "read-home-timeline" ]; then
        url="http://$address:3008/home"
    elif [ "$API" = "read-user-timeline" ]; then
        url="http://$address:3009/user"
    else
        echo "Unknown social API: $API"
        exit 1
    fi
    ./rwg/rwg run --url $url -d exp -D 5,$DURATION -r $BASE,$RATE -w 150 -o $output_dir
    exit "$?"
fi
