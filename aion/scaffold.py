# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               `make new-design` -- a design directory that
#                             runs through the whole flow as it stands
#
#  Everything comes from templates/design/.  A file there ending in .tmpl is
#  rendered with string.Template ($name, $top, ...) and written without the
#  suffix; "TOP" in a file name becomes the top-level module name.  RTL
#  templates are per language (src/rtl/TOP.<lang>.tmpl) and only the chosen
#  one is written.
# ================================================================

from __future__ import annotations

import re
import string
from pathlib import Path

from . import design as design_mod
from . import workspace
from .style import note, ok

LANG_TEMPLATE = {"vhdl": ("vhdl", ".vhd"), "verilog": ("v", ".v"),
                 "systemverilog": ("sv", ".sv")}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def new_design(name: str, language: str = "verilog", top: str | None = None,
               clock_port: str = "clk", period_ns: float = 20.0,
               die: tuple = (150.0, 150.0), pdk: str = "ihp-sg13g2",
               agent: str | None = None, force: bool = False) -> Path:
    language = language.lower()
    if language in ("sv",):
        language = "systemverilog"
    if language in ("vhd",):
        language = "vhdl"
    if language not in LANG_TEMPLATE:
        raise SystemExit(f"error: LANG={language!r}: one of vhdl, verilog, systemverilog")
    if not NAME_RE.match(name):
        raise SystemExit(f"error: design name {name!r}: letters, digits and _ only, "
                         f"starting with a letter")
    top = top or name
    if not NAME_RE.match(top):
        raise SystemExit(f"error: top {top!r} is not a valid module name")
    dest = design_mod.DESIGNS_DIR / name
    if dest.exists() and not force:
        raise SystemExit(f"error: {workspace.rel(dest)} already exists (FORCE=1 to "
                         f"overwrite the template files; your other files are kept)")

    source = workspace.TEMPLATES_DIR / "design"
    lang_key, suffix = LANG_TEMPLATE[language]
    variables = {
        "name": name, "top": top, "language": language, "suffix": suffix,
        "clock_port": clock_port, "period_ns": f"{period_ns:g}",
        "die_w": f"{die[0]:g}", "die_h": f"{die[1]:g}", "pdk": pdk,
        "agent": agent or "",
        "agent_line": f'agent = "{agent}"' if agent else
                      '# agent = "claude-opus"    # default: aion.toml [defaults] agent',
        "rtl_sim": "ghdl" if language == "vhdl" else
                   ("verilator" if language == "systemverilog" else "icarus"),
    }
    written = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        parts = list(rel.parts)
        leaf = parts[-1]
        # Per-language RTL: keep only TOP.<lang>.tmpl for the chosen one.
        m = re.match(r"^TOP\.(vhdl|v|sv)\.tmpl$", leaf)
        if m:
            if m.group(1) != lang_key:
                continue
            leaf = f"{top}{suffix}"
        else:
            leaf = leaf.replace("TOP", top)
            if leaf.endswith(".tmpl"):
                leaf = leaf[: -len(".tmpl")]
        target = dest.joinpath(*parts[:-1], leaf)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text()
        if path.name.endswith(".tmpl"):
            text = string.Template(text).substitute(variables)
        target.write_text(text)
        written.append(target)

    ok(f"new design {name} ({language}, top {top}) in {workspace.rel(dest)}")
    for target in written:
        note(f"  {workspace.rel(target)}")
    note("")
    note("next:")
    note(f"  $EDITOR {workspace.rel(dest / 'src' / 'rtl')}/      # your RTL")
    note(f"  make sim    DESIGN={name}                       # RTL testbench")
    note(f"  make config DESIGN={name}                       # resolved settings")
    note(f"  make flow   DESIGN={name}                       # the whole flow")
    return dest
