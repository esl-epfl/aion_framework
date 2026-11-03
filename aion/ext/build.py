# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Build one hand-designed PDK-extension cell:
#                             characterize, draw, characterize again, publish
#
#  The main flow mines its cells out of a netlist.  This does not: a
#  PDK-extension cell is DESIGNED, because the PDK's own implementation of
#  that function is not the best one available (a transmission-gate mux2 is
#  4 devices where complementary CMOS needs 12).  `aion ext rank` finds the
#  candidates; the cell is then authored in the design that wants it:
#
#    designs/<d>/pdk_extension/<CELL>/
#      <CELL>.v                gold gate-level reference -- what it computes
#      <CELL>.spice            the hand-designed transistor netlist
#      <CELL>.provisional.lib  Liberty for the mapper before it is drawn
#      FLOORPLAN.md            the placement plan the drawer implements
#      <CELL>.py               the layout generator (scaffolded, then drawn)
#      ---- published by this module ----
#      <CELL>.lef .gds <CELL>[_<corner>].lib .cdl .layout.v .png provenance.json
#
#  The whole directory is per design and git-ignored: extension cells are
#  never shared between designs and never committed unless forced.
#
#  Stages:
#    characterize  aion_char's 3-way ngspice proof: gold (the PDK Liberty
#                  function of <CELL>.v), the PDK reference transistors, and
#                  <CELL>.spice must agree on every input vector
#    layout        scaffold the generator, then the shared draw loop
#                  (agent/draw.py) until `make verify` says RESULT: PASS
#    post-layout   PEX, the abutted PDK baseline, Liberty from the extracted
#                  netlist, the comparison, the export, then publish
#    publish       (ONLY=publish) re-read the last post-layout verdict and
#                  publish without running it again
# ================================================================

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from collect_cells import check_lef, published_lib_problem

from .. import workspace
from ..agent import profile as agent_profile
from ..agent.draw import Drawer, DrawTask, provenance
from ..coherence import now
from ..runner import Runner, StepFailed
from ..style import fail, info, note, ok, title, warn

STAGES = ("characterize", "layout", "post-layout")
PUBLISH = "publish"
#: C-coupled: mode 3 (full RC) runs Magic's extresist, which can leave a rail
#: port bound to no segment on an abutted PDK baseline, and rail resistance is
#: the parasitic these cells least want modelled.  See aion_chip's history.
PEX_MODE_DEFAULT = 2
REQUIRED_VIEWS = (".lef", ".lib", ".gds")
RECOMMENDED_VIEWS = (".v", ".spice", ".cdl")
PUBLISHED_SUFFIXES = REQUIRED_VIEWS + RECOMMENDED_VIEWS + (".png", ".json")


@dataclass
class Cell:
    name: str
    root: Path            # designs/<d>/pdk_extension
    build_root: Path      # <run>/pdk_extension/cells

    @property
    def dir(self) -> Path:
        return self.root / self.name

    @property
    def verilog(self) -> Path:
        return self.dir / f"{self.name}.v"

    @property
    def spice(self) -> Path:
        return self.dir / f"{self.name}.spice"

    @property
    def provisional_lib(self) -> Path:
        return self.dir / f"{self.name}.provisional.lib"

    @property
    def floorplan(self) -> Path:
        return self.dir / "FLOORPLAN.md"

    @property
    def generator(self) -> Path:
        return self.dir / f"{self.name}.py"

    @property
    def build(self) -> Path:
        return self.build_root / self.name

    @property
    def layout_build(self) -> Path:
        return self.build / "layout"

    @property
    def reference(self) -> Path:
        return self.build / "steps" / "aion_char" / "tb" / "spice" / "reference_cells.spice"

    @property
    def reference_split(self) -> Path:
        return self.build / "raw_spice" / f"reference_{self.name}.spice"

    @property
    def authored(self) -> set:
        return {self.verilog.name, self.spice.name, self.provisional_lib.name,
                self.floorplan.name, self.generator.name}

    def require_sources(self) -> None:
        missing = [p for p in (self.verilog, self.spice) if not p.exists()]
        if missing:
            raise SystemExit(
                f"error: {self.name} is missing "
                + ", ".join(workspace.rel(p) for p in missing)
                + f"\n       an extension cell is authored as {self.name}.v (gold "
                f"gate-level reference)\n       plus {self.name}.spice (transistor "
                f"implementation) in {workspace.rel(self.dir)}/ — "
                f"`make ext-new CELL={self.name}` writes the skeleton")


# ---------------------------------------------------------------------
class Builder:
    def __init__(self, ws, cfg, runner: Runner, *, agent_overrides=(),
                 corners: str = "all", jobs: int = 8, driver: Optional[str] = None,
                 pex_mode: int = PEX_MODE_DEFAULT,
                 accept_delay_loss: Optional[float] = None):
        self.ws, self.cfg, self.run = ws, cfg, runner
        self.agent_overrides = list(agent_overrides)
        self.corners, self.jobs, self.pex_mode = corners, jobs, pex_mode
        self.driver = driver
        self.accept_delay_loss = accept_delay_loss

    def cell(self, name: str) -> Cell:
        return Cell(name, self.ws.pdk_ext_dir, self.ws.pdk_ext_build / "cells")

    # -----------------------------------------------------------------
    def characterize(self, cell: Cell) -> int:
        title(f" characterize {cell.name} ")
        common = [f"NETLIST={workspace.container(cell.verilog)}",
                  f"BUILD_DIR={workspace.container(cell.build / 'steps')}",
                  f"CUSTOM={workspace.container(cell.spice)}"]
        for target, label in (("aion-char-generate", "testbench generation"),
                              ("aion-char-sv", "SystemVerilog testbenches"),
                              ("aion-char-spice", "ngspice 3-way proof")):
            if not self.run.container(target, common, name=f"{cell.name}.{target}").ok:
                fail(f"{label} failed")
                return 1
            ok(label) if not self.run.dry_run else info(f"would run: {label}")
        if self.run.dry_run:
            return 0
        if not cell.reference.exists():
            fail(f"no reference netlist at {workspace.rel(cell.reference)}")
            return 1
        text = cell.reference.read_text()
        start = text.find(f".subckt reference_{cell.name} ")
        if start < 0:
            fail(f"reference_{cell.name} not found in {workspace.rel(cell.reference)}")
            return 1
        cell.reference_split.parent.mkdir(parents=True, exist_ok=True)
        cell.reference_split.write_text(text[start:text.find(".ends", start)] + ".ends\n")
        ok(f"reference netlist -> {workspace.rel(cell.reference_split)}")
        return 0

    # -----------------------------------------------------------------
    def task(self, cell: Cell) -> DrawTask:
        return DrawTask(kind="ext_layout", cell=cell.name, generator=cell.generator,
                        netlist=cell.spice, build=cell.layout_build, jobs=self.jobs,
                        sections=floorplan_section(cell))

    def layout(self, cell: Cell) -> int:
        title(f" layout {cell.name} ")
        task = self.task(cell)
        if not self.run.dry_run:
            task.build.mkdir(parents=True, exist_ok=True)
        if not cell.generator.exists():
            info(f"no generator at {workspace.rel(cell.generator)} — scaffolding one")
            if not self.run.layout("scaffold", task.make_vars(), name=f"{cell.name}.scaffold").ok:
                return 1
            warn("the scaffold is deliberately incomplete; its first verify WILL fail")
            if self.cfg.DRAW_MODE != "auto":
                info(f"finish it by hand, then:  make ext-build DESIGN={self.ws.design.name} "
                     f"CELL={cell.name} FROM=layout")
                return 2
        info(f"generator {workspace.rel(cell.generator)}")
        if self.cfg.DRAW_MODE == "auto":
            drawer = Drawer(self.ws, self.run,
                            agent_profile.load(self.cfg.AGENT, self.agent_overrides))
            drawer.prepare()
            verdict = drawer.draw(task)
        else:
            verdict = self.run.layout("verify", task.make_vars(),
                                      name=f"{cell.name}.verify").verdict("RESULT:")
        if self.run.dry_run:
            info("dry run: DRC and LVS were not executed, so there is no verdict")
            return 0
        if verdict != "RESULT: PASS":
            fail(f"the layout does not verify yet ({verdict or 'no verdict'})")
            info(f"iterate on {workspace.rel(cell.generator)}, then:  make ext-build "
                 f"DESIGN={self.ws.design.name} CELL={cell.name} FROM=layout")
            return 2
        ok("RESULT: PASS — DRC and LVS clean against the hand-designed netlist")
        return 0

    # -----------------------------------------------------------------
    def post_layout(self, cell: Cell) -> int:
        title(f" post-layout {cell.name} ")
        if not cell.reference_split.exists() and not self.run.dry_run:
            fail(f"no baseline at {workspace.rel(cell.reference_split)} — "
                 f"run the characterize stage first")
            return 1
        if self.driver is None:
            warn("no driver cell: the Liberty is measured against an ideal ramp, "
                 "which over-states a cell with a pass gate on an input path")
        else:
            info(f"stimulus   {self.driver} (A -> X), candidate and baseline both")
        task = self.task(cell)
        result = self.run.layout("flow", [
            *task.make_vars(), f"BASELINE={cell.reference_split}",
            f"CORNERS={self.corners}", f"PEX_MODE={self.pex_mode}",
            *((f"DRIVER={self.driver}", "DRIVER_IN=A", "DRIVER_OUT=X") if self.driver else ()),
        ], name=f"{cell.name}.flow")
        if self.run.dry_run:
            return 0
        return self._publish_if_allowed(cell, result.output, chain_ok=result.ok)

    def publish(self, cell: Cell) -> int:
        title(f" publish {cell.name} ")
        log = self.run.log_dir / f"{cell.name}.flow.log"
        if not log.exists():
            fail(f"no post-layout run to publish: {workspace.rel(log)} does not exist")
            return 1
        info(f"verdict from {workspace.rel(log)}")
        return self._publish_if_allowed(cell, log.read_text(), chain_ok=None)

    # -----------------------------------------------------------------
    def _publish_if_allowed(self, cell: Cell, output: str, chain_ok) -> int:
        allowed, accepted, reason = may_publish(output, self.accept_delay_loss)
        if not allowed:
            fail(f"not published: {reason}")
            if reason.startswith("COMPARE: LOSS ("):
                info(f"to publish it anyway without re-characterizing:  make ext-build "
                     f"DESIGN={self.ws.design.name} CELL={cell.name} ONLY=publish "
                     f"ACCEPT_DELAY_LOSS=<PCT>")
            return 1
        if accepted:
            warn(reason)
            warn("this cell is published SLOWER than the PDK cells it replaces on its "
                 "worst arc; the Liberty says so and STA will see it")
        elif chain_ok is False:
            fail("the mechanical chain failed after printing COMPARE: WIN")
            return 1
        return 0 if self._publish_views(cell, reason) else 1

    def _publish_views(self, cell: Cell, verdict: str) -> bool:
        final = cell.layout_build / "final"
        if not final.is_dir():
            fail(f"nothing exported to {workspace.rel(final)}")
            return False
        rejected = sorted(final.glob("*.rejected"))
        if rejected:
            fail("the exporter REFUSED to publish — " + ", ".join(p.name for p in rejected))
            return False
        written = [p for p in sorted(final.iterdir()) if p.is_file()]
        views = {p.suffix.lower(): p for p in written}
        missing = [ext for ext in REQUIRED_VIEWS if ext not in views]
        if missing:
            fail(f"cannot publish, missing {', '.join(missing)}")
            return False
        problem = published_lib_problem(cell.name, [p.name for p in written
                                                    if p.suffix.lower() == ".lib"])
        if problem is not None:
            fail(f"cannot publish, {problem}")
            return False
        problems = check_lef(str(views[".lef"]))
        if problems:
            fail("cannot publish, the LEF would fail PnR")
            for problem in problems:
                note(f"  {problem}")
            return False

        for path in sorted(cell.dir.iterdir()):
            if (path.is_file() and path.name not in cell.authored
                    and path.suffix.lower() in PUBLISHED_SUFFIXES):
                path.unlink()
        copied = []
        for path in written:
            target = published_name(cell, path)
            if target is not None:
                shutil.copy2(path, cell.dir / target)
                copied.append(target)
        (cell.dir / "provenance.json").write_text(json.dumps({
            "cell": cell.name, "published": now(), "design": self.ws.design.name,
            "run": workspace.rel(self.ws.run_dir), "compare": verdict,
            "corners": self.corners, "pex_mode": self.pex_mode, "driver": self.driver,
            "agent": provenance(cell.layout_build),
        }, indent=2) + "\n")
        try:
            from gds_to_image import render_gds
            render_gds(str(views[".gds"]), str(cell.dir / f"{cell.name}.png"), title=cell.name)
            copied.append(f"{cell.name}.png")
        except Exception as exc:            # noqa: BLE001 - a picture never fails a publish
            warn(f"no layout PNG — {exc}")
        ok(f"published {len(copied)} view(s) to {workspace.rel(cell.dir)}")
        note("  " + "  ".join(copied) + "  provenance.json")
        note("the next synthesis maps against the measured Liberty, not the estimate")
        return True


# ---------------------------------------------------------------------
def floorplan_section(cell: Cell) -> str:
    """The hand-authored plan, verbatim, or an instruction to derive one."""
    try:
        text = cell.floorplan.read_text()
    except OSError:
        return ("There is no FLOORPLAN.md for this cell. Derive the floorplan the "
                "skill's way -- shared diffusion, columns, width, then draw -- and "
                "write it into the generator's module docstring so the next turn "
                "does not derive it again.\n")
    return "\n".join([
        f"=== THE FLOORPLAN — {cell.floorplan}, authored by hand ===",
        "The STRUCTURE in it -- device pairing, diffusion segments, node and gate "
        "coordinates, cell width, taps, pins -- is a DECISION. Implement it; do not "
        "re-derive it and do not silently depart from it. If you believe one of "
        "those numbers is wrong, say so in your final message and stop.",
        "The ROUTING is deliberately NOT decided. Draw the structure, run verify, "
        "and let the evidence packet's cross-net overlap table drive the routing. "
        "A broken GDS on disk beats a plan that is not on disk.",
        "",
        text[:40000],
        "",
    ])


_COMPARE_LINE = re.compile(
    r"^COMPARE:\s+(?P<verdict>WIN|LOSS)\s+area\s+(?P<area>[+-]?\d+(?:\.\d+)?)%"
    r".*?worst delay\s+(?P<delay>[+-]?\d+(?:\.\d+)?)%", re.MULTILINE)


def may_publish(output: str, accept_delay_loss: Optional[float]) -> tuple:
    """(allowed, accepted_loss, reason) from a post-layout run's output.

    A WIN is allowed.  A LOSS only with accept_delay_loss set, only when the
    cell is smaller, and only when its worst arc is at most that many percent
    slower.  A cell that is not smaller never qualifies: area is the only
    reason these cells exist.
    """
    results = [line.strip() for line in output.splitlines() if line.strip().startswith("RESULT:")]
    if results and results[-1] != "RESULT: PASS":
        return False, False, f"the mechanical chain re-graded the cell {results[-1]}"
    matches = list(_COMPARE_LINE.finditer(output))
    if not matches:
        return False, False, "the mechanical chain failed before the comparison"
    last = matches[-1]
    verdict, area, delay = last["verdict"], float(last["area"]), float(last["delay"])
    if verdict == "WIN":
        return True, False, "COMPARE: WIN"
    summary = f"area {area:+.1f}%, worst delay {delay:+.1f}%"
    if area >= 0:
        return False, False, f"COMPARE: LOSS and not smaller ({summary})"
    if accept_delay_loss is None:
        return False, False, f"COMPARE: LOSS ({summary})"
    if delay > accept_delay_loss:
        return False, False, (f"COMPARE: LOSS ({summary}), beyond "
                              f"ACCEPT_DELAY_LOSS {accept_delay_loss:g}")
    return True, True, (f"COMPARE: LOSS ACCEPTED ({summary}, within "
                        f"ACCEPT_DELAY_LOSS {accept_delay_loss:g})")


def published_name(cell: Cell, path: Path) -> Optional[str]:
    """An exported view's name once it sits next to the sources (None = skip)."""
    suffix = path.suffix.lower()
    if suffix not in REQUIRED_VIEWS + RECOMMENDED_VIEWS:
        return None
    if suffix == ".v":
        return f"{cell.name}.layout.v"      # <CELL>.v is the authored gold model
    if suffix == ".spice":
        if path.read_bytes() == cell.spice.read_bytes():
            return None                     # the exporter's copy of the source
        warn(f"the exported {path.name} differs from the netlist that went in — "
             f"publishing it as {cell.name}.layout.spice")
        return f"{cell.name}.layout.spice"
    return path.name


def run(builder: Builder, name: str, start: str = "characterize",
        only: Optional[str] = None) -> int:
    cell = builder.cell(name)
    cell.require_sources()
    todo = [only] if only else list(STAGES[STAGES.index(start):])
    info(f"{cell.name}: {' -> '.join(todo)}   (sources {workspace.rel(cell.dir)})")
    for stage in todo:
        if stage == "characterize":
            code = builder.characterize(cell)
        elif stage == "layout":
            code = builder.layout(cell)
        elif stage == PUBLISH:
            code = builder.publish(cell)
        else:
            code = builder.post_layout(cell)
        if code == 2:
            warn(f"stopped at {stage}: the cell does not verify yet")
            return 3
        if code:
            fail(f"{stage} failed")
            return 1
    ok(f"{cell.name}: {', '.join(todo)}")
    return 0


__all__ = ["Builder", "Cell", "run", "STAGES", "PUBLISH", "may_publish",
           "StepFailed"]
