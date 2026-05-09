# Queue Sizing Optimization — Design Notes

## Problem Statement

A web service exposes **N APIs**, each with:
- Its own **ingress queue** that buffers requests when the server is at capacity
- A **p99 latency SLO** that must not be violated end-to-end (queue wait + service time)

The goal is to determine **q\*_i** — the maximum safe ingress queue size for each API — such that no request waiting in the queue will breach its SLO.

---

## Model

### Variables & Parameters

| Symbol   | Meaning |
|----------|---------|
| `s_i`    | Service time of API i (time spent inside the server) |
| `c_i`    | Max concurrency of API i (may be integer slots or a fractional **effective** max concurrency / parallelism) |
| `slo_i`  | p99 latency SLO for API i (end-to-end budget: queue wait + service time) |
| `w_i`    | Worst-case waiting time at the ingress queue |
| `q_i`    | Ingress queue size (the decision variable) |
| `q*_i`   | Maximum valid queue size (the answer) |

### Waiting Time Formula

When a request arrives at the ingress and `q_i` requests are already ahead of it, the worst-case wait is:

```
w_i(q) = q * (s_i / c_i)
```

**Intuition:** Each of the `c_i` server slots completes a request every `s_i` seconds on average. So the server "drains" one queue slot every `s_i / c_i` seconds. A request at position `q` in the queue waits approximately `q` drain cycles. If `c_i` is fractional, use the same formula: effective drain rate is `c_i / s_i` requests per second.

### SLO Constraint

The total latency (wait + service) must be strictly under the SLO:

```
w_i(q) + s_i < slo_i
q * (s_i / c_i) + s_i < slo_i
```

### Closed-Form Solution

Solving for `q`:

```
q < (slo_i - s_i) * c_i / s_i
```

Therefore:

```
q*_i = floor((slo_i - s_i) * c_i / s_i)
```

With a strict-inequality edge case: if `(slo_i - s_i) * c_i / s_i` is exactly a whole number, `floor` would land on the boundary and violate the strict `<`. In that case, subtract 1.

No optimizer needed — this is a direct algebraic solve.

---

## Thought Process

### Why no optimizer?

The constraint is a single linear inequality in one variable (`q_i`) per API, and APIs are currently treated as independent. The feasible set is `[0, q*_i)` and the objective is to maximize `q_i` within it — which is just the floor of the upper bound. An LP or solver would be overkill here and would obscure the structure.

### Why model waiting time as `q * (s_i / c_i)`?

This is a **steady-state, worst-case approximation** inspired by simple queueing intuition:
- The server has `c_i` slots, each cycling with period `s_i`
- The effective throughput rate is `c_i / s_i` requests/second
- A request at queue position `q` therefore waits `q / (c_i / s_i) = q * s_i / c_i`

This is not a rigorous M/M/c derivation — it assumes all in-flight requests arrive simultaneously and have identical service times. It gives a **conservative (pessimistic) upper bound** on wait time, which is appropriate for SLO enforcement.

### Why is q\*_i an upper bound even in the multi-API case?

In the single-API case, API i gets full concurrency `c_i` and full service time `s_i` as inputs. In the multi-API case, if other APIs are competing:
- `c_i` could be lower (less server capacity allocated to API i)
- `s_i` could be higher (resource contention slows processing)

Both effects increase `w_i`, meaning the single-API `q*_i` is already the **most generous** queue size that is ever safe. It serves as a hard ceiling regardless of multi-API interactions.

---

## Corner Cases

### 1. Infeasible SLO (`slo_i <= s_i`)

If the SLO is smaller than or equal to the service time, no request can meet the SLO even with an empty queue. The only correct response is to reject the configuration as infeasible — no valid queue size exists.

```
wait_budget = slo_i - s_i <= 0  →  INFEASIBLE
```

### 2. Exact Integer Boundary

If `(slo_i - s_i) * c_i / s_i` is exactly an integer `k`, then `q = k` would produce `w_i + s_i = slo_i`, violating the **strict** inequality. The correct answer is `k - 1`.

This is handled explicitly in code since floating-point arithmetic can mask it — an exact integer result should be checked with care.

### 3. q\*_i = 0

Occurs when the wait budget is positive but smaller than one drain cycle (`s_i / c_i`). The queue must be kept empty; any queued request would breach the SLO. This is a valid (not infeasible) result — requests arriving when the server is full should be rejected immediately (fail-fast).

### 4. Very Large q\*_i

When `c_i` is large or `s_i` is small relative to the SLO budget, `q*_i` can be very large. This is mathematically correct but operationally worth flagging: a huge queue can mask load problems and cause latency spikes that appear fine on average but hurt p99 in bursty traffic. A practical system may want to cap queue sizes independently of the SLO math.

### 5. Floating-Point Precision

`s_i`, `c_i`, and `slo_i` are real-valued inputs. The formula involves division and floor, so floating-point rounding errors near integer boundaries can cause off-by-one errors. Mitigations: use `math.floor` with an epsilon tolerance check, or work in integer milliseconds internally.

---

## Results (Example Runs)

### Example 1 — Single API

| Parameter | Value |
|-----------|-------|
| `s_i` | 0.2s |
| `c_i` | 50 |
| `slo_i` | 1.0s |
| wait budget | 0.8s |
| **q\*_i** | **199** |
| total latency at q\* | 0.796 + 0.2 = 0.996s < 1.0s ✓ |

### Example 2 — Two Independent APIs

| API | `s_i` | `c_i` | `slo_i` | **q\*_i** |
|-----|-------|-------|---------|-----------|
| search | 0.2s | 50 | 1.0s | **199** |
| recommend | 0.5s | 20 | 2.0s | **59** |

Server capacity = 100; sum(c_i) = 70 → within limit.

### Example 3 — Tight / Infeasible SLO

| API | `s_i` | `c_i` | `slo_i` | **q\*_i** |
|-----|-------|-------|---------|-----------|
| realtime | 0.9s | 10 | 1.0s | **1** (only 100ms budget) |
| batch | 1.5s | 5 | 1.0s | **INFEASIBLE** |

### Example 4 — Capacity Warning

Sum of `c_i` = 140 > server capacity of 100 → warning emitted. Queue sizes are still computed (since `c_i` values are taken as given), but the user is alerted that the concurrency inputs are inconsistent with server limits.

---

## Assumptions & Limitations

- **Homogeneous service time:** All requests to API i take exactly `s_i` seconds. In practice, service time is a distribution; using p99 service time as `s_i` would give a more conservative bound.
- **Steady-state / worst-case:** The wait model assumes the server is always at full capacity when the queue is non-empty. It does not model transient arrivals or bursty traffic.
- **No head-of-line blocking:** Requests are assumed to enter the server in FIFO order with no priority differentiation.
- **Independent APIs:** Currently, `c_i` values are provided independently. Coupling via a shared server capacity `C` is noted but not yet enforced in the optimizer.
- **No admission control beyond queue cap:** The model only sizes the queue. It does not prescribe what to do when the queue is full (e.g., reject with 429, shed load, etc.).

---

## Future Directions

### 1. Coupled Multi-API Optimization (next natural step)

Introduce a shared server capacity `C` such that `Σ c_i ≤ C`. Then `c_i` becomes a decision variable (concurrency allocation), and the problem becomes:

```
maximize   Σ q*_i(c_i)          (or some utility function)
subject to Σ c_i ≤ C
           c_i > 0  for all i
```

Since `q*_i = floor((slo_i - s_i) * c_i / s_i)`, ignoring the floor this is a **linear program** in `c_i` — trivially solvable. With the floor it becomes an integer program, but the LP relaxation solution (proportional allocation) is likely a tight approximation.

### 2. Stochastic Service Times

Replace fixed `s_i` with a distribution (e.g., log-normal). Use the p99 of service time as `s_i` for a conservative bound, or derive the wait time distribution analytically using M/G/c queueing theory for a tighter bound.

### 3. Traffic-Aware Queue Sizing

Incorporate arrival rate `λ_i`. Under light load the server is not always at capacity, so the effective wait is lower. This allows larger queues under low traffic and tighter enforcement at high traffic — a dynamic sizing policy.

### 4. Priority / Weighted Fairness

If some APIs are higher priority, concurrency can be allocated with weights rather than independently. The SLO constraint structure stays the same; only the allocation rule changes.

### 5. Empirical Calibration

Plug in measured p99 service times from production traces instead of static inputs. The formula then becomes a live capacity planning tool that reacts to observed performance changes.

### 6. Reject vs. Queue Policy

At `q*_i`, the queue is full. Future work should specify the rejection policy: immediate 429, retry with backoff, shed to a fallback, etc. The queue size and rejection policy together determine the user-visible error rate.