# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The design's cocotb testbench, on every view
#
#  One testbench (designs/<d>/tb/<module>.py), five things it runs against:
#
#    rtl       the RTL                          design.rtl_sim (ghdl, icarus, ...)
#    synth     1_synth's netlist                design.gate_sim, zero delay
#    rewrite   3_rewrite's netlist + AION cells design.gate_sim, zero delay
#    pnr       7_pnr's netlist, drawn cells     icarus with the SDF (SIM_SDF)
#    baseline  the PDK-only hardened netlist    icarus with the SDF
#
#  The testbench drives the top-level ports only, so the same file serves
#  every view.  Everything lands in runs/<d>/<tag>/sim/<stage>/.
# ================================================================

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from . import workspace
from .runner import Runner, StepFailed
from .style import note, ok, warn

STAGES = ("rtl", "synth", "rewrite", "pnr", "baseline")


def _spec(ws, stage: str, simulator: str, lang: str, sources: list, *,
          build_args=(), test_args=(), plusargs=(), timescale=("1ns", "1ps"),
          waves: bool = False) -> Path:
    design = ws.design
    build = ws.sim_dir / stage
    build.mkdir(parents=True, exist_ok=True)
    spec = {
        "stage": stage,
        "simulator": simulator,
        "lang": lang,
        "toplevel": design.top,
        "sources": [str(Path(s).resolve()) for s in sources],
        "includes": [str(p) for p in design.include_dirs] if lang != "vhdl" else [],
        "defines": dict(design.defines) if stage == "rtl" else {},
        "build_args": list(build_args),
        "test_args": list(test_args),
        "plusargs": list(plusargs),
        "test_module": design.tb_module,
        "test_dir": str(design.tb_dir.resolve()),
        "build_dir": str(build.resolve()),
        "timescale": list(timescale) if timescale else None,
        "waves": waves,
    }
    path = build / "spec.json"
    path.write_text(json.dumps(spec, indent=2) + "\n")
    return path


def _run(ws, runner: Runner, stage: str, spec: Path) -> None:
    result = runner.host([sys.executable, "-m", "aion.simrun", str(spec)],
                         name=f"sim.{stage}")
    if runner.dry_run:
        return
    verdict = result.verdict("SIM:")
    if not result.ok:
        raise StepFailed(f"simulation ({stage}): {verdict or 'did not finish'}",
                         result.returncode or 1, result.log)
    ok(verdict or f"simulation ({stage}) passed")


def skip_reason(ws, cfg) -> Optional[str]:
    if not cfg.SIM:
        return "SIM is off"
    if not ws.design.tb_module:
        return "the design names no testbench ([tb] module)"
    if not ws.design.has_testbench:
        return f"no {workspace.rel(ws.design.tb_dir / (ws.design.tb_module + '.py'))}"
    return None


# ---------------------------------------------------------------------
def rtl(ws, runner: Runner, waves: bool = False) -> None:
    design = ws.design
    lang = "vhdl" if design.language == "vhdl" else "verilog"
    build_args, test_args = [], []
    if design.rtl_sim == "ghdl":
        build_args = [f"--std={design.vhdl_std}", "-fsynopsys"]
        test_args = [f"--std={design.vhdl_std}"]
    elif design.rtl_sim == "nvc":
        build_args = [f"--std={design.vhdl_std}"]
    elif design.rtl_sim == "icarus":
        build_args = ["-g2012"]
    elif design.rtl_sim == "verilator":
        build_args = ["--timing", "-Wno-fatal"]
    _run(ws, runner, "rtl",
         _spec(ws, "rtl", design.rtl_sim, lang, design.rtl,
               build_args=build_args, test_args=test_args, waves=waves))


def gate(ws, runner: Runner, stage: str, netlist: Path, models: list, *,
         sdf: Optional[Path] = None, waves: bool = False) -> None:
    """A gate-level netlist plus the cell models it instantiates."""
    if stage not in STAGES[1:]:
        raise ValueError(stage)
    design, pdk = ws.design, ws.pdk
    simulator = "icarus" if sdf else design.gate_sim
    plusargs: list = []
    sources = [netlist, *models]
    if simulator == "icarus":
        cell_models = list(pdk.verilog_models)
        build_args = ["-g2012"]
        if sdf:
            # A second root that annotates the DUT by name, so the testbench
            # still drives the top level directly.
            annotate = ws.sim_dir / stage / "sdf_annotate.v"
            annotate.parent.mkdir(parents=True, exist_ok=True)
            annotate.write_text(
                "module sdf_annotate;\n"
                f"  initial $sdf_annotate(\"{Path(sdf).resolve()}\", {design.top});\n"
                "endmodule\n")
            sources.append(annotate)
            build_args += ["-gspecify", "-ginterconnect", "-Ttyp", "-s", "sdf_annotate"]
            note(f"SDF {workspace.rel(sdf)} — delays only; setup/hold are STA's job")
    else:
        cell_models = list(pdk.verilog_models_nodelay)
        build_args = ["--timing", "-Wno-fatal", "-Wno-SPECIFYIGN",
                      "-Wno-TIMESCALEMOD", "-Wno-PINCONNECTEMPTY",
                      "-Wno-UNOPTFLAT", "-Wno-WIDTHEXPAND", "-Wno-MULTIDRIVEN"]
        if sdf:
            warn("Verilator ignores SDF: this run checks function only")
    sources += cell_models
    missing = [s for s in sources if not Path(s).is_file()]
    if missing and not runner.dry_run:
        raise StepFailed("simulation sources missing: "
                         + ", ".join(workspace.rel(m) for m in missing), 2)
    _run(ws, runner, stage,
         _spec(ws, stage, simulator, "verilog", sources, build_args=build_args,
               plusargs=plusargs, waves=waves))
