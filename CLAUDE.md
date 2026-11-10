# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`hwcomponents_dsent` is a plug-in for [HWComponents](https://accelergy-project.github.io/hwcomponents/) providing NoC `Router` and `NoCLink` models backed by DSENT. It is a sibling of `hwcomponents-cacti` and deliberately mirrors its structure (subprocess call + content-addressed on-disk cache + tech-node interpolation).

## Build / install

```bash
make build          # compiles all hwcomponents_dsent/dsent/**/*.cc into hwcomponents_dsent/dsent/dsent (objects in .obj/)
make clean
pip install -e .    # setup.py's build hook runs `make build`
hwc --list | grep -iE 'router|link'
```

Python >= 3.12; `hwcomponents` must be importable at build time (setuptools-scm version scheme). No test suite; smoke-test with `python -c "from hwcomponents_dsent import Router; print(Router(tech_node=45e-9, flit_width=64).route_flit())"`.

## Architecture

- **DSENT source** (`hwcomponents_dsent/dsent/`) is gem5's DSENT copy with its Python-2 C extension (`interface.cc`) and CMake file removed. `main.cc` is our standalone driver: `dsent <cfg> <out>` builds the model from the cfg, runs its `EvaluateString`, and writes each `print "name" expr;` result as `name\tvalue` to `<out>`. DSENT `ASSERT`s abort the process, which is why it runs as a subprocess rather than an extension.
- **DSENT builds the model from the cfg once** (parameters, then properties, then optional timing optimization to `1/Frequency`). All structural parameters must be in the cfg; nothing can be changed after build.
- **`_call_dsent`** formats a full cfg from a params dict plus an `evaluate` dict (output name → DSENT calculator expression like `$(Energy>>Router:WriteBuffer)`), hashes it, and caches results in `hwcomponents_dsent/dsent_inputs_outputs/` under an `fcntl` lock. Per-event energies (J), `NddPower` leakage (W), and `Area` (m²) come out in SI units; no conversion is done.
- **`_interp_tech_node`** interpolates between bulk 22/32/45nm tech models (TG11 only with `include_11nm_trigate=True`). Out-of-range scaling matches hwcomponents-cacti's intent. Scaling class is chosen by output-name prefix (`area*` → area, `leak*` → leakage), so name new outputs accordingly.
- **Clock power is leakage**: `Router` folds `DistributeClock` energy × frequency into `leak_power` (`self.clock_power`).
- `ActionCost.throughput` is actions/second: `frequency × n_ports` for router actions, `frequency` for link `send`. Latency is `pipeline_stages / frequency` (router) or `delay` (link, defaults to one cycle and also sets DSENT's repeater-sizing target).
