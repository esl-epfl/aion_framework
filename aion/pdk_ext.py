# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               A design's PDK-extension cells, as the tools
#                             downstream of synthesis need to see them
#
#  Synthesis maps against the PDK's cells *plus* the hand-designed ones under
#  designs/<d>/pdk_extension/.  From then on the netlist can instantiate a
#  cell no stock file describes, and every tool that reads it has to be told:
#
#    <run>/pdk_extension/lib/<lib>_ext_<corner>.lib   ABC, OpenSTA, the LEC
#    <run>/pdk_extension/tech_dict/<lib>_ext.json     aion_opt (steps 2, 3)
#
#  Neither fails loudly when it is the stock one instead: kepler-formal stops
#  with "<CELL> cannot be found in SNL", which reads like a broken rewrite, and
#  aion_opt silently DROPS every instance whose type its dictionary lacks --
#  step 3 then writes a netlist with those outputs undriven.  So any step that
#  hands a netlist to one of them asks here first.
# ================================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, Optional

import merge_lib
import merge_tech_dict

from . import workspace
from .runner import StepFailed
from .style import info, note


class Views(NamedTuple):
    libraries: dict             # corner -> extended Liberty
    tech_dict: Optional[Path]   # extended technology dictionary
    cells: tuple = ()

    @property
    def active(self) -> bool:
        return bool(self.libraries)

    def library(self, corner: str) -> Optional[Path]:
        return self.libraries.get(corner)


NONE = Views({}, None, ())


def build(ws, provisional: bool = False, quiet: bool = False) -> Views:
    """Splice the extension cells into the PDK Liberty (per corner) and dictionary."""
    cells = ws.pdk_ext_cells()
    if not cells:
        return NONE
    pdk = ws.pdk
    libraries = {}
    for corner in pdk.corners:
        sources = []
        for cell in cells:
            try:
                path, how = merge_lib.extension_liberty(
                    cell, corner, ws.pdk_ext_dir, provisional=provisional,
                    typ_corner=pdk.typ_corner)
            except SystemExit as exc:
                raise StepFailed(str(exc).removeprefix("error: "), 2) from None
            sources.append((str(path), path.read_text()))
            if not quiet and corner == pdk.typ_corner:
                note(f"  {cell}: {how}  {workspace.rel(path)}")
        try:
            merged, _ = merge_lib.merge(pdk.liberty(corner).read_text(), sources)
        except SystemExit as exc:
            raise StepFailed(str(exc).removeprefix("error: "), 2) from None
        out = ws.pdk_ext_lib(corner)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(merged)
        libraries[corner] = out

    base = json.loads(pdk.tech_dict.read_text())
    merged_dict, added = merge_tech_dict.merge(base, libraries[pdk.typ_corner].read_text())
    ws.pdk_ext_dict.parent.mkdir(parents=True, exist_ok=True)
    ws.pdk_ext_dict.write_text(json.dumps(merged_dict, indent=2) + "\n")
    if not quiet:
        info(f"PDK extension: {len(cells)} cell(s) spliced — {', '.join(cells)}")
        note(f"  Liberty    {workspace.rel(libraries[pdk.typ_corner])} (+{len(libraries) - 1} corner(s))")
        note(f"  tech dict  {workspace.rel(ws.pdk_ext_dict)}  (+{len(added)} leaf cell(s))")
    return Views(libraries, ws.pdk_ext_dict, tuple(cells))


def views(ctx) -> Views:
    """The extension views for this run, built once per process."""
    if ctx.pdk_ext is not None:
        return ctx.pdk_ext
    if not ctx.cfg.PDK_EXT or not ctx.ws.pdk_ext_cells():
        ctx.pdk_ext = NONE
        return NONE
    if ctx.runner.dry_run:
        info(f"would splice {len(ctx.ws.pdk_ext_cells())} PDK-extension cell(s)")
        ctx.pdk_ext = Views({c: ctx.ws.pdk_ext_lib(c) for c in ctx.ws.pdk.corners},
                            ctx.ws.pdk_ext_dict, tuple(ctx.ws.pdk_ext_cells()))
        return ctx.pdk_ext
    ctx.pdk_ext = build(ctx.ws, provisional=ctx.ext_provisional)
    return ctx.pdk_ext
