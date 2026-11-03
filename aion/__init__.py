# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               AION framework -- netlist-to-layout flow handler
#
#  The package drives the tools in the aion_flow submodule over any design
#  under designs/.  Nothing in here knows a design by name: a design is a
#  directory with a design.toml, and everything the flow produces for it lands
#  under runs/<design>/<tag>/.
# ================================================================

import sys
from pathlib import Path

#: The flat helper modules (collect_cells, merge_lib, ...) import each other
#: by bare name, the way they did as scripts.  Putting their directory on the
#: path once, here, keeps that working without rewriting every import.
_TOOLS = Path(__file__).resolve().parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

__version__ = "0.1.0"
