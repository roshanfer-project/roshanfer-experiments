#!/bin/python3
"""
Queue Sizing Optimizer
======================
Determines the maximum allowed ingress queue size (q*_i) for each API
such that the end-to-end latency (wait time + service time) stays within SLO.

Model
-----
For API i with:
  s_i   = service time (seconds)       -- time spent inside the server
  c_i   = max concurrency (positive real) -- effective parallelism / slots
  slo_i = p99 latency SLO (seconds)   -- end-to-end latency budget

The waiting time for a request at the head of the queue is:
  w_i(q) = q * (s_i / c_i)

This comes from a simple worst-case model: if q requests are ahead in the
queue, each slot in the server takes s_i/c_i seconds to free up on average,
so the request waits q * (s_i/c_i) before entering.

The SLO constraint is:
  w_i(q) + s_i < slo_i
  q * (s_i / c_i) + s_i < slo_i

Solving for q:
  q < (slo_i - s_i) * c_i / s_i

Therefore:
  q*_i = floor((slo_i - s_i) * c_i / s_i)

Constraints
-----------
- slo_i > s_i  (SLO must be larger than service time, otherwise infeasible)
- c_i > 0, s_i > 0, slo_i > 0  (reals; c_i may be fractional)
- Sum of c_i <= server_capacity  (not enforced here yet, user-provided)
"""

import math
from dataclasses import dataclass


@dataclass
class API:
    name: str
    service_time: float   # s_i in seconds
    concurrency: float    # c_i effective max concurrency (may be fractional)
    slo: float            # slo_i in seconds (p99 latency budget)


@dataclass
class QueueResult:
    api_name: str
    service_time: float
    concurrency: float
    slo: float
    max_wait_budget: float   # slo - s_i
    q_star: int              # maximum allowed queue size
    feasible: bool           # False if slo <= service_time
    note: str = ""


def compute_q_star(api: API) -> QueueResult:
    """
    Solves: q * (s / c) + s < slo  =>  q < (slo - s) * c / s
    Returns q*_i = floor((slo - s) * c / s)
    """
    s = api.service_time
    c = api.concurrency
    slo = api.slo

    wait_budget = slo - s

    if wait_budget <= 0:
        return QueueResult(
            api_name=api.name,
            service_time=s,
            concurrency=c,
            slo=slo,
            max_wait_budget=wait_budget,
            q_star=0,
            feasible=False,
            note="INFEASIBLE: SLO must be strictly greater than service time.",
        )

    # q < (slo - s) * c / s  =>  q*_i = floor((slo - s) * c / s)
    q_exact = wait_budget * c / s
    q_star = math.floor(q_exact)

    # Edge case: if floor lands exactly on the boundary, it would violate
    # strict inequality — floor already handles this since q < q_exact means
    # q_star = floor(q_exact) is valid when q_exact is not an integer,
    # and floor(q_exact) = q_exact - 1 would be needed when q_exact is integer.
    if q_exact == q_star:  # q_exact is a whole number => strict < not satisfied
        q_star -= 1

    note = ""
    if q_star < 0:
        note = "WARNING: No valid queue size exists (SLO is too tight relative to service time)."

    return QueueResult(
        api_name=api.name,
        service_time=s,
        concurrency=c,
        slo=slo,
        max_wait_budget=wait_budget,
        q_star=max(q_star, 0),  # clamp to 0; negative means reject at ingress
        feasible=q_star >= 0,
        note=note,
    )


def print_result(r: QueueResult):
    print(f"\n  API: {r.api_name}")
    print(f"    service_time (s_i) = {r.service_time}s")
    print(f"    concurrency  (c_i) = {r.concurrency}")
    print(f"    SLO          (slo) = {r.slo}s")
    print(f"    wait budget        = slo - s_i = {r.max_wait_budget:.4f}s")
    if r.feasible:
        actual_wait = r.q_star * (r.service_time / r.concurrency)
        print(f"    q*_i               = {r.q_star}  (wait at q*: {actual_wait:.4f}s, total: {actual_wait + r.service_time:.4f}s < {r.slo}s ✓)")
    else:
        print(f"    q*_i               = INFEASIBLE")
    if r.note:
        print(f"    NOTE: {r.note}")


def solve(apis: list[API], server_capacity: int | None = None) -> list[QueueResult]:
    """
    Compute q*_i for each API independently.
    Optionally validates that sum(c_i) <= server_capacity.
    """
    results = []

    if server_capacity is not None:
        total_c = sum(a.concurrency for a in apis)
        print(f"Server capacity check: sum(c_i) = {total_c}, limit = {server_capacity}")
        if total_c > server_capacity:
            print(f"  WARNING: sum of concurrency values ({total_c}) exceeds server capacity ({server_capacity}).")
        else:
            print(f"  OK: concurrency allocation is within server capacity.")

    print("\n--- Queue Sizing Results ---")
    for api in apis:
        result = compute_q_star(api)
        results.append(result)
        print_result(result)

    return results


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    """ print("=" * 60)
    print("EXAMPLE 1: Single API")
    print("=" * 60)
    solve([
        API(name="search", service_time=0.2, concurrency=50, slo=1.0),
    ]) """

    print("\n" + "=" * 60)
    print("one-service benchmark with 3 APIs")
    print("=" * 60)
    solve(
        apis=[
            API(name="f1",    service_time=4, concurrency=1.6, slo=20),
            API(name="f2", service_time=5.8, concurrency=2.4, slo=30),
            API(name="f3", service_time=5, concurrency=2, slo=35)
        ],
        server_capacity=6,
    )
    print("Real values: f1=5, f2=6, f3=8")

    print("\n" + "=" * 60)
    print("fanout-fanin benchmark with 2 APIs")
    print("=" * 60)
    solve(
        apis=[
            API(name="f1",    service_time=14.7, concurrency=20.8, slo=35),
            API(name="g1", service_time=5, concurrency=6.2, slo=20)
        ],
        server_capacity=27,
    )
    print("Real values: q_f1=14 and q_g1=13")

    """ print("\n" + "=" * 60)
    print("EXAMPLE 3: Tight SLO — feasibility edge case")
    print("=" * 60)
    solve([
        API(name="realtime", service_time=0.9, concurrency=10, slo=1.0),  # only 100ms budget
        API(name="batch",    service_time=1.5, concurrency=5,  slo=1.0),  # infeasible: slo < s
    ])

    print("\n" + "=" * 60)
    print("EXAMPLE 4: Concurrency exceeds server capacity warning")
    print("=" * 60)
    solve(
        apis=[
            API(name="api_a", service_time=0.3, concurrency=80, slo=1.5),
            API(name="api_b", service_time=0.4, concurrency=60, slo=2.0),
        ],
        server_capacity=100,  # 80+60=140 exceeds this
    ) """