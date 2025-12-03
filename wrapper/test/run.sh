#!/bin/bash

# Check if required arguments are provided
if [ -z "$2" ] || [ -z "$3" ] || [ -z "$4" ] || [ -z "$5" ] || [ -z "$6" ]; then
  echo "Error: Missing required arguments"
  echo "Usage: $0 [grpc] BASE RATE DURATION API OUTPUT_DIR"
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
    echo "GRPC is not supported for test API"
    exit "$?"
else
    if [ "$API" = "app" ]; then
        url="http://$address:3000/app"
    else
        echo "Unknown test API: $API"
        exit 1
    fi
    ./rwg/rwg run --url $url -d exp -D 2,$DURATION -r $BASE,$RATE -w 5000 -o $output_dir -t 15
    exit "$?"
fi