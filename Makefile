# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               AION framework -- the front door
#
#  Every target is one `python -m aion <command>` call; the Python owns the
#  logic, this file owns the spelling.  `make help` lists them.
#
#    make new-design DESIGN=alu LANG=vhdl
#    make flow       DESIGN=alu                     the whole flow
#    make flow       DESIGN=alu STEP=6 AGENT=claude-sonnet
#    make status     DESIGN=alu
#    make artifacts  DESIGN=alu [TAG=main]
# ================================================================

PYTHON    ?= python3
AION       = $(PYTHON) -m aion

# ---- what to act on ---------------------------------------------------------
DESIGN    ?=
TAG       ?=
STEP      ?=
# Flow settings for this run, space-separated NAME=VALUE (see `make config`):
#   make flow DESIGN=alu SET="ELITE_COUNT=4 DRAW_JOBS=2"
SET       ?=
# The drawing agent (a directory under agents/), and per-run overrides of
# its agent.toml:  AGENT_SET="model=claude-sonnet-5-5 effort=max"
AGENT     ?=
AGENT_SET ?=
DRAW      ?=
JOBS      ?=
LENIENT   ?=
DRY_RUN   ?=
QUIET     ?=
FORCE     ?=
YES       ?=
ARGS      ?=

# new-design
LANG      ?= verilog
TOP       ?=
CLOCK     ?= clk
PERIOD    ?= 20
DIE       ?= 150,150

# sim
STAGE     ?= rtl
WAVES     ?=

# ext-build / ext-new
CELL      ?=
FROM      ?=
ONLY      ?=
CORNERS   ?=
DRIVER    ?=
PEX_MODE  ?=
ACCEPT_DELAY_LOSS ?=
EXT_PROVISIONAL ?=

# gc
KEEP      ?= 3

# ---- spelled once ------------------------------------------------------------
_TAG      = $(if $(TAG),--tag $(TAG))
_SET      = $(foreach s,$(SET),--set '$(s)')
_AGENT    = $(if $(AGENT),--agent $(AGENT)) $(foreach s,$(AGENT_SET),--agent-set '$(s)')
_COMMON   = $(_TAG) $(_SET) $(_AGENT)
_YES      = $(if $(filter 1 yes true,$(YES)),--yes)

define need_design
	@if [ -z "$(DESIGN)" ]; then \
		echo "error: name the design: make $@ DESIGN=<name>"; \
		$(AION) designs; exit 2; fi
endef

.DEFAULT_GOAL := help
.PHONY: help new-design designs config flow status steps sim baseline \
        artifacts clean-run gc cells agents agent \
        ext-rank ext-new ext-build ext-lib explore test test-layout-tool

help: ## This list
	@awk 'BEGIN {FS = ":.*##"; printf "\nAION framework\n\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo
	@echo "  common variables: DESIGN= TAG= STEP= SET=\"K=V ...\" AGENT= AGENT_SET=\"k=v ...\""
	@echo "                    DRAW=manual|auto JOBS=N LENIENT=1 DRY_RUN=1 QUIET=1 FORCE=1"
	@echo

# ---- designs -----------------------------------------------------------------
new-design: ## New design from the template (DESIGN= LANG=vhdl|verilog|systemverilog TOP= CLOCK= PERIOD= DIE=W,H AGENT=)
	@if [ -z "$(DESIGN)" ]; then echo "error: make new-design DESIGN=<name> [LANG=vhdl]"; exit 2; fi
	$(AION) new-design $(DESIGN) --lang $(LANG) $(if $(TOP),--top $(TOP)) \
		--clock $(CLOCK) --period $(PERIOD) --die $(DIE) $(if $(AGENT),--agent $(AGENT)) \
		$(if $(FORCE),--force)

designs: ## List the designs under designs/
	@$(AION) designs

config: ## Resolved settings of a design, who set each, and its agent (DESIGN=)
	$(need_design)
	@$(AION) config $(DESIGN) $(_COMMON)

# ---- the flow ----------------------------------------------------------------
flow: ## Run the flow (DESIGN= STEP=<n|a..b|n-> TAG= SET= AGENT= DRAW= JOBS= LENIENT= DRY_RUN=)
	$(need_design)
	$(AION) run $(DESIGN) $(STEP) $(_COMMON) \
		$(if $(DRAW),--draw $(DRAW)) $(if $(JOBS),-j $(JOBS)) \
		$(if $(LENIENT),--lenient) $(if $(DRY_RUN),--dry-run) $(if $(QUIET),--quiet) \
		$(if $(FORCE),--force) $(if $(EXT_PROVISIONAL),--provisional)

status: ## Per-step coherence of a run (DESIGN= TAG=)
	$(need_design)
	@$(AION) status $(DESIGN) $(_TAG)

steps: ## The nine flow steps
	@$(AION) steps

sim: ## Testbench on one view (DESIGN= STAGE=rtl|synth|rewrite|pnr|baseline WAVES=1)
	$(need_design)
	$(AION) sim $(DESIGN) --stage $(STAGE) $(_COMMON) $(if $(WAVES),--waves)

baseline: ## Harden the PDK-only control into runs/<d>/<tag>/baseline/ (DESIGN=)
	$(need_design)
	$(AION) baseline $(DESIGN) $(_COMMON) $(if $(LENIENT),--lenient)

# ---- artifacts ---------------------------------------------------------------
artifacts: ## Runs of a design; with TAG=, that run in detail (steps, sizes, agent, cost)
	$(need_design)
	@$(AION) artifacts $(DESIGN) $(_TAG)

clean-run: ## Delete a run, or one step of it (DESIGN= TAG= [STEP=] YES=1)
	$(need_design)
	@if [ -z "$(TAG)" ]; then echo "error: make clean-run DESIGN=$(DESIGN) TAG=<tag> [STEP=n] YES=1"; exit 2; fi
	@$(AION) clean $(DESIGN) --tag $(TAG) $(if $(STEP),--step $(STEP)) $(_YES)

gc: ## Delete all but the KEEP newest runs of a design (DESIGN= KEEP=3 YES=1)
	$(need_design)
	@$(AION) gc $(DESIGN) --keep $(KEEP) $(_YES)

cells: ## The shared library of drawn cells, with who drew each and what it cost
	@$(AION) cells

# ---- agents ------------------------------------------------------------------
agents: ## List the agent profiles under agents/
	@$(AION) agents

agent: ## One agent profile, resolved (AGENT= AGENT_SET=)
	@if [ -z "$(AGENT)" ]; then echo "error: make agent AGENT=<profile>"; exit 2; fi
	@$(AION) agent $(AGENT) $(foreach s,$(AGENT_SET),--agent-set '$(s)')

# ---- the functional cell extractor (PDK extension, per design) --------------
ext-rank: ## Rank multi-cell functions of the synthesized netlist (DESIGN= ARGS="--new-only --explain 3")
	$(need_design)
	$(AION) ext rank $(DESIGN) $(_COMMON) -- $(ARGS)

ext-new: ## Skeleton of a hand-designed cell in designs/<d>/pdk_extension/ (DESIGN= CELL=)
	$(need_design)
	@if [ -z "$(CELL)" ]; then echo "error: make ext-new DESIGN=$(DESIGN) CELL=<name>"; exit 2; fi
	$(AION) ext new $(DESIGN) $(CELL) $(_COMMON) $(if $(FORCE),--force)

ext-build: ## Characterize, draw, publish one extension cell (DESIGN= CELL= FROM= ONLY= AGENT= DRIVER= CORNERS=)
	$(need_design)
	@if [ -z "$(CELL)" ]; then echo "error: make ext-build DESIGN=$(DESIGN) CELL=<name>"; exit 2; fi
	$(AION) ext build $(DESIGN) $(CELL) $(_COMMON) \
		$(if $(FROM),--from $(FROM)) $(if $(ONLY),--only $(ONLY)) $(if $(DRAW),--draw $(DRAW)) \
		$(if $(CORNERS),--corners $(CORNERS)) $(if $(DRIVER),--driver-cell $(DRIVER)) \
		$(if $(PEX_MODE),--pex-mode $(PEX_MODE)) $(if $(JOBS),--jobs $(JOBS)) \
		$(if $(ACCEPT_DELAY_LOSS),--accept-delay-loss $(ACCEPT_DELAY_LOSS)) \
		$(if $(DRY_RUN),--dry-run) $(if $(QUIET),--quiet)

ext-lib: ## Splice the design's extension cells into its run's Liberty + tech dict (DESIGN= EXT_PROVISIONAL=1)
	$(need_design)
	$(AION) ext lib $(DESIGN) $(_COMMON) $(if $(EXT_PROVISIONAL),--provisional)

explore: ## Sweep the step-2/3 mining knobs (DESIGN= ARGS="sweep --budget 10")
	$(need_design)
	$(AION) explore $(DESIGN) $(_COMMON) -- $(ARGS)

# ---- tests -------------------------------------------------------------------
test: ## Framework self-tests (host only, no container)
	$(PYTHON) -m pytest -q tests

test-layout-tool: ## The layout tool's own host-side suite
	$(MAKE) -C aion_flow aion-layout-test
