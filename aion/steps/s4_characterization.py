# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               4_characterization -- prove the cells compute
#                             what the netlist says they compute
#
#  aion_char builds, per AION module, a gold model from the PDK Liberty
#  functions and a `reference_<module>` transistor view assembled from the
#  PDK cells, then checks them against each other over every input vector --
#  once in SystemVerilog, once in ngspice.  reference_cells.spice is also the
#  input step 5 minimizes, which is why this step has to run before it even
#  if you only care about the minimizer.
# ================================================================

from __future__ import annotations

import re

from .. import workspace
from ..style import info, note, ok, warn
from .base import Context, Step


class CharacterizationStep(Step):
    key = "4_characterization"
    title = "Characterization"
    summary = "exhaustive SV + SPICE testbenches for every AION cell"
    upstream = ("3_rewrite",)
    needs_container = True

    def tb_dir(self, ctx):
        return self.outdir(ctx) / "steps" / "aion_char" / "tb"

    def reference_cells(self, ctx):
        return self.tb_dir(ctx) / "spice" / "reference_cells.spice"

    def inputs(self, ctx: Context) -> list:
        return [ctx.ws.step("3_rewrite") / "nl" / "aion_cells.v"]

    def outputs(self, ctx: Context) -> list:
        return [self.reference_cells(ctx), self.tb_dir(ctx) / "gold_functions.md"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        run = ctx.runner
        cells = ctx.ws.step("3_rewrite") / "nl" / "aion_cells.v"
        self.require(cells)

        build = self.ensure(ctx, "steps")
        # Container paths: every aion-char target runs inside iic-osic-tools.
        cells_c = workspace.container(cells)
        build_c = workspace.container(build)
        common = [f"BUILD_DIR={build_c}", f"NETLIST={cells_c}"]

        run.check(run.container("aion-char-generate", common,
                                name="aion-char-generate"),
                  "testbench generation")
        self.require(self.reference_cells(ctx))
        info(f"testbenches in {workspace.rel(self.tb_dir(ctx))}")

        run.check(run.container("aion-char-sv", common, name="aion-char-sv"),
                  "SystemVerilog testbenches")
        if not self.dry_run:
            ok("SystemVerilog testbenches passed")

        spice = run.container("aion-char-spice", common, name="aion-char-spice")
        if not spice.ok:
            for line in spice.output.splitlines():
                if line.startswith("[FAIL]") or line.startswith("[NO-RESULT]"):
                    warn(line.strip())
            ctx.note("SPICE testbenches failed")
            run.check(spice, "SPICE testbenches")
        if not self.dry_run:
            ok("SPICE testbenches passed")

        self._report(ctx)
        return "ok"

    # -----------------------------------------------------------------
    def _report(self, ctx: Context) -> None:
        cells = _reference_cells(self.reference_cells(ctx))
        info(f"{len(cells)} cell(s) characterized")
        for name in cells:
            note(f"  {name}")
        ctx.note(f"{len(cells)} cells characterized")


_SUBCKT = re.compile(r"^\s*\.subckt\s+(\S+)", re.IGNORECASE | re.MULTILINE)


def _reference_cells(path) -> list:
    """The `reference_<CELL>` subckts, with the prefix stripped."""
    try:
        text = path.read_text()
    except OSError:
        return []
    return [name[len("reference_"):] if name.startswith("reference_") else name
            for name in _SUBCKT.findall(text)]


STEP = CharacterizationStep()
