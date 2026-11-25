# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               6_layout_drawing -- draw, verify and publish
#                             a standard cell per mined AION module
#
#  The only step with an irreducible agent in it.  Exactly one artifact per
#  cell is authored rather than computed:
#
#      library/<pdk>/generators/<CELL>.py     generate(name, tech) -> Cell
#
#  Everything else -- DRC, LVS, PEX, the abutted PDK baseline,
#  characterization, the comparison, the view export -- is mechanical and is
#  run here, never by the agent.  The agent's own report is never the
#  verdict; the verdict is this step's own `make verify`.
#
#    DRAW_MODE=manual   scaffold the generator, print the loop, stop.
#    DRAW_MODE=auto     drive the agent profile AGENT (agents/<name>/) until
#                       RESULT: PASS or the profile's turn budget.
#
#  The generators and the published views are SHARED by every design on the
#  PDK: a pattern drawn once is reused, under the same name, wherever it is
#  mined again (library/<pdk>/registry.json keeps the names stable).  Each
#  published cell carries provenance.json -- the design and run it was drawn
#  in, the agent profile, its digest, the model, turns and cost.
# ================================================================

from __future__ import annotations

import json
import shutil
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cell_registry
from collect_cells import LIB_CORNERS, check_lef, published_lib_problem

from .. import workspace
from ..agent import profile as agent_profile
from ..agent.draw import Drawer, DrawTask, NetlistGuard, provenance, turn_log
from ..coherence import BLOCKED, now
from ..pex import PexError, write_wrapper
from ..runner import terminate_all
from ..style import Style, color, emit, fail, info, note, ok, prefixed, warn
from .base import Context, Step, StepFailed

TAG_COLORS = (Style.CYAN, Style.MAGENTA, Style.YELLOW, Style.GREEN, Style.BLUE)
TAG_WIDTH = 18

#: What PnR demands of every published cell, and what it merely likes.
REQUIRED_VIEWS = (".lef", ".lib", ".gds")
RECOMMENDED_VIEWS = (".v", ".spice", ".cdl")


@dataclass
class CellOutcome:
    cell: str
    state: str                      # drawn | awaiting_draw | failed
    verdict: Optional[str] = None
    compare: Optional[str] = None
    published: bool = False
    pex_verified: Optional[bool] = None
    detail: str = ""


class LayoutDrawingStep(Step):
    key = "6_layout_drawing"
    title = "Layout drawing"
    summary = "draw a DRC/LVS-clean cell per AION module and publish its views"
    upstream = ("5_gate_minimization",)
    needs_container = True

    def inputs(self, ctx: Context) -> list:
        return [ctx.ws.step("5_gate_minimization") / "minimized_spice",
                ctx.ws.step("3_rewrite") / "nl" / "aion_cells.v"]

    def outputs(self, ctx: Context) -> list:
        return [self.outdir(ctx) / "summary.json"]

    # -----------------------------------------------------------------
    def run(self, ctx: Context) -> str:
        cfg, ws = ctx.cfg, ctx.ws
        step5 = ws.step("5_gate_minimization")
        cells_v = ws.step("3_rewrite") / "nl" / "aion_cells.v"
        self.require(step5 / "minimized_spice", cells_v)

        netlists = sorted((step5 / "minimized_spice").glob("*_minimized.spice"))
        if not netlists:
            if self.dry_run:
                info("no minimized netlists yet — nothing to preview")
                return "ok"
            raise StepFailed("step 5 produced no minimized netlists", 2)

        self.drawer = None
        if cfg.DRAW_MODE == "auto":
            profile = agent_profile.load(cfg.AGENT, ctx.agent_overrides)
            self.drawer = Drawer(ws, ctx.runner, profile)
            self.drawer.prepare()
            ctx.record["agent"] = {"profile": profile.name, "digest": profile.digest(),
                                   "model": profile.model, "effort": profile.effort,
                                   "overrides": profile.overrides}

        jobs = max(1, min(cfg.DRAW_JOBS, len(netlists)))
        info(f"{len(netlists)} cell(s)   mode={cfg.DRAW_MODE}   jobs={jobs}   "
             f"library {workspace.rel(ws.library)}")
        outcomes = self._draw_cells(ctx, netlists, cells_v, jobs)
        return self._summarize(ctx, outcomes)

    # -----------------------------------------------------------------
    def _draw_cells(self, ctx: Context, netlists: list, cells_v: Path,
                    jobs: int) -> list:
        if jobs <= 1 or self.dry_run:
            return [self._one_cell_guarded(ctx, n, cells_v) for n in netlists]
        cells = [_cell_of(n) for n in netlists]
        tags = _tags(cells)
        info(f"drawing {jobs} cell(s) at a time — every line is tagged with its cell")
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="draw") as pool:
            futures = [pool.submit(self._one_cell_guarded, ctx, netlist, cells_v, tags[cell])
                       for netlist, cell in zip(netlists, cells)]
            try:
                return [future.result() for future in futures]
            except KeyboardInterrupt:
                emit()
                fail("interrupted — stopping every cell")
                note(f"killed {terminate_all()} running command(s); "
                     "the drawn generators are still on disk")
                for future in futures:
                    future.cancel()
                raise

    def _one_cell_guarded(self, ctx: Context, netlist: Path, cells_v: Path,
                          tag: str = "") -> CellOutcome:
        """A failure is this cell's result, not the step's."""
        cell = _cell_of(netlist)
        with prefixed(tag):
            try:
                return self._one_cell(ctx, netlist, cells_v)
            except StepFailed as exc:
                fail(f"{cell}: {exc}")
                return CellOutcome(cell, "failed", detail=str(exc))
            except Exception as exc:                     # pragma: no cover
                fail(f"{cell}: {type(exc).__name__}: {exc}")
                emit(traceback.format_exc())
                return CellOutcome(cell, "failed", detail=f"{type(exc).__name__}: {exc}")

    # -----------------------------------------------------------------
    def _task(self, ctx: Context, cell: str, netlist: Path) -> DrawTask:
        return DrawTask(kind="layout", cell=cell,
                        generator=ctx.ws.generators_dir / f"{cell}.py",
                        netlist=netlist, build=self.outdir(ctx) / cell,
                        jobs=ctx.cfg.LAYOUT_JOBS)

    def _one_cell(self, ctx: Context, netlist: Path, cells_v: Path) -> CellOutcome:
        cfg, run, ws = ctx.cfg, ctx.runner, ctx.ws
        cell = _cell_of(netlist)
        baseline = ws.step("5_gate_minimization") / "raw_spice" / f"reference_{cell}.spice"
        task = self._task(ctx, cell, netlist)
        if not self.dry_run:
            task.build.mkdir(parents=True, exist_ok=True)
            task.generator.parent.mkdir(parents=True, exist_ok=True)

        emit()
        emit(color(f"  ── {cell} " + "─" * max(0, 60 - len(cell)), Style.CYAN, Style.BOLD))

        key = _canonical_key(cells_v, cell)
        owner = _registry_owner(ws, cell)
        if key is not None and owner is not None and owner != key:
            return CellOutcome(cell, "failed", detail=(
                f"{cell} is already the name of a different pattern in "
                f"{workspace.rel(ws.registry)}. Re-run step 2 so the registry "
                f"can rename it."))
        if not baseline.exists() and not self.dry_run:
            return CellOutcome(cell, "failed", detail="missing baseline netlist")
        if not self.dry_run:
            # Frozen before anything is drawn against it; checked after every
            # agent turn and once more before the cell is published.
            task.guard = NetlistGuard.take(netlist)

        # ---- the generator: scaffold it once, never overwrite a drawing.
        if not task.generator.exists():
            info(f"scaffolding {workspace.rel(task.generator)}")
            run.check(run.layout("scaffold", task.make_vars(), name=f"{cell}.scaffold"),
                      f"scaffolding {cell}")
            note("the scaffold is deliberately incomplete: no taps, no "
                 "source/drain contacts, no routing. Its first verify WILL fail.")
        else:
            info(f"generator {workspace.rel(task.generator)} (shared library)")

        if self.drawer is not None:
            verdict = self.drawer.draw(task)
        else:
            verdict = run.layout("verify", task.make_vars(),
                                 name=turn_log(cell, "verify", None)).verdict("RESULT:")

        if verdict != "RESULT: PASS":
            self._manual_instructions(ctx, task)
            return CellOutcome(cell, "awaiting_draw", verdict=verdict,
                               detail="layout does not verify yet")
        ok(f"{cell}: {verdict}")
        if task.guard is not None:
            task.guard.enforce(cell, task.build, "something during drawing")
        if key is not None:
            _remember(ws, cell, key)

        reused = self._reuse_published(ctx, task)
        if reused is not None:
            return reused

        flow = run.layout("flow", [*task.make_vars(), f"BASELINE={baseline}",
                                   f"CORNERS={cfg.LAYOUT_CORNERS}"],
                          name=f"{cell}.flow")
        compare = flow.verdict("COMPARE:")
        result = flow.verdict("RESULT:")
        if result and result != "RESULT: PASS":
            fail(f"{cell}: {result}")
            return CellOutcome(cell, "failed", verdict=result,
                               detail="the mechanical chain re-graded it FAIL")
        if compare is None and not flow.ok:
            fail(f"{cell}: the layout chain did not reach a verdict — "
                 f"see {workspace.rel(flow.log)}")
            return CellOutcome(cell, "failed", detail="no COMPARE: verdict")
        if compare:
            (ok if compare.startswith("COMPARE: WIN") else warn)(f"{cell}: {compare}")

        losing = bool(compare and compare.startswith("COMPARE: LOSS"))
        if losing and not cfg.DRAW_PUBLISH_ON_LOSS:
            warn(f"{cell}: not published (lost the comparison, DRAW_PUBLISH_ON_LOSS off)")
            return CellOutcome(cell, "drawn", verdict="RESULT: PASS",
                               compare=compare, detail="withheld on loss")

        refusal = self._publish(ctx, task, compare)
        pex_ok = self._verify_pex(ctx, task, cells_v) if cfg.LAYOUT_VERIFY_PEX else None
        if refusal is not None:
            return CellOutcome(cell, "failed", verdict="RESULT: PASS", compare=compare,
                               pex_verified=pex_ok, detail=f"not published: {refusal}")
        return CellOutcome(cell, "drawn", verdict="RESULT: PASS", compare=compare,
                           published=True, pex_verified=pex_ok)

    # -----------------------------------------------------------------
    def _reuse_published(self, ctx: Context, task: DrawTask) -> Optional[CellOutcome]:
        """Publish again from the Liberty already in the library, or None.

        Everything after a PASS -- PEX, baseline, characterization at every
        corner, the comparison -- depends only on the geometry.  When the GDS
        verify just built is the published one and the published Liberty is
        the set LAYOUT_CORNERS asks for, only the export and the publish
        checks run again.
        """
        cfg, cell = ctx.cfg, task.cell
        published = ctx.ws.cells_dir / cell
        if not cfg.LAYOUT_REUSE or not published.is_dir():
            return None
        libs = _published_libs(published, cell, cfg.LAYOUT_CORNERS)
        gds, built = published / f"{cell}.gds", task.build / f"{cell}.gds"
        if libs is None:
            note(f"{cell}: published Liberty is not the LAYOUT_CORNERS="
                 f"{cfg.LAYOUT_CORNERS} set — characterizing again")
            return None
        same = _same_layout(built, gds) if gds.is_file() else False
        if not same:
            note(f"{cell}: " + ("layout differs from the published one" if same is False
                                else "cannot compare layouts (no klayout on this host)")
                 + " — characterizing again")
            return None
        compare = _published_compare(task.build, cell) or _provenance_compare(published)
        if compare and compare.startswith("COMPARE: LOSS") and not cfg.DRAW_PUBLISH_ON_LOSS:
            return None
        info(f"{cell}: same layout as the published cell — reusing its "
             f"characterization; exporting and re-checking only")
        export = ctx.runner.layout(
            "export", [*task.make_vars(), "CANDIDATE_LIBS=" + " ".join(str(p) for p in libs)],
            name=f"{cell}.export")
        if export.verdict("STEP: export") != "STEP: export OK" and not self.dry_run:
            warn(f"{cell}: re-export from the published Liberty failed — "
                 "running the whole chain instead")
            return None
        refusal = self._publish(ctx, task, compare, reused=True)
        if refusal is not None:
            return CellOutcome(cell, "failed", verdict="RESULT: PASS", compare=compare,
                               detail=f"not published: {refusal}")
        return CellOutcome(cell, "drawn", verdict="RESULT: PASS", compare=compare,
                           published=True, detail="characterization reused")

    # -----------------------------------------------------------------
    def _publish(self, ctx: Context, task: DrawTask, compare: Optional[str],
                 reused: bool = False) -> Optional[str]:
        """Copy the exported views into library/<pdk>/cells/<CELL>/.  None = published."""
        cell, final = task.cell, task.final
        if self.dry_run:
            info(f"{cell}: would publish to {workspace.rel(ctx.ws.cells_dir / cell)}")
            return None
        if not final.is_dir():
            return f"nothing exported to {workspace.rel(final)}"
        rejected = sorted(final.glob("*.rejected"))
        if rejected:
            names = ", ".join(p.name for p in rejected)
            fail(f"{cell}: the exporter REFUSED to publish — {names}")
            return f"the exporter refused it ({names})"
        views = {p.suffix.lower(): p for p in final.iterdir() if p.is_file()}
        missing = [ext for ext in REQUIRED_VIEWS if ext not in views]
        if missing:
            return f"missing {', '.join(missing)}"
        problem = published_lib_problem(cell, [p.name for p in final.glob("*.lib")])
        if problem is not None:
            return problem
        problems = check_lef(str(views[".lef"]))
        if problems:
            for problem in problems:
                note(f"  {problem}")
            return f"the LEF would fail PnR: {problems[0]}"
        access = ctx.runner.layout("pin-access", task.make_vars(), name=f"{cell}.pin_access")
        verdict = access.verdict("STEP: pin_access") or access.verdict("RESULT: ERROR")
        if verdict != "STEP: pin_access OK":
            problems = [line.strip()[len("problem: "):] for line in access.output.splitlines()
                        if line.strip().startswith("problem: ")]
            return (f"TritonRoute cannot reach it: {problems[0]}"
                    if verdict == "STEP: pin_access FAIL" and problems
                    else "TritonRoute pin access did not run")

        target = ctx.ws.cells_dir / cell
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        copied = []
        for path in sorted(final.iterdir()):
            if path.is_file() and path.suffix.lower() in REQUIRED_VIEWS + RECOMMENDED_VIEWS:
                shutil.copy2(path, target / path.name)
                copied.append(path.name)
        record = {
            "cell": cell,
            "published": now(),
            "design": ctx.ws.design.name,
            "run": workspace.rel(ctx.ws.run_dir),
            "generator": workspace.rel(task.generator),
            "compare": compare,
            "corners": ctx.cfg.LAYOUT_CORNERS,
            "characterization_reused": reused,
            "agent": provenance(task.build),
        }
        (target / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
        ok(f"{cell}: published {len(copied)} view(s) to {workspace.rel(target)}")
        note("  " + "  ".join(copied) + "  provenance.json")
        try:
            from gds_to_image import render_gds
            render_gds(str(views[".gds"]), str(target / f"{cell}.png"), title=cell)
        except Exception as exc:            # noqa: BLE001 - a picture never fails a publish
            warn(f"{cell}: no layout PNG — {exc}")
        ctx.note(f"{cell} published")
        return None

    # -----------------------------------------------------------------
    def _verify_pex(self, ctx: Context, task: DrawTask, cells_v: Path) -> Optional[bool]:
        """Re-prove the cell's function on the EXTRACTED netlist, in SPICE."""
        cell, build = task.cell, task.build
        candidates = (sorted((build / "pex").glob(f"{cell}_pex*.spice"))
                      or sorted((build / "pex").glob("*.spice")))
        if not candidates:
            warn(f"{cell}: no PEX netlist to verify")
            return None
        wrapper = build / "verify" / f"{cell}_pex_wrapped.spice"
        try:
            write_wrapper(candidates[0], cell, cells_v, wrapper)
        except PexError as exc:
            fail(f"{cell}: cannot build a PEX verification wrapper — {exc}")
            return False
        info(f"{cell}: verifying the extracted netlist in SPICE")
        result = ctx.runner.container("aion-char-verify-spice", [
            f"CELL={cell}",
            f"SPICE={workspace.container(wrapper)}",
            f"NETLIST={workspace.container(cells_v)}",
            f"BUILD_DIR={workspace.container(build / 'verify')}",
        ], name=f"{cell}.pex-verify")
        if result.ok:
            ok(f"{cell}: extracted layout matches the cell function")
            return True
        fail(f"{cell}: PEX SPICE verification FAILED — see {workspace.rel(result.log)}")
        return False

    # -----------------------------------------------------------------
    def _manual_instructions(self, ctx: Context, task: DrawTask) -> None:
        emit()
        warn(f"{task.cell} is waiting for a layout")
        note(f"draw   {workspace.rel(task.generator)}")
        note("then, from anywhere:")
        note(f"  export AION_CONTAINER_MOUNT={workspace.container(workspace.AION_FLOW)}")
        note(f"  {task.tool_command('verify')}")
        note(f"  {task.tool_command('evidence')}")
        note("repeat until `RESULT: PASS`, then re-run the step:")
        note(f"  make flow DESIGN={ctx.ws.design.name} STEP=6")
        note(f"or let an agent do it:  make flow DESIGN={ctx.ws.design.name} STEP=6 "
             f"SET=DRAW_MODE=auto [AGENT=<profile>]")

    # -----------------------------------------------------------------
    def _summarize(self, ctx: Context, outcomes: list) -> str:
        drawn = [o for o in outcomes if o.state == "drawn"]
        waiting = [o for o in outcomes if o.state == "awaiting_draw"]
        broken = [o for o in outcomes if o.state == "failed"]
        published = [o for o in drawn if o.published]

        emit()
        info(f"{len(published)}/{len(outcomes)} cell(s) published to "
             f"{workspace.rel(ctx.ws.cells_dir)}")
        for outcome in outcomes:
            bits = [outcome.state]
            if outcome.compare:
                bits.append(outcome.compare.replace("COMPARE: ", ""))
            if outcome.pex_verified is True:
                bits.append("pex ok")
            elif outcome.pex_verified is False:
                bits.append("pex FAILED")
            if outcome.detail:
                bits.append(f"— {_clip(outcome.detail)}")
            note(f"  {outcome.cell:<34} {'  '.join(bits)}")
            ctx.note(f"{outcome.cell}: {'  '.join(bits)}")

        if not self.dry_run:
            summary = {"finished": now(), "mode": ctx.cfg.DRAW_MODE,
                       "agent": ctx.record.get("agent"),
                       "cells": [o.__dict__ for o in outcomes]}
            for o in summary["cells"]:
                rec = self.outdir(ctx) / o["cell"] / "agent.json"
                try:
                    data = json.loads(rec.read_text())
                    o["agent_turns"] = data.get("turns")
                    o["agent_cost_usd"] = data.get("cost_usd")
                except (OSError, ValueError):
                    pass
            self.outdir(ctx).mkdir(parents=True, exist_ok=True)
            (self.outdir(ctx) / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

        if broken:
            raise StepFailed(f"{len(broken)} cell(s) failed: " + "; ".join(
                f"{o.cell} ({_clip(o.detail)})" if o.detail else o.cell for o in broken), 1)
        if waiting:
            warn(f"{len(waiting)} cell(s) still need a layout. Step 7 will REFUSE to "
                 "run until they are published: the netlist instantiates them.")
            return BLOCKED
        if not published and not self.dry_run:
            raise StepFailed("no cell was published; step 7 has nothing to place", 1)
        return "ok"


# ---------------------------------------------------------------------
def _canonical_key(cells_v: Path, cell: str) -> Optional[str]:
    try:
        return cell_registry.keys_by_name(cells_v).get(cell)
    except OSError:
        return None


def _registry_owner(ws, cell: str) -> Optional[str]:
    for key, name in cell_registry.load(ws.registry).items():
        if name == cell:
            return key
    return None


def _remember(ws, cell: str, key: str) -> None:
    """Bind this name to this pattern once the layout verifies (not publishes)."""
    try:
        message = cell_registry.record(key, cell, ws.registry)
    except ValueError as exc:
        warn(f"{cell}: not recorded — {exc}")
        return
    if message:
        note(f"  registry: {message}")


def _published_libs(directory: Path, cell: str, corners: str) -> Optional[list]:
    names = ([f"{cell}_{corner}.lib" for corner in LIB_CORNERS]
             if corners == "all" else [f"{cell}.lib"])
    if sorted(p.name for p in directory.glob("*.lib")) != sorted(names):
        return None
    return [directory / name for name in names]


def _same_layout(a: Path, b: Path) -> Optional[bool]:
    """Same geometry (not bytes: every build stamps a new GDS header)."""
    try:
        import klayout.db as pya
    except ImportError:
        return None
    try:
        layouts = []
        for path in (a, b):
            layout = pya.Layout()
            layout.read(str(path))
            layouts.append(layout)
        return bool(pya.LayoutDiff().compare(*layouts))
    except Exception:                    # noqa: BLE001 - unreadable is "not the same"
        return False


def _published_compare(build: Path, cell: str) -> Optional[str]:
    try:
        return json.loads((build / f"{cell}.compare.json").read_text()).get("verdict_line")
    except (OSError, ValueError):
        return None


def _provenance_compare(published: Path) -> Optional[str]:
    try:
        return json.loads((published / "provenance.json").read_text()).get("compare")
    except (OSError, ValueError):
        return None


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cell_of(netlist: Path) -> str:
    return netlist.name[: -len("_minimized.spice")]


def _tags(cells: list) -> dict:
    short = {cell: (cell[5:] if cell.startswith("AION_") else cell)[:TAG_WIDTH] for cell in cells}
    width = max((len(s) for s in short.values()), default=0)
    return {cell: color(f"[{short[cell]:<{width}}] ", TAG_COLORS[i % len(TAG_COLORS)])
            for i, cell in enumerate(cells)}


STEP = LayoutDrawingStep()
