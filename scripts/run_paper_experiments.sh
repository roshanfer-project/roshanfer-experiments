#!/usr/bin/env bash
# Sequential Part 2 paper experiments (Option B when both options exist).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source "$ROOT/scripts/elapsed.sh"

echo "=== Figure 13: Alibaba goodput vs load ==="
./run_tests.sh --remote --num-generators 3 --type latency-and-goodput-vs-load \
  --bench alibaba-large --also-alibaba --comment figure13_all

echo "=== Figure 10: Hotel queueing ==="
./run_tests.sh --remote --num-generators 3 --type max-queue --num-apis 1 \
  --bench hotel --also-hotel-social --comment figure10_all

echo "=== Figure 9: Hotel resource waste ==="
./run_tests.sh --remote --num-generators 3 --type resource-waste --num-apis 1 \
  --bench hotel --also-hotel-social --comment figure9_all

echo "=== Figure 8: Hotel latency and rates over time ==="
./run_tests.sh --remote --num-generators 3 --bench hotel --also-hotel-social \
  --type latency-and-rate-vs-time --num-apis 1 --comment figure8_all

echo "=== Figure 15: overcommitment and priority ==="
./run_tests.sh --remote --num-generators 3 \
  --bench leaf-1-2,leaf-1-10,leaf-1-2-p-2-1 \
  --type throughput-vs-overcommitment --comment figure15

echo "=== Figure 12: dynamic call graph ==="
./run_tests.sh --remote --num-generators 3 --type latency-and-goodput-vs-load \
  --bench dynamic-large --comment figure12_all

echo "=== Figure 11: Social multi-API ==="
./run_tests.sh --remote --num-generators 3 --type latency-and-goodput-vs-load \
  --bench social --also-hotel-social --num-apis 3 --comment figure11_all

echo "=== Figure 14: Hotel overhead ==="
./run_tests.sh --remote --num-generators 3 --type latency-vs-throughput --num-apis 1 \
  --bench hotel --also-hotel-social --comment figure14

echo "=== Figure 7: Hotel and Social goodput vs load ==="
./run_tests.sh --remote --num-generators 3 --type latency-and-goodput-vs-load \
  --bench hotel,social --also-hotel-social --num-apis 1 --comment figure7_all

echo "All Part 2 paper experiments finished."
