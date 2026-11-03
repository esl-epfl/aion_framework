#!/usr/bin/env python3
# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Splice PDK-extension cells into the standard
#                             cell Liberty the technology mapper reads
#
#  A Liberty file is one `library (NAME) { ... }` block holding the unit
#  declarations, the lookup-table templates and then every cell.  Adding a
#  cell means putting its `cell (...) { ... }` block *inside* that brace --
#  concatenating two .lib files does not do it, that produces two library
#  blocks and yosys reads the first one and ignores the rest, silently.
#
#  Why this exists at all.  LibreLane already has a way to hand extra cells
#  to the flow -- EXTRA_LIBS, which is what `--cells-dir` fills in -- but it
#  reads them as
#
#      read_liberty -lib -ignore_miss_dir -setattr blackbox <lib>
#
#  (librelane/steps/yosys.py:148).  That registers the cell for STA and for
#  hierarchy resolution and hides it from the technology mapper, which is
#  right for a macro and wrong for a standard cell.  The list ABC actually
#  maps against is CELL_LIBS (librelane/steps/pyosys.py:296), so a cell that
#  is to be *synthesized* has to be in the file that variable points at.
#
#  The templates matter.  A cell's timing tables reference lu_table_template
#  names by string, and a template that is not in the same library is a
#  dangling reference, so any template the extension defines and the base
#  does not is copied across too.
# ================================================================

from __future__ import annotations

import re
from pathlib import Path

from collect_cells import LIB_CORNERS


#: Group headers whose blocks live directly under `library (...)`.
_TEMPLATE_KINDS = ("lu_table_template", "power_lut_template", "operating_conditions")


def _blocks(text: str, kind: str) -> "list[tuple[str, str]]":
    """Every `<kind> (name) { ... }` block, brace-matched, as (name, text).

    Liberty has no nesting rules a regex can be trusted with -- timing groups
    contain values with braces in them -- so the opening line is found by
    pattern and the extent by counting braces.
    """
    found = []
    for match in re.finditer(rf"^([ \t]*){re.escape(kind)}\s*\(\s*([^)]*?)\s*\)\s*\{{",
                             text, re.MULTILINE):
        start = match.start()
        depth = 0
        for i in range(text.index("{", start), len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    found.append((match.group(2), text[start:i + 1]))
                    break
    return found


def cell_blocks(text: str) -> "list[tuple[str, str]]":
    """Every `cell (NAME) { ... }` block in a Liberty, as (name, text).

    Public because scripts/merge_tech_dict.py reads the *merged* library this
    script writes: the technology dictionary aion_opt mines and rewrites with
    has to describe the same cells the mapper chose from, and there is exactly
    one file that says what those are.
    """
    return _blocks(text, "cell")


def pin_blocks(text: str) -> "list[tuple[str, str]]":
    """Every `pin (NAME) { ... }` block, brace-matched, as (name, text)."""
    return _blocks(text, "pin")


def _library_close(text: str) -> int:
    """Index of the `}` that closes the outer `library (...)` block."""
    open_at = text.index("{", re.search(r"\blibrary\s*\(", text).start())
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces: no closing brace for `library`")


def merge(base_text: str, extensions: "list[tuple[str, str]]",
          replace: bool = False) -> "tuple[str, list[str]]":
    """Return the merged Liberty and the names of the cells added."""
    have_cells = {name for name, _ in _blocks(base_text, "cell")}
    have_templates = {name for kind in _TEMPLATE_KINDS
                      for name, _ in _blocks(base_text, kind)}

    added: list[str] = []
    additions: list[str] = []
    for source, text in extensions:
        for kind in _TEMPLATE_KINDS:
            for name, block in _blocks(text, kind):
                if name not in have_templates:
                    have_templates.add(name)
                    additions.append(block)
        for name, block in _blocks(text, "cell"):
            if name in have_cells:
                if not replace:
                    raise SystemExit(
                        f"error: {source} defines cell {name!r}, which the base "
                        f"library already has.\n"
                        f"       Two cells of the same name make the mapper's "
                        f"choice depend on read order.\n"
                        f"       Rename the extension cell, or pass --replace "
                        f"to drop the base one.")
                base_text = base_text.replace(
                    dict(_blocks(base_text, "cell"))[name], "", 1)
            have_cells.add(name)
            added.append(name)
            additions.append(block)

    if not additions:
        return base_text, []
    close = _library_close(base_text)
    banner = ("\n  /* ---- PDK extension cells, spliced in by "
              "scripts/merge_lib.py ---- */\n")
    return (base_text[:close] + banner + "\n".join(additions) + "\n"
            + base_text[close:], added)


def extension_liberty(cell: str, corner: str, root: Path,
                      provisional: bool = False,
                      typ_corner: str = "typ_1p20V_25C") -> "tuple[Path, str]":
    """The Liberty to splice for one extension cell at one corner, and why that one.

    A cell published per corner (scripts/pdk_cell.py --corners all) is spliced
    with that corner's own <CELL>_<corner>.lib, so the slow library the mapper
    and the pre-PnR STA read carries slow tables for it. A cell published once
    (--corners typ) is spliced with its <CELL>.lib, which then puts typ tables
    into every corner. A per-corner set missing this corner is an error, not a
    quiet fall-back to another corner's data. The characterized Liberty always
    wins over the provisional estimate, which is only used when asked for, so
    a stale estimate can never quietly outrank a measurement.
    """
    directory = root / cell
    own = directory / f"{cell}_{corner}.lib"
    if own.exists():
        return own, "characterized"
    corner_set = [c for c in LIB_CORNERS if (directory / f"{cell}_{c}.lib").exists()]
    if corner_set:
        raise SystemExit(
            f"error: {cell} is published per corner ({', '.join(corner_set)}) "
            f"but has no Liberty for {corner}.\n"
            f"       Re-run `make ext-build CELL={cell} FROM=post-layout`.")
    single = directory / f"{cell}.lib"
    if single.exists():
        return single, ("characterized" if corner == typ_corner
                        else "characterized at typ only")
    estimate = directory / f"{cell}.provisional.lib"
    if provisional and estimate.exists():
        return estimate, "PROVISIONAL"
    raise SystemExit(
        f"error: {cell} has no characterized Liberty for {corner} in {directory}.\n"
        f"       Draw it (`make ext-build CELL={cell}`), or set EXT_PROVISIONAL=1 "
        f"to map against the pre-layout estimate.")
