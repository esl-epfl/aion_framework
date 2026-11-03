# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               3_rewrite -- substitute the cells, prove it, sim it
# ================================================================

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Optional

from .. import pdk_ext, sim, workspace
from ..config import optional
from ..style import info, note, ok, warn
from .base import Context, Step, StepFailed
from .s2_pattern_extraction import cell_lib_vars, mining_vars


class RewriteStep(Step):
    key = "3_rewrite"
    title = "Rewrite"
    summary = "instantiate the AION cells, prove equivalence (LEC), re-simulate"
    upstream = ("1_synth", "2_pattern_extraction")
    needs_container = True          # LEC runs kepler-formal

    def cells(self, ctx):
        """The library of record: exactly the cells this netlist instantiates.

        Steps 4, 5 and 6 all read this file rather than step 2's, so that a
        hand-curated selection propagates without being re-derived.
        """
        return self.outdir(ctx) / "nl" / "aion_cells.v"

    def inputs(self, ctx: Context) -> list:
        return [ctx.ws.netlist("1_synth"),
                self._source_library(ctx),
                ctx.ws.step("2_pattern_extraction") / "work" / "selection.json"]

    def outputs(self, ctx: Context) -> list:
        return [ctx.ws.netlist(self.key), self.cells(ctx),
                self.outdir(ctx) / "report" / "rewrite_report.json"]

    # -----------------------------------------------------------------
    def _source_library(self, ctx: Context) -> Path:
        step2 = ctx.ws.step("2_pattern_extraction")
        choice = ctx.cfg.REWRITE_CELLS
        if choice == "elite":
            return step2 / "aion_cells_elite.v"
        if choice == "all":
            return step2 / "aion_cells.v"
        path = Path(choice)
        return path if path.is_absolute() else (workspace.FRAMEWORK_ROOT / path)

    def _lec_lib(self, ctx: Context) -> Optional[Path]:
        """The Liberty kepler-formal must read, as a host path.

        kepler-formal resolves every instance in both netlists against one
        Liberty, so the file has to describe every cell the netlist actually
        instantiates.  run_lec_sec.py defaults to the plain PDK library, and
        with PDK_EXT on that is exactly the library synthesis did *not* map
        against: the netlist can carry an `AION_mux2i_*` the mapper took out
        of the extended one, and the LEC then dies at load time with

            AION_mux2i_1 cannot be found in SNL while constructing instance ...

        which is a missing cell definition, not a design that changed.  So
        point it at the same extended Liberty synthesis mapped against.

        `None` keeps run_lec_sec.py's default -- right when PDK_EXT is off, or
        when there are no extension cells to have been mapped in.
        """
        if ctx.cfg.LEC_LIB:
            path = Path(ctx.cfg.LEC_LIB)
            return path if path.is_absolute() else workspace.FRAMEWORK_ROOT / path
        return pdk_ext.views(ctx).library(ctx.ws.pdk.typ_corner)

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run = ctx.cfg, ctx.runner
        step1 = ctx.ws.netlist("1_synth")
        step2 = ctx.ws.step("2_pattern_extraction")
        selection = step2 / "work" / "selection.json"
        source = self._source_library(ctx)
        ext = pdk_ext.views(ctx)

        self.require(step1, source, selection)

        nl_dir = self.ensure(ctx, "nl")
        self.ensure(ctx, "report")

        # Freeze the chosen library here. Everything downstream reads this
        # copy, so a later edit to step 2's output cannot silently change
        # what step 6 is asked to draw.
        if not self.dry_run and source.resolve() != self.cells(ctx).resolve():
            shutil.copy2(source, self.cells(ctx))
        elif source.resolve() == self.cells(ctx).resolve():
            # REWRITE_CELLS pointing at the frozen copy is a normal way to
            # re-run after hand-editing it; there is nothing to copy.
            info("rewriting with this step's own frozen library")
        kept = _module_names(self.cells(ctx)) if self.cells(ctx).exists() else []
        info(f"library of record: {workspace.rel(source)} -> "
             f"{len(kept)} cell(s)")
        for name in kept:
            note(f"  {name}")
        if not kept and not self.dry_run:
            raise StepFailed(f"{workspace.rel(source)} defines no modules", 2)

        # A mined cell is named after the PDK cells it merges, so one built
        # out of AION_mux2i_1 is called AION_mux2i_<id> -- one digit away from
        # the hand-designed cell's own name. If the two ever land on the same
        # name, aion_cells.v and pdk_extension.core both define that module
        # and the simulator takes whichever it read first, silently.
        clash = sorted(set(kept) & set(ext.cells))
        if clash:
            raise StepFailed(
                f"{', '.join(clash)}: a mined cell has the same name as a "
                f"hand-designed one under {workspace.rel(ctx.ws.pdk_ext_dir)}/.\n"
                f"  Both would define the module, and the netlist would bind "
                f"to whichever the reader saw first.\n"
                f"  Rename the mined cell in "
                f"{workspace.rel(self.cells(ctx))} (keeping its "
                f"'// AION canonical_key:' comment with it), or re-run "
                f"step 2 with a different CELL_PREFIX.", 2)

        netlist = ctx.ws.netlist(self.key)
        flat = nl_dir / f"{ctx.ws.design.top}.flat.nl.v"
        report_prefix = self.outdir(ctx) / "report" / "rewrite_report"

        run.check(
            run.flow_host("aion-opt-rewrite", [
                f"INPUT={step1}",
                f"TOP={ctx.ws.design.top}",
                *mining_vars(cfg),
                *cell_lib_vars(ctx, self.outdir(ctx) / "work"),
                f"CELLS={self.cells(ctx)}",
                f"REWRITE_NETLIST={netlist}",
                *(["REWRITE_FLAT=" + str(flat)] if cfg.REWRITE_FLAT else []),
                f"REWRITE_REPORT={report_prefix}",
                f"SELECTION={selection}",
                f"BUILD_DIR={self.outdir(ctx) / 'steps'}",
            ], name="aion-opt-rewrite"),
            "rewrite",
        )
        self.require(netlist)
        if not self.dry_run:
            self._report(ctx)

        # ---- LEC: the rewritten netlist must be the netlist we started from.
        # kepler-formal is container-only. MOD is two files because the
        # netlist instantiates the AION modules and both have to be readable
        # in one compilation unit.
        # Two paths in one make variable, deliberately: the Makefile
        # interpolates MOD unquoted into the script's `--mod` (nargs="+").
        # No quotes here -- Runner.container() quotes each assignment once for
        # the shell hop into the container, and quoting it twice makes the
        # pair a single unopenable filename.
        lib = self._lec_lib(ctx)
        mod = f"{workspace.container(netlist)} {workspace.container(self.cells(ctx))}"
        lec = run.container("aion-opt-lec", [
            f"REF={workspace.container(step1)}",
            f"MOD={mod}",
            f"BUILD_DIR={workspace.container(self.outdir(ctx) / 'steps')}",
            # LEC runs in the container, so a Liberty named on the host has to
            # be renamed too; kepler-formal cannot open /home/... from there.
            *optional("LIB", workspace.container(lib) if lib else None),
        ], name="aion-opt-lec")
        if not lec.ok:
            # run_lec_sec.py decides by string-matching its log, so a real
            # mismatch, a tool crash and a netlist that never loaded all share
            # an exit status. Say which.
            unresolved = _unresolved_cells(lec.output)
            if "Difference was found." in lec.output:
                verdict = "MISMATCH"
            elif unresolved:
                verdict = ("compared nothing: " + ", ".join(unresolved)
                           + " is not in the Liberty it read")
            else:
                verdict = "did not reach a verdict"
            warn(f"LEC {verdict} — see {workspace.rel(lec.log)}")
            if unresolved:
                # kepler-formal resolves every instance against the Liberty
                # before it compares anything, so a cell that is missing from
                # it stops the run at load time. That is a library problem,
                # not a rewrite that broke the design.
                named = workspace.rel(lib) if lib else "the tool default"
                note(f"Liberty: {named}")
                note("every cell the netlist instantiates has to be in it — "
                     "point LEC_LIB at a Liberty that has them, or re-run "
                     "step 1 with PDK_EXT=0")
            ctx.note(f"LEC {verdict}")
            run.check(lec, "logical equivalence check")
        if not self.dry_run:
            ok("LEC passed: the rewritten netlist is logically identical")

        # ---- Function check with the new cells in place.
        reason = sim.skip_reason(ctx.ws, cfg)
        if reason:
            warn(f"post-rewrite simulation skipped: {reason}")
            return "ok"
        models = [self.cells(ctx)] + [ctx.ws.pdk_ext_dir / c / f"{c}.v" for c in ext.cells]
        sim.gate(ctx.ws, run, "rewrite", netlist, models)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        """`aion-opt-rewrite` exits 0 even when it substituted nothing."""
        report = self.outdir(ctx) / "report" / "rewrite_report.json"
        if not report.exists():
            warn("no rewrite report — cannot confirm anything was substituted")
            return
        try:
            summary = json.loads(report.read_text()).get("summary", {})
        except (OSError, json.JSONDecodeError):
            return
        applied = summary.get("patterns_applied", 0)
        sites = summary.get("occurrences_applied", 0)
        reduction = summary.get("cell_reduction")
        saved = summary.get("estimated_total_area_savings")
        info(f"applied {applied} pattern(s) at {sites} site(s)")
        note(f"cell reduction {reduction}   estimated area saved {saved}")
        ctx.note(f"{applied} patterns at {sites} sites, cell reduction {reduction}")
        if not applied:
            raise StepFailed(
                "the rewrite substituted nothing: no cell in the library "
                "matches the mined cover. Check that the mining knobs match "
                "step 2 and that the curated library kept its "
                "'// AION canonical_key:' comments.", 2)


def _unresolved_cells(output: str) -> list:
    """Cells kepler-formal could not find in the Liberty it was given.

    It reports them as `<CELL> cannot be found in SNL while constructing
    instance <INST>` and stops at the first one, so this is normally a single
    name — but the message is the same whether one cell is missing or twenty.
    """
    return sorted({match.group(1) for match in
                   re.finditer(r"(\S+) cannot be found in SNL", output)})


def _module_names(path: Path) -> list:
    names = []
    for line in path.read_text().splitlines():
        stripped = line.lstrip()
        if stripped.startswith("module "):
            names.append(stripped[len("module "):].split("(")[0].strip().rstrip(";"))
    return names


STEP = RewriteStep()
