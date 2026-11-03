# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               pdks/<name>/pdk.toml -- what the flow needs to
#                             know about a process, in one place
#
#  The shared standard-cell views (Liberty per corner, the SPICE models, the
#  gate-level Verilog, aion_opt's technology dictionary) are listed here once,
#  and every design on that PDK reads them from here.  The cells the flow
#  itself mines and draws are shared the same way, under library/<pdk>/.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .settings import FRAMEWORK_ROOT, read_toml

PDKS_DIR = FRAMEWORK_ROOT / "pdks"


@dataclass
class Pdk:
    name: str
    dir: Path
    std_cell_library: str
    corners: tuple
    typ_corner: str
    site_width_um: float
    row_height_um: float
    tech_dict: Path
    lib_dir: Path
    spice: Path
    verilog_models: tuple
    verilog_models_nodelay: tuple
    #: CELL_LIBS as the PDK declares it for LibreLane, per corner.
    librelane_cell_libs: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def liberty(self, corner: str) -> Path:
        return self.lib_dir / f"{self.std_cell_library}_{corner}.lib"

    @property
    def typ_liberty(self) -> Path:
        return self.liberty(self.typ_corner)


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else FRAMEWORK_ROOT / p


def available() -> list:
    if not PDKS_DIR.is_dir():
        return []
    return sorted(p.name for p in PDKS_DIR.iterdir() if (p / "pdk.toml").is_file())


def load(name: str) -> Pdk:
    path = PDKS_DIR / name / "pdk.toml"
    if not path.is_file():
        known = ", ".join(available()) or "none"
        raise SystemExit(f"error: unknown PDK {name!r} (no {path}). Known: {known}")
    data = read_toml(path)
    paths = data.get("paths", {})
    return Pdk(
        name=data.get("name", name),
        dir=path.parent,
        std_cell_library=data["std_cell_library"],
        corners=tuple(data["corners"]),
        typ_corner=data["typ_corner"],
        site_width_um=float(data["site_width_um"]),
        row_height_um=float(data["row_height_um"]),
        tech_dict=_path(paths["tech_dict"]),
        lib_dir=_path(paths["lib_dir"]),
        spice=_path(paths["spice"]),
        verilog_models=tuple(_path(p) for p in paths["verilog_models"]),
        verilog_models_nodelay=tuple(_path(p) for p in
                                     paths.get("verilog_models_nodelay",
                                               paths["verilog_models"])),
        librelane_cell_libs={k: list(v) for k, v in
                             data.get("librelane", {}).get("cell_libs", {}).items()},
        raw=data,
    )
