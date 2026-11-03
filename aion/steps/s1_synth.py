# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               1_synth -- RTL -> gate netlist, then simulate
# ================================================================

from __future__ import annotations

import json

from .. import librelane, pdk_ext, sim
from ..style import info, note, warn
from .base import Context, Step


class SynthStep(Step):
    key = "1_synth"
    title = "Synthesis"
    summary = "RTL -> gate-level netlist + pre-PnR STA, then post-synthesis simulation"
    upstream = ()
    needs_container = True

    def inputs(self, ctx: Context) -> list:
        d = ctx.ws.design
        sources = [*d.rtl, d.librelane_config, d.sdc, d.toml]
        # A cell added or recharacterized there is a changed input to synthesis,
        # which is what makes every later step read STALE.
        if ctx.cfg.PDK_EXT and ctx.ws.pdk_ext_dir.is_dir():
            sources.append(ctx.ws.pdk_ext_dir)
        return sources

    def outputs(self, ctx: Context) -> list:
        return [ctx.ws.netlist(self.key), self.outdir(ctx) / "metrics.json"]

    def run(self, ctx: Context) -> str:
        ws, run = ctx.ws, ctx.runner
        ext = pdk_ext.views(ctx)
        if ext.active:
            info(f"mapping against the PDK + {len(ext.cells)} extension cell(s)")
        librelane.prepare(ws, ctx.cfg, "synth", ext_libs=ext.libraries or None)
        librelane.run(ws, run, "synth", self.outdir(ctx), name="librelane.synth")

        netlist = ws.netlist(self.key)
        self.require(netlist)
        self._report(ctx)

        reason = sim.skip_reason(ws, ctx.cfg)
        if reason:
            warn(f"post-synthesis simulation skipped: {reason}")
            return "ok"
        models = [ws.pdk_ext_dir / c / f"{c}.v" for c in ext.cells]
        sim.gate(ws, run, "synth", netlist, models)
        return "ok"

    def _report(self, ctx: Context) -> None:
        path = self.outdir(ctx) / "metrics.json"
        try:
            metrics = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        cells = metrics.get("design__instance__count")
        area = metrics.get("design__instance__area")
        info("synthesis")
        note(f"cells {cells}   area {area} um2   setup ws {metrics.get('timing__setup__ws')}")
        ctx.note(f"{cells} cells, {area} um2")


STEP = SynthStep()
