# Overview

This document describes the role of each subdirectory and their interactions.

# rwg
This is a custom workload generator added as a submodule. Therefore, we should not change it's code from here. Any required changes should be reported back to the user.

# exec
This is an automated tetsing and plotting framework. In sumary it does the following:
- Prepares the benchmarks (applications under the test) and runs them
- Runs the `rwg` (through wrapper scripts found in `wrapper`) to send requests to the benchmark
- Collect the raw results of the test and parse it
- Plot the parsed results


# tuner
This module is a parameter tuning tool. It relies on some parts of `exec` (the required files are copied).

# configs
This directory holds config files reuqired to run the `exec` module

