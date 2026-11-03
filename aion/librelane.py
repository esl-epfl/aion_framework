# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               LibreLane: build a run's config.json from the
#                             design's template, run it in the container,
#                             and keep what it produced
#
#  The design ships designs/<d>/librelane/config.json: the LibreLane keys that
#  are its own (clock, die, density, strategy) plus a `_modes` table of
#  overlays per mode.  This module never edits it.  For every run it writes
#  <run>/librelane/<mode>/config.json with:
#
#    * the sources, as dir:: paths, under the key the language needs
#      (VHDL_FILES for VHDLClassic, VERILOG_FILES for Classic, USE_SLANG for
#      SystemVerilog)
#    * the SDC, pin order or DEF template the design names
#    * CELL_LIBS for synthesis (the PDK library + this design's extension cells)
#    * EXTRA_* views for PnR (the drawn mined cells + the extension cells)
#    * the overlay for the mode, then the `lenient` overlay if asked for
#
#  Three modes:  synth (synthesis + pre-PnR STA, stop), pnr (from a fixed
#  netlist, the AION cells in it), baseline (RTL -> GDS, PDK cells only).
# ================================================================

from __future__ import annotations

import json
import os
import re
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Optional

from collect_cells import (EXTRA_KEY_BY_VIEW, collect_cells,
                           collect_pdk_extension_cells, corner_lib_problems)

from . import workspace
from .runner import Runner, StepFailed
from .settings import get as get_settings
from .style import info, note

MODES = ("synth", "pnr", "baseline")
#: Older templates (aion_chip's) call the baseline overlay `pnr_simple`.
MODE_ALIASES = {"baseline": ("baseline", "pnr_simple")}

#: Synthesis proper, its checkers, then a pre-PnR STA so the SDC is exercised
#: and a timing report lands next to the netlist.
SYNTH_ONLY_FLOW = {
    "vhdl": ["Yosys.VHDLSynthesis"],
    "verilog": ["Yosys.JsonHeader", "Yosys.Synthesis"],
    "systemverilog": ["Yosys.JsonHeader", "Yosys.Synthesis"],
}
SYNTH_CHECKS = [
    "Checker.YosysUnmappedCells",
    "Checker.YosysSynthChecks",
    "Checker.NetlistAssignStatements",
    "OpenROAD.CheckSDCFiles",
    "OpenROAD.STAPrePNR",
]

#: Template keys that are ours, not LibreLane's.  Any key starting with "//"
#: is a comment.
TEMPLATE_ONLY_KEYS = ("_modes",)
COMMENT_PREFIX = "//"

#: Keys this module owns.  A template that sets one is overridden, loudly.
OWNED_KEYS = ("DESIGN_NAME", "VHDL_FILES", "VERILOG_FILES", "VERILOG_INCLUDE_DIRS",
              "VERILOG_DEFINES", "USE_SLANG", "PNR_SDC_FILE", "SIGNOFF_SDC_FILE",
              "IO_PIN_ORDER_CFG", "FP_DEF_TEMPLATE", "CELL_LIBS", "LIB")


def _template_only(key) -> bool:
    return isinstance(key, str) and (key in TEMPLATE_ONLY_KEYS
                                     or key.startswith(COMMENT_PREFIX))


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not _template_only(k)}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def _overlay(config: dict, overlay: dict, label: str) -> None:
    for key, value in overlay.items():
        if _template_only(key):
            continue
        if key == "meta" and isinstance(value, dict):
            config.setdefault("meta", {}).update(value)
        else:
            config[key] = value
    keys = sorted(k for k in overlay if not _template_only(k))
    if keys:
        note(f"overlay '{label}': {', '.join(keys)}")


def dir_path(path, base: Path) -> str:
    """A path as LibreLane's dir:: prefix spells it, relative to the run dir.

    Refuses anything outside the framework: only that tree is mounted into
    the container, so a path that escapes it resolves to nothing there.
    """
    workspace.container(path)          # raises PathError outside the tree
    return "dir::" + os.path.relpath(Path(path).resolve(), Path(base).resolve())


def def_die_area(path: Path) -> list:
    units = None
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", line)
        if m:
            units = int(m.group(1))
            continue
        m = re.match(r"\s*DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)"
                     r"\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;", line)
        if m and units:
            return [Decimal(v) / units for v in m.groups()]
    raise StepFailed(f"no UNITS and DIEAREA statements in {path}", 2)


def cell_views(sources, run_dir: Path, pdk_cell_libs: dict, corners: tuple,
               cell_libs: Optional[dict]):
    """EXTRA_* views and the CELL_LIBS map for the cells PnR is handed.

    A cell's single <CELL>.lib goes to EXTRA_LIBS, which LibreLane loads into
    every corner alike.  A Liberty per corner cannot go there -- every corner
    would read all of them -- so each is appended to its own corner's
    CELL_LIBS entry, after the PDK's libraries, which stay exactly as they
    were: the PDK cells are timed against the same files as in the baseline,
    so the substitution stays the only variable between the two runs.
    """
    views: dict = {}
    per_corner: dict = {}
    for cells in sources:
        for cell in cells:
            errors, _ = corner_lib_problems(cell)
            if errors:
                raise StepFailed(errors[0], 2)
            for view, path in cell.views.items():
                views.setdefault(EXTRA_KEY_BY_VIEW[view], []).append(dir_path(path, run_dir))
            for corner, path in cell.corner_libs.items():
                per_corner.setdefault(corner, []).append(dir_path(path, run_dir))
    if not per_corner:
        return views, cell_libs
    merged = {key: list(libs) for key, libs in (
        cell_libs or {f"*_{c}": pdk_cell_libs[c] for c in corners}).items()}
    for corner, libs in sorted(per_corner.items()):
        key = f"*_{corner}"
        if key not in merged:
            raise StepFailed(f"CELL_LIBS has no {key!r} entry for the {corner} "
                             f"Liberty of the cells", 2)
        merged[key] += sorted(libs)
    return views, merged


# ---------------------------------------------------------------------
def prepare(ws, cfg, mode: str, *, ext_libs: Optional[dict] = None,
            cells_used: Optional[set] = None,
            ext_cells_used: Optional[set] = None) -> Path:
    """Write <run>/librelane/<mode>/config.json and return the run directory.

    ext_libs   synth: corner -> extended Liberty (the PDK + extension cells)
    cells_used     pnr: the mined cells the netlist instantiates; only those
                   are taken from the shared library
    ext_cells_used pnr: the drawn extension cells the netlist instantiates
    """
    if mode not in MODES:
        raise ValueError(mode)
    design, pdk = ws.design, ws.pdk
    run_dir = ws.librelane_run(mode)
    run_dir.mkdir(parents=True, exist_ok=True)

    template = json.loads(design.librelane_config.read_text())
    modes = template.get("_modes", {})
    config = _strip(template)
    info(f"LibreLane config ({mode}) from {workspace.rel(design.librelane_config)}")

    clash = sorted(k for k in OWNED_KEYS if k in config)
    if clash:
        note(f"set by the framework, ignored in the template: {', '.join(clash)}")
        for key in clash:
            config.pop(key)
    for key in list(config):
        if key.startswith("EXTRA_"):
            config.pop(key)

    config["DESIGN_NAME"] = design.top
    meta = config.setdefault("meta", {})
    meta.setdefault("version", 2)

    # ---- sources
    files = [dir_path(p, run_dir) for p in design.rtl]
    if design.language == "vhdl":
        meta["flow"] = "VHDLClassic"
        config["VHDL_FILES"] = files
    else:
        meta["flow"] = "Classic"
        config["VERILOG_FILES"] = files
        if design.include_dirs:
            config["VERILOG_INCLUDE_DIRS"] = [dir_path(p, run_dir) for p in design.include_dirs]
        if design.defines:
            config["VERILOG_DEFINES"] = [f"{k}={v}" if v not in (None, "", True) else k
                                         for k, v in design.defines.items()]
        if design.language == "systemverilog":
            config["USE_SLANG"] = True

    # ---- constraints and pins
    sdc = dir_path(design.sdc, run_dir)
    config["PNR_SDC_FILE"] = sdc
    config["SIGNOFF_SDC_FILE"] = sdc
    note(f"sdc: {workspace.rel(design.sdc)}")
    if design.def_template is not None:
        config["FP_DEF_TEMPLATE"] = dir_path(design.def_template, run_dir)
    elif design.pin_order is not None and design.pin_order.is_file():
        config["IO_PIN_ORDER_CFG"] = dir_path(design.pin_order, run_dir)

    # ---- the standard-cell Liberty the mapper reads (synthesis only)
    cell_libs: dict = {}
    if mode == "synth" and ext_libs:
        for corner, lib in sorted(ext_libs.items()):
            cell_libs[f"*_{corner}"] = [dir_path(lib, run_dir),
                                        *pdk.librelane_cell_libs[corner][1:]]
        config["CELL_LIBS"] = cell_libs

    # ---- leaf cells that are not the PDK's (PnR only)
    sources = []
    if mode == "pnr":
        if ws.cells_dir.is_dir() and cells_used:
            mined = [c for c in collect_cells(str(ws.cells_dir)) if c.name in cells_used]
            sources.append(mined)
            note(f"cells: {len(mined)} from {workspace.rel(ws.cells_dir)} "
                 f"({', '.join(c.name for c in mined) or '—'})")
        if ext_cells_used and ws.pdk_ext_dir.is_dir():
            ext = [c for c in collect_pdk_extension_cells(str(ws.pdk_ext_dir))
                   if c.name in ext_cells_used]
            sources.append(ext)
            note(f"cells: {len(ext)} PDK-extension ({', '.join(c.name for c in ext)})")
        views, merged = cell_views(sources, run_dir, pdk.librelane_cell_libs,
                                   pdk.corners, cell_libs or None)
        for key, paths in sorted(views.items()):
            config[key] = sorted(paths)
        if merged:
            config["CELL_LIBS"] = merged

    # ---- overlays
    for name in MODE_ALIASES.get(mode, (mode,)):
        if name in modes:
            _overlay(config, modes[name], name)
            break
    if cfg.LENIENT:
        _overlay(config, modes.get("lenient", LENIENT_OVERLAY), "lenient")

    # ---- the die against the DEF template
    if design.def_template is not None:
        area = def_die_area(design.def_template)
        die = config.get("DIE_AREA")
        if die is None:
            config["DIE_AREA"] = [float(v) for v in area]
        elif [Decimal(str(v)) for v in die] != area:
            raise StepFailed(
                f"DIE_AREA {die} is not the die of {workspace.rel(design.def_template)} "
                f"({[float(v) for v in area]}); the template's pins only line up "
                f"with its own die", 2)

    if mode == "synth":
        meta["flow"] = SYNTH_ONLY_FLOW[design.language] + SYNTH_CHECKS
        meta.pop("substituting_steps", None)

    (run_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return run_dir


#: Used when a template has no `lenient` overlay of its own.
LENIENT_OVERLAY = {
    "ERROR_ON_TR_DRC": False,
    "ERROR_ON_DISCONNECTED_PINS": False,
    "ERROR_ON_LONG_WIRE": False,
    "ERROR_ON_PDN_VIOLATIONS": False,
    "ERROR_ON_NL_ASSIGN_STATEMENTS": False,
    "ERROR_ON_SYNTH_CHECKS": False,
    "ERROR_ON_UNMAPPED_CELLS": False,
    "ERRORS_ON_UNMATCHED_IO": "none",
    "ERROR_ON_MAGIC_DRC": False,
    "ERROR_ON_KLAYOUT_DRC": False,
    "ERROR_ON_ILLEGAL_OVERLAPS": False,
    "ERROR_ON_XOR_ERROR": False,
    "ERROR_ON_LVS_ERROR": False,
    "TIMING_VIOLATION_CORNERS": [],
    "SETUP_VIOLATION_CORNERS": [],
    "HOLD_VIOLATION_CORNERS": [],
    "MAX_SLEW_VIOLATION_CORNERS": [],
    "MAX_CAP_VIOLATION_CORNERS": [],
    "GRT_ALLOW_CONGESTION": True,
}


# ---------------------------------------------------------------------
def run(ws, runner: Runner, mode: str, out_dir: Path, *,
        extra_args: str = "", name: str = "") -> None:
    """Run LibreLane in the container, then save its views into out_dir.

    The views are saved whatever the exit status, so a run that dies late
    still leaves its reports to look at; the status is raised afterwards.
    """
    run_dir = ws.librelane_run(mode)
    s = get_settings()
    command = (f"librelane config.json --pdk {ws.pdk.name} --pdk-root {s.pdk_root} "
               f"--manual-pdk --save-views-to ./final/ {extra_args}").strip()
    result = runner.container_shell(command, cwd=run_dir, name=name or f"librelane.{mode}")
    if runner.dry_run:
        return
    saved = save(run_dir, out_dir)
    if saved:
        info(f"saved {workspace.rel(saved)} -> {workspace.rel(out_dir)}/")
    runner.check(result, f"LibreLane ({mode})")
    if not saved:
        raise StepFailed(f"LibreLane ({mode}) left no run under "
                         f"{workspace.rel(run_dir)}/runs/", 2)


def save(run_dir: Path, out_dir: Path) -> Optional[Path]:
    """Copy the views, metrics and reports of the latest run into out_dir."""
    runs = sorted((run_dir / "runs").glob("RUN_*"))
    if not runs:
        return None
    latest = runs[-1]
    run_root = workspace.RUNS_DIR.resolve()
    if run_root not in Path(out_dir).resolve().parents:
        raise StepFailed(f"refusing to replace {out_dir}: not under {run_root}", 2)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "reports").mkdir(parents=True)
    (out_dir / "logs").mkdir()
    final = latest / "final"
    if final.is_dir():
        shutil.copytree(final, out_dir, dirs_exist_ok=True)
    for name in ("flow.log", "resolved.json"):
        if (latest / name).is_file():
            shutil.copy2(latest / name, out_dir / "logs" / name)
    for rpt in latest.rglob("*.rpt"):
        relative = rpt.relative_to(latest)
        if len(relative.parts) < 2:
            continue
        target = out_dir / "reports" / str(relative).replace("/reports/", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rpt, target)
    (out_dir / "logs" / "librelane_run.txt").write_text(f"{workspace.rel(latest)}\n")
    return latest
