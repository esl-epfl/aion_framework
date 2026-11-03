# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The functional cell extractor
#
#  Step 2's miner is structural: it merges cells that already sit next to
#  each other.  This package finds FUNCTIONS -- the Boolean functions a
#  netlist builds out of several cells, grouped by NPN class and priced by
#  the area one cell of each would free -- and turns the best of them into
#  hand-designed cells for one design:
#
#    rank_functions   rank the candidates                     (make ext-rank)
#    new_cell         the authored skeleton of a new one      (make ext-new)
#    build            characterize, draw, publish it          (make ext-build)
#    pdk_ext (aion/)  splice them into the mapper's library   (make ext-lib)
#    explore_cells    sweep the step-2/3 mining knobs         (make explore)
# ================================================================

from __future__ import annotations

import re
import string
from pathlib import Path

from .. import workspace
from ..style import note, ok

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def new_cell(ws, name: str, force: bool = False) -> Path:
    if not NAME_RE.match(name):
        raise SystemExit(f"error: {name!r} is not a valid cell name")
    dest = ws.pdk_ext_dir / name
    if dest.exists() and any(dest.iterdir()) and not force:
        raise SystemExit(f"error: {workspace.rel(dest)} already exists")
    dest.mkdir(parents=True, exist_ok=True)
    source = workspace.TEMPLATES_DIR / "pdk_extension"
    variables = {"cell": name, "design": ws.design.name, "pdk": ws.pdk.name,
                 "lib": ws.pdk.std_cell_library}
    written = []
    for path in sorted(source.glob("*.tmpl")):
        target = dest / path.name[: -len(".tmpl")].replace("CELL", name)
        if target.exists() and not force:
            continue
        target.write_text(string.Template(path.read_text()).substitute(variables))
        written.append(target)
    ok(f"extension cell skeleton {name} in {workspace.rel(dest)}")
    for target in written:
        note(f"  {workspace.rel(target)}")
    note("fill in the .v (function), the .spice (transistors), the provisional")
    note("Liberty (an area estimate for the mapper) and FLOORPLAN.md, then:")
    note(f"  make ext-build DESIGN={ws.design.name} CELL={name}")
    note("designs/<d>/pdk_extension/ is git-ignored: `git add -f` to keep a cell")
    return dest
