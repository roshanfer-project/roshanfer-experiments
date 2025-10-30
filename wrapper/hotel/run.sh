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
    if [ "$API" = "search-hotel" ]; then
        args="lat=37.7867,lon=-122.4112,InDate=2024-08-15,OutDate=2024-08-17"
        proto="protobuf.RajomonClient/SearchHotels"
    elif [ "$API" = "reserve-hotel" ]; then
        echo "API 'reserve-hotel' is not supported"
        exit 1
    fi
    ./rwg/rwg run -u $address -p $proto -d exp -D 5,$DURATION -r $BASE,$RATE -w 40000 -o $output_dir --args $args
    exit "$?"
else
    if [ "$API" = "search-hotel" ]; then
        url="http://$address:3000/hotels?lat=37.7867&lon=-122.4112&inDate=2024-08-15&outDate=2024-08-17"
    elif [ "$API" = "reserve-hotel" ]; then
        url="http://$address:3009/reservation?inDate=2025-05-20&outDate=2025-05-22&hotelId=4&customerName=Alice&username=Cornell_1&password=1111111111&number=1"
    else
        echo "Unknown hotel API: $API"
        exit 1
    fi
    ./rwg/rwg run --url $url -d exp -D 5,$DURATION -r $BASE,$RATE -w 500 -o $output_dir
    exit "$?"
fi