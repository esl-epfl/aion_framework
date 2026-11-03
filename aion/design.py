# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               designs/<name>/design.toml -- one design
#
#  A design is a directory:
#
#    designs/<name>/
#      design.toml            this file: top, language, sources, agent, knobs
#      src/rtl/               the RTL (VHDL, Verilog or SystemVerilog)
#      tb/                    cocotb testbench, run on RTL and on every netlist
#      constraints/<top>.sdc  timing constraints
#      librelane/config.json  LibreLane base config + per-mode overlays
#      pdk_extension/         hand-designed cells for THIS design (git-ignored)
#
#  Every path in design.toml is relative to the design directory.
# ================================================================

from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .settings import FRAMEWORK_ROOT, read_toml

DESIGNS_DIR = FRAMEWORK_ROOT / "designs"

LANGUAGES = ("vhdl", "verilog", "systemverilog")
RTL_SIMULATORS = {"vhdl": ("ghdl", "nvc"),
                  "verilog": ("icarus", "verilator"),
                  "systemverilog": ("verilator", "icarus")}
GATE_SIMULATORS = ("icarus", "verilator")

#: The suffix new-design and the source glob default to, per language.
SUFFIX = {"vhdl": ".vhd", "verilog": ".v", "systemverilog": ".sv"}


@dataclass
class Design:
    name: str
    dir: Path
    top: str
    language: str
    pdk: str
    rtl: list
    include_dirs: list
    defines: dict
    vhdl_std: str
    tb_dir: Path
    tb_module: Optional[str]
    rtl_sim: str
    gate_sim: str
    librelane_config: Path
    sdc: Path
    pin_order: Optional[Path]
    def_template: Optional[Path]
    agent: Optional[str]
    flow: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def toml(self) -> Path:
        return self.dir / "design.toml"

    @property
    def pdk_extension_dir(self) -> Path:
        """Hand-designed cells for this design only.  Never shared, never tracked."""
        return self.dir / "pdk_extension"

    @property
    def has_testbench(self) -> bool:
        return bool(self.tb_module) and (self.tb_dir / f"{self.tb_module}.py").is_file()

    def problems(self) -> list:
        """What is missing for a flow run, as sentences."""
        out = []
        if self.language not in LANGUAGES:
            out.append(f"language {self.language!r} is not one of {', '.join(LANGUAGES)}")
        if not self.rtl:
            out.append("no RTL sources (design.toml [sources] rtl)")
        for path in self.rtl:
            if not path.is_file():
                out.append(f"RTL source missing: {rel(path, self.dir)}")
        if not self.librelane_config.is_file():
            out.append(f"LibreLane config missing: {rel(self.librelane_config, self.dir)}")
        if not self.sdc.is_file():
            out.append(f"SDC missing: {rel(self.sdc, self.dir)}")
        if self.rtl_sim not in RTL_SIMULATORS.get(self.language, ()):
            out.append(f"rtl_sim {self.rtl_sim!r} cannot simulate {self.language}")
        for path in self.unlisted_packages():
            out.append(f"{rel(path, self.dir)} declares a package but is not in [sources] rtl: "
                       f"include_dirs only serve `include, so synthesis and Icarus never "
                       f"see it (Verilator finds it by file name, which hides the mistake). "
                       f"List it before the files that import it")
        if self.gate_sim not in GATE_SIMULATORS:
            out.append(f"gate_sim {self.gate_sim!r} is not one of {', '.join(GATE_SIMULATORS)}")
        return out


    def unlisted_packages(self) -> list:
        """SV/Verilog files under include_dirs that declare a package, but are not sources."""
        if self.language == "vhdl":
            return []
        listed = {p.resolve() for p in self.rtl}
        found = []
        for directory in self.include_dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if (path.suffix in (".sv", ".svh", ".v", ".vh") and path.resolve() not in listed
                        and _PACKAGE.search(path.read_text(errors="replace"))):
                    found.append(path)
        return found


_PACKAGE = re.compile(r"^\s*package\s+\w+\s*;", re.MULTILINE)


def rel(path: Path, base: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except ValueError:
        return str(path)


def available() -> list:
    if not DESIGNS_DIR.is_dir():
        return []
    return sorted(p.name for p in DESIGNS_DIR.iterdir() if (p / "design.toml").is_file())


def _expand(directory: Path, patterns: list) -> list:
    """Sources in the order given; a glob expands sorted, in place."""
    out: list = []
    for pattern in patterns:
        hits = sorted(glob.glob(str(directory / pattern), recursive=True))
        for hit in hits or [str(directory / pattern)]:
            path = Path(hit)
            if path not in out:
                out.append(path)
    return out


def load(name: str, default_pdk: str = "ihp-sg13g2") -> Design:
    directory = DESIGNS_DIR / name
    path = directory / "design.toml"
    if not path.is_file():
        known = ", ".join(available()) or "none yet -- `make new-design DESIGN=<name>`"
        raise SystemExit(f"error: no design {name!r} ({path} not found). Designs: {known}")
    data = read_toml(path)
    meta = data.get("design", {})
    sources = data.get("sources", {})
    tb = data.get("tb", {})
    ll = data.get("librelane", {})
    layout = data.get("layout", {})

    language = meta.get("language", "verilog").lower()
    top = meta.get("top", name)
    rtl_default = [f"src/rtl/*{SUFFIX.get(language, '.v')}"]

    def opt(key: str) -> Optional[Path]:
        value = ll.get(key)
        return directory / value if value else None

    return Design(
        name=meta.get("name", name),
        dir=directory,
        top=top,
        language=language,
        pdk=meta.get("pdk", default_pdk),
        rtl=_expand(directory, sources.get("rtl", rtl_default)),
        include_dirs=[directory / d for d in sources.get("include_dirs", [])],
        defines=dict(sources.get("defines", {})),
        vhdl_std=str(sources.get("vhdl_std", "08")),
        tb_dir=directory / tb.get("dir", "tb"),
        tb_module=tb.get("module"),
        rtl_sim=tb.get("rtl_sim", "ghdl" if language == "vhdl" else "icarus"),
        gate_sim=tb.get("gate_sim", "icarus"),
        librelane_config=directory / ll.get("config", "librelane/config.json"),
        sdc=directory / ll.get("sdc", f"constraints/{top}.sdc"),
        pin_order=opt("pin_order"),
        def_template=opt("def_template"),
        agent=layout.get("agent"),
        flow=dict(data.get("flow", {})),
        raw=data,
    )
