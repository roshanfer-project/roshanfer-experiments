from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any, Tuple
from prometheus_api_client import PrometheusConnect
import math

from .config import Config


# Read the goodput and tail-latency and return it from prometheus
def extract_metrics_from_prometheus(path, api :str) -> Tuple[float, float]:
    config = Config()
    prom = PrometheusConnect(url=config.prometheus_url, disable_ssl=True)

    with open(os.path.join(path, "timestamps.csv"), "r") as file:
        timestamps = file.readlines()
        start_timestamp = datetime.fromtimestamp(float(timestamps[0].strip()))
        end_timestamp = datetime.fromtimestamp(float(timestamps[1].strip()))

    final_goodput = -2000
    final_tail = 2000

    ## average goodput
    avg_goodput = prom.custom_query(query=f"ppm_k6_goodput_counter_total{{api=\"{api}\"}}")
    if len(avg_goodput) > 0:
        final_goodput = float(avg_goodput[0]["value"][1])/15
    
    """ ## max_goodput
    max_goodput = prom.custom_query_range(query="rate(ppm_k6_goodput_counter_total{api=\"search-hotel\"}[1s])",
                                          start_time=start_timestamp,
                                          end_time=end_timestamp,
                                          step="50ms")
    if len(max_goodput) > 0:
        # find max value and set it to max_goodput
        max_goodput_final = float(max(max_goodput[0]["values"], key=lambda x: float(x[1]))[1]) """

    ## tail latency
    tail_latency = prom.custom_query_range(query=f"histogram_quantile(0.95, sum(rate(ppm_k6_success_req_latency_milliseconds_bucket{{api=\"{api}\"}}[15s])) by (le))",
                                     start_time=start_timestamp,
                                     end_time=end_timestamp,
                                     step="5s")
    if len(tail_latency) > 0:
        final_tail = float(tail_latency[0]["values"][-1][1])
    
    # check if final tail is nan
    if math.isnan(final_tail):
        final_tail = 2001

    print(f"Final Goodput: {final_goodput}, Final Tail Latency: {final_tail}")

    return final_goodput, final_tail

if __name__ == "__main__":
    extract_metrics_from_prometheus()