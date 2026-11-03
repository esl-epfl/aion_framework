# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               7_pnr -- harden the rewritten netlist with the
#                             drawn cells, then simulate it with the SDF
# ================================================================

from __future__ import annotations

import json
import shutil

from collect_cells import check_cells, collect_cells, collect_pdk_extension_cells

from .. import librelane, pdk_ext, sim, workspace
from ..style import info, note, warn
from .base import Context, Step, StepFailed


class PnrStep(Step):
    key = "7_pnr"
    title = "Place and route"
    summary = "harden the rewritten netlist with the AI cells, then simulate with SDF"
    upstream = ("3_rewrite", "6_layout_drawing")
    needs_container = True

    def inputs(self, ctx: Context) -> list:
        sources = [ctx.ws.netlist("3_rewrite"), ctx.ws.design.librelane_config,
                   ctx.ws.design.sdc]
        if ctx.ws.design.language != "vhdl":
            sources.append(ctx.ws.step("1_synth") / "json_h")
        sources += [ctx.ws.cells_dir / c for c in sorted(self._wanted(ctx))
                    if (ctx.ws.cells_dir / c).is_dir()]
        if ctx.cfg.PDK_EXT and ctx.ws.pdk_ext_dir.is_dir():
            sources.append(ctx.ws.pdk_ext_dir)
        return sources

    def outputs(self, ctx: Context) -> list:
        top = ctx.ws.design.top
        return [ctx.ws.netlist(self.key), self.outdir(ctx) / "gds" / f"{top}.gds",
                self.outdir(ctx) / "metrics.json"]

    def _wanted(self, ctx: Context) -> set:
        return instantiated_cells(ctx.ws.netlist("3_rewrite"), ctx.cfg.CELL_PREFIX)

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, run, ws = ctx.cfg, ctx.runner, ctx.ws
        netlist = ws.netlist("3_rewrite")
        self.require(netlist)

        ext = pdk_ext.views(ctx)
        wanted = self._wanted(ctx)
        published = drawn_cells(ws.cells_dir) & wanted
        ext_used = used_cells(netlist, ext.cells) if ext.active else set()
        ext_drawn = drawn_cells(ws.pdk_ext_dir) & ext_used
        info(f"{len(wanted)} mined cell(s) instantiated, {len(published)} published "
             f"in {workspace.rel(ws.cells_dir)}")
        if ext.active:
            info(f"{len(ext_used)} PDK-extension cell(s) instantiated, {len(ext_drawn)} drawn")
        missing = sorted((wanted - published) | (ext_used - ext_drawn))
        if missing and not self.dry_run:
            raise StepFailed(
                "the netlist instantiates cells that have no published views: "
                + ", ".join(missing)
                + "\n  run step 6 until every mined cell is published"
                + ("; a hand-designed one is drawn with `make ext-build CELL=<name>`"
                   if ext_used else ""), 2)
        if not wanted:
            warn("no AION cells in the netlist — this run is a second baseline")

        if not self.dry_run:
            groups = []
            if published:
                groups.append((str(ws.cells_dir), [c for c in collect_cells(str(ws.cells_dir))
                                                   if c.name in published]))
            if ext_drawn:
                groups.append((str(ws.pdk_ext_dir),
                               [c for c in collect_pdk_extension_cells(str(ws.pdk_ext_dir))
                                if c.name in ext_drawn]))
            if groups and check_cells(groups, strict=not cfg.LENIENT):
                raise StepFailed("the published cells would not survive PnR (see above)", 2)

        run_dir = librelane.prepare(ws, cfg, "pnr", cells_used=published,
                                    ext_cells_used=ext_drawn)
        top = ws.design.top
        overrides = [f"-e nl=nl/{top}.nl.v"]
        # Classic (Verilog/SV) runs Odb.SetPowerConnections, which reads the
        # JSON header Yosys.JsonHeader wrote at the start of synthesis -- a
        # step `--from` skips.  It describes the top level's ports, which the
        # rewrite does not touch, so step 1's copy is the right one.
        # VHDLClassic has neither step.
        header = ws.step("1_synth") / "json_h" / f"{top}.h.json"
        if ws.design.language != "vhdl":
            self.require(header)
            overrides.append(f"-e json_h=nl/{top}.h.json")
        if not self.dry_run:
            (run_dir / "nl").mkdir(exist_ok=True)
            shutil.copy2(netlist, run_dir / "nl" / f"{top}.nl.v")
            if ws.design.language != "vhdl":
                shutil.copy2(header, run_dir / "nl" / f"{top}.h.json")
        librelane.run(ws, run, "pnr", self.outdir(ctx),
                      extra_args="--from Checker.NetlistAssignStatements " + " ".join(overrides),
                      name="librelane.pnr")
        self.require(ws.netlist(self.key))
        if not self.dry_run:
            report(ctx, self.outdir(ctx))

        reason = sim.skip_reason(ws, cfg)
        if reason:
            warn(f"post-PnR simulation skipped: {reason}")
            return "ok"
        models = [ws.cells_dir / c / f"{c}.v" for c in sorted(published)]
        models += [ws.pdk_ext_dir / c / f"{c}.layout.v" for c in sorted(ext_drawn)]
        sim.gate(ws, run, "pnr", ws.netlist(self.key), models,
                 sdf=sdf_file(ws, self.outdir(ctx), cfg.SDF_CORNER) if cfg.SIM_SDF else None)
        return "ok"


# ---------------------------------------------------------------------
def sdf_file(ws, outdir, corner: str):
    path = outdir / "sdf" / corner / f"{ws.design.top}__{corner}.sdf"
    if not path.is_file():
        found = sorted((outdir / "sdf").rglob("*.sdf")) if (outdir / "sdf").is_dir() else []
        return found[0] if found else path
    return path


def report(ctx: Context, outdir) -> None:
    try:
        metrics = json.loads((outdir / "metrics.json").read_text())
    except (OSError, ValueError):
        return
    info("hardened")
    note(f"instances {metrics.get('design__instance__count')}   "
         f"core area {metrics.get('design__core__area')}   "
         f"utilization {metrics.get('design__instance__utilization')}")
    note(f"setup ws {metrics.get('timing__setup__ws')}   "
         f"hold ws {metrics.get('timing__hold__ws')}   "
         f"DRC {metrics.get('route__drc_errors')}   "
         f"LVS {metrics.get('design__lvs_error__count')}")
    ctx.note(f"{metrics.get('design__instance__count')} instances, "
             f"setup ws {metrics.get('timing__setup__ws')}")


def drawn_cells(root) -> set:
    """Cells with a published abstract under `root`, by LEF file stem."""
    if not root.is_dir():
        return set()
    return {path.stem for path in root.rglob("*.lef") if path.is_file()}


def instantiated_cells(netlist, prefix: str) -> set:
    """Which AION modules the netlist instantiates."""
    try:
        text = netlist.read_text()
    except OSError:
        return set()
    found = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            found.add(stripped.split()[0].split("(")[0])
    return found


def used_cells(netlist, cells) -> set:
    """Which of `cells` the netlist instantiates (by master name)."""
    try:
        text = netlist.read_text()
    except OSError:
        return set()
    masters = {line.split()[0] for line in text.splitlines() if line.strip()
               and not line.lstrip().startswith(("//", "module", "input", "output",
                                                  "wire", "assign", "endmodule"))}
    return {c for c in cells if c in masters}


STEP = PnrStep()
