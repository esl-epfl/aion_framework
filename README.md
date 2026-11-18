# AION framework

**AI-Optimized Netlist-to-Layout**, for any design: extend the PDK's
standard-cell library with cells that design needs, have an agent draw their
layout, harden the design with those cells, and measure the result against
the same design built from PDK cells only.

The library is extended along two paths:

* **function-driven**: rank the logic functions the synthesized netlist
  computes (k-input cuts grouped by NPN class), design new or improved cells
  for the best ones, and re-synthesize so the mapper can use them;
* **pattern-driven**: mine the mapped netlist for recurring clusters of
  cells, merge each selected cluster into one transistor-level cell, and
  rewrite the netlist with it, formally proven equivalent.

Cells from both paths go through the same cell pipeline: transistor netlist
and exhaustive proof, an LLM-drawn layout gated by DRC/LVS/routability, then
characterization and comparison against the PDK cells they replace. The model
draws, the tools decide.

The tools live in the [`aion_flow`](aion_flow/) submodule (aion_opt,
aion_char, aion_minimizer, aion_layout_claude). This repository is the driver:
it knows nothing about any one design, and every design is a directory under
`designs/`.

```
Makefile              the front door: `make help`
aion.toml             framework settings (container, default PDK/agent/tag)
aion/                 the flow handler (python -m aion)
  steps/              one module per flow step
  agent/              agent profiles, the turn runner, the draw loop
  ext/                the function-driven path: function ranking, extension cells
  tools/              helpers: LibreLane cells, registry, Liberty merge, reports
agents/<name>/        drawing-agent profiles: agent.toml, prompts/, skills/, ...
designs/<name>/       designs: design.toml, src/, tb/, constraints/, librelane/
pdks/<name>/          what the flow needs to know about a process
templates/            what `make new-design` / `make ext-new` start from
tests/                host-only self-tests (`make test`)
aion_flow/            submodule: the tools

runs/<design>/<tag>/  every derived artifact                   (git-ignored)
library/<pdk>/        drawn cells, shared by all designs        (git-ignored)
```

## Quick start

```bash
source <conda env with yosys, ghdl/iverilog/verilator, cocotb, klayout>
docker start iic-osic-tools_shell_uid_1000      # must mount this checkout, see below

make new-design DESIGN=alu LANG=vhdl            # or verilog, systemverilog
make sim        DESIGN=alu                      # RTL testbench
make flow       DESIGN=alu                      # all nine steps
make status     DESIGN=alu                      # is the run coherent?
make artifacts  DESIGN=alu TAG=main             # what it produced, what the agent cost
```

Five designs ship with the framework (`make designs` lists them):

| Design | What | Language |
|--------|------|----------|
| `mac8_vhdl` | 8x8 multiply-accumulate: the small worked example | VHDL |
| `mac8_sv` | the same MAC | SystemVerilog |
| `sha256` | secworks SHA-224/256 core, register interface | Verilog |
| `cve2` | CVE2 (CV32E20) RISC-V core, no memories | SystemVerilog |
| `posit_alu` | the AION Tiny Tapeout chip's Posit ALU (`tt_um_aion`) | VHDL |

## A design

```
designs/<name>/
  design.toml              top, language, sources, testbench, agent, flow knobs
  src/rtl/                 RTL: VHDL, Verilog or SystemVerilog
  tb/test_<top>.py         cocotb testbench -- runs on the RTL and on every netlist
  constraints/<top>.sdc    timing constraints (PnR and signoff)
  librelane/config.json    LibreLane template + per-mode overlays (synth/pnr/baseline/lenient)
  pdk_extension/           hand-designed cells for this design      (git-ignored)
```

`make new-design DESIGN=<n> LANG=<l> [TOP=] [CLOCK=clk] [PERIOD=20] [DIE=150,150] [AGENT=]`
writes all of it from `templates/design/`, with a small registered datapath so
the flow runs out of the box. The SDC and the LibreLane template are what the
flow uses unless `design.toml` points elsewhere. For every run the flow writes
`runs/<d>/<tag>/librelane/<mode>/config.json` from that template, adding:

* the sources under the key the language needs (`VHDL_FILES` with VHDLClassic,
  `VERILOG_FILES` with Classic, plus `USE_SLANG` for SystemVerilog),
* the SDC, the pin order or a DEF template,
* `CELL_LIBS` for synthesis (the PDK library plus this design's extension cells),
* `EXTRA_*` views for PnR (the drawn cells the netlist instantiates).

## The flow

| # | Step | What it does | Where |
|---|------|--------------|-------|
| 1 | `1_synth` | RTL → netlist (PDK + this design's function-driven cells) + pre-PnR STA, post-synthesis simulation | container |
| 2 | `2_pattern_extraction` | pattern mining: recurring clusters, emit a cell library, keep `ELITE_COUNT` | host |
| 3 | `3_rewrite` | substitute the merged cells, **LEC**, re-simulate | host + container |
| 4 | `4_characterization` | exhaustive SV + SPICE testbenches per cell | container |
| 5 | `5_gate_minimization` | each cell as one CMOS stack, proved in SPICE | container |
| 6 | `6_layout_drawing` | **the agent** draws each cell; DRC, LVS, PEX, characterize, compare, publish | host + container |
| 7 | `7_pnr` | harden the rewritten netlist with the drawn cells, SDF simulation | container |
| 8 | `8_render` | the die, and where the AION cells landed | host |
| 9 | `9_report` | against the PDK-only baseline (`make baseline`) | host (+ container to build the baseline) |

The nine steps are the **pattern-driven path** plus implementation. The
**function-driven path** runs before them, through the `ext-*` targets
([below](#the-function-driven-path-pdk-extension)): once a design has
published extension cells, step 1 synthesizes against the extended library.

`make flow DESIGN=<d> STEP=3`, `STEP=2..5`, `STEP=6-`. Every step reads only
what it declares and writes only under its own directory; `coherence.json`
records digests of both, so re-running an early step shows the later ones as
`STALE` in `make status`.

### Settings

One value per knob, later layers win:

1. defaults (`aion/config.py`)
2. `aion.toml` / `aion.local.toml` → `[flow]`
3. `designs/<d>/design.toml` → `[flow]`
4. the command line → `SET="ELITE_COUNT=4 DRAW_JOBS=2"`

`make config DESIGN=<d>` prints every setting, its value and the layer that
set it, plus the agent profile that will be used. Values are type-checked and
choices validated: `SET=DRAW_MODE=Auto` is an error, not a silent fallback.

### Other targets

| Target | What it does |
|--------|--------------|
| `make designs` / `make steps` | list the designs / the nine steps |
| `make sim DESIGN=<d> STAGE=rtl\|synth\|rewrite\|pnr\|baseline [WAVES=1]` | the testbench on one view |
| `make baseline DESIGN=<d>` | harden the PDK-only control into `runs/<d>/<tag>/baseline/` |
| `make test` | framework self-tests (host only, no container) |
| `make test-layout-tool` | the layout tool's own host-side suite |

`make help` lists every target with its variables.

## Agents

Step 6, and `make ext-build`, are the only places a model works. What it is
given lives in one directory per profile:

```
agents/<name>/
  agent.toml        backend, model, effort, turns, timeout, cost caps, tools, extends=
  prompts/          layout.md (a pattern-driven cell), ext_layout.md (a function-driven cell)
  skills/<skill>/   Claude Code skills installed into the session (aion-layout)
  settings.json     Claude Code settings: the post-edit cell-check hook
  CLAUDE.md         standing instructions
```

`extends = "<other>"` inherits another profile's TOML and every file it does
not have itself, so `claude-sonnet` and `claude-opus-max` are a few lines each.

**Selecting one.** Most specific wins: `AGENT=<profile>` on the command line,
then `[layout] agent` in the design, then `[defaults] agent` in `aion.toml`.
`AGENT_SET="model=claude-sonnet-5-5 effort=max max_usd_per_cell=20"` overrides
single keys for one run. `make agents`, `make agent AGENT=<p>`.

**What the agent can and cannot do.** Each session runs in the profile
materialized into the run (`runs/<d>/<tag>/agent/<profile>/`) with
`--setting-sources project`: its own skills, hooks and CLAUDE.md, nothing from
`~/.claude`. It may edit exactly one file, the cell's generator. Every graded
step (DRC, LVS, PEX, characterization, comparison, export) runs on the host,
and the verdict is the `RESULT:` line of `make verify`, never the agent's own
report.

**Tracking.** Every session is recorded with profile, profile digest, model,
session id, turns, cost and duration:

* `runs/<d>/<tag>/agent/usage.jsonl`: one line per session, the whole run
* `runs/<d>/<tag>/6_layout_drawing/<CELL>/agent.json`: per cell
* `runs/<d>/<tag>/run.json`: the profile the run used, with overrides
* `library/<pdk>/cells/<CELL>/provenance.json`: on every published cell, so
  `make cells` can say who drew what, in which design, and at what cost

## Artifacts

| What | Where | Tracked |
|------|-------|---------|
| authored inputs | `designs/`, `agents/`, `pdks/`, `templates/` | yes |
| one run | `runs/<design>/<tag>/` (`TAG=` picks another) | no |
| drawn pattern-driven cells (generators, views, registry) | `library/<pdk>/`: shared by every design | no |
| function-driven (extension) cells | `designs/<d>/pdk_extension/`: one design only | no (`git add -f`) |

```
runs/<design>/<tag>/
  run.json  coherence.json        what this run is, and is it coherent
  1_synth/ … 9_report/            one directory per step
  baseline/                       the PDK-only control
  librelane/{synth,pnr,baseline}/ LibreLane's own run directories
  sim/<stage>/                    every testbench run
  pdk_extension/                  this run's extended Liberty + tech dictionary
  explore/                        `make ext-rank` / `make explore` work and results
  agent/                          the profile as used, and usage.jsonl
  logs/<step>/                    one log per command
```

```bash
make artifacts DESIGN=<d>                     # every run: size, steps, agent, cost
make artifacts DESIGN=<d> TAG=<t>             # one run in detail
make clean-run DESIGN=<d> TAG=<t> [STEP=6] YES=1
make gc        DESIGN=<d> KEEP=3 YES=1        # all but the 3 newest runs
make cells                                    # the shared library, with provenance
```

Nothing deletes without `YES=1`. Nothing in `clean-run`/`gc` touches
`library/` or a design's `pdk_extension/`.

## The function-driven path (PDK extension)

The pattern-driven path (steps 2–3) is structural: it merges cells that
already sit next to each other, so a function the mapper built in two shapes
shows up as two unrelated clusters. The function-driven path ranks the
**functions** themselves: every gate's k-input cuts (k ≤ 4), their truth
tables grouped by NPN class, each occurrence priced by the area of the
fanout-free cone it would free. Classes on the same leaves are ranked jointly,
so a full adder (XOR3 + MAJ3) shows up as one multi-output candidate. The
cells are selected by hand from the ranking and fall in two categories:

* **(i) new**: a function with no PDK counterpart (e.g. an inverting MAJ3),
  compared against the abutted PDK cells that build it;
* **(ii) improved**: a function the PDK has but the mapper avoids (e.g. MUX2
  built from generic gates), redesigned in another logic style
  (transmission-gate, pass-transistor) and compared against that PDK cell.

They are authored per design, and their instances stay leaves when step 2
mines the re-synthesized netlist:

```bash
make ext-rank  DESIGN=<d> ARGS="--new-only --explain 3"  # rank the candidates
make ext-new   DESIGN=<d> CELL=AION_maj3i_1               # authored skeleton
$EDITOR designs/<d>/pdk_extension/AION_maj3i_1/           # .v .spice .provisional.lib FLOORPLAN.md
make ext-build DESIGN=<d> CELL=AION_maj3i_1 AGENT=claude-opus-max
make flow      DESIGN=<d>                                 # synthesis now maps onto it
```

`ext-build` is the cell pipeline for one cell: the 3-way SPICE proof,
the shared draw loop, post-layout characterization, and it publishes the views
next to the sources once the cell wins its comparison (`ACCEPT_DELAY_LOSS=<pct>` for a smaller-but-slower cell).
`FROM=layout|post-layout` resumes a build part-way, `ONLY=publish` re-publishes
the last verdict. `make ext-lib DESIGN=<d>` splices the design's extension
cells into its run's Liberty and tech dictionary (step 1 does this by itself;
`EXT_PROVISIONAL=1` maps cells that are not drawn yet on their provisional
Liberty). `make explore DESIGN=<d> ARGS="sweep --budget 10"` sweeps the mining
knobs.

## The container

LibreLane, ngspice, magic, netgen, klayout and kepler-formal run in
IIC-OSIC-TOOLS. The container must see **this checkout** at the path
`aion.toml` names (`/foss/designs/aion_framework` by default):

```bash
docker run ... -v $(pwd):/foss/designs/aion_framework ... hpretl/iic-osic-tools
```

or put the path it already has in `aion.local.toml`:

```toml
[container]
name = "my-container"
mount = "/foss/designs/aion"
```

The flow checks both the container and the mount before the first
containerised step, and says which one is missing.
