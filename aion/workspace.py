# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Where everything lives: the framework's fixed
#                             directories, one run's directories, and the
#                             host <-> container path algebra
#
#  Three kinds of file, and each has exactly one home:
#
#    authored    designs/<d>/, agents/<a>/, pdks/<p>/       tracked
#    derived     runs/<d>/<tag>/                            git-ignored
#    published   library/<pdk>/  (mined cells, shared)      git-ignored
#                designs/<d>/pdk_extension/ (per design)    git-ignored
#
#  Two namespaces, and mixing them is the easiest way to get a silently wrong
#  run:
#
#    host       <FRAMEWORK_ROOT>/runs/alu/main/1_synth/nl/alu.nl.v
#    container  <mount>/runs/alu/main/1_synth/nl/alu.nl.v
#
#  `container()` is the only conversion, and it refuses anything outside
#  FRAMEWORK_ROOT, because nothing else is reachable from inside the container
#  under a name this flow can rely on.
# ================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import settings as settings_mod
from .design import Design
from .pdk import Pdk
from .settings import FRAMEWORK_ROOT

AION_FLOW = FRAMEWORK_ROOT / "aion_flow"
LAYOUT_TOOL = AION_FLOW / "tools" / "aion_layout_claude"
AION_OPT_DIR = AION_FLOW / "tools" / "aion_opt"
AION_MIN_DIR = AION_FLOW / "tools" / "aion_minimizer"
RUNS_DIR = FRAMEWORK_ROOT / "runs"
AGENTS_DIR = FRAMEWORK_ROOT / "agents"
LIBRARY_DIR = FRAMEWORK_ROOT / "library"
TEMPLATES_DIR = FRAMEWORK_ROOT / "templates"

#: Step keys, in flow order.  The directory of each under a run is its key.
STEP_KEYS = (
    "1_synth",
    "2_pattern_extraction",
    "3_rewrite",
    "4_characterization",
    "5_gate_minimization",
    "6_layout_drawing",
    "7_pnr",
    "8_render",
    "9_report",
)


class PathError(RuntimeError):
    """A path cannot be expressed in the namespace it is needed in."""


# ---------------------------------------------------------------------
def rel(path) -> str:
    """Framework-relative spelling, for logs and reports."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(FRAMEWORK_ROOT))
    except ValueError:
        return str(resolved)


def container(path) -> str:
    """A host path as the EDA container sees it."""
    mount = settings_mod.get().container_mount
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(FRAMEWORK_ROOT)
    except ValueError as exc:
        raise PathError(
            f"{resolved} lies outside {FRAMEWORK_ROOT}, the only tree the "
            f"container sees (as {mount}). Keep flow inputs and outputs inside "
            f"the framework.") from exc
    return mount if str(relative) == "." else f"{mount}/{relative}"


def host(path) -> Path:
    """Inverse of container()."""
    mount = settings_mod.get().container_mount
    text = str(path)
    if text == mount:
        return FRAMEWORK_ROOT
    if not text.startswith(mount + "/"):
        raise PathError(f"{text} is not under {mount}")
    return FRAMEWORK_ROOT / text[len(mount) + 1:]


# ---------------------------------------------------------------------
@dataclass
class Workspace:
    """Every directory one (design, tag) run reads and writes."""

    design: Design
    pdk: Pdk
    tag: str

    # ---- the run ----------------------------------------------------
    @property
    def run_dir(self) -> Path:
        return RUNS_DIR / self.design.name / self.tag

    def step(self, key: str) -> Path:
        return self.run_dir / key

    @property
    def logs(self) -> Path:
        """Outside every step directory: a LibreLane save wipes its step dir."""
        return self.run_dir / "logs"

    @property
    def coherence_file(self) -> Path:
        return self.run_dir / "coherence.json"

    @property
    def manifest_file(self) -> Path:
        return self.run_dir / "run.json"

    @property
    def baseline_dir(self) -> Path:
        """The PDK-only hardening step 9 measures against.  Not a step."""
        return self.run_dir / "baseline"

    def librelane_run(self, mode: str) -> Path:
        """LibreLane's own working directory for one mode (synth/pnr/baseline)."""
        return self.run_dir / "librelane" / mode

    @property
    def sim_dir(self) -> Path:
        return self.run_dir / "sim"

    @property
    def agent_dir(self) -> Path:
        """The agent profile as it was used by this run: snapshot + workspace."""
        return self.run_dir / "agent"

    @property
    def explore_dir(self) -> Path:
        return self.run_dir / "explore"

    # ---- PDK extension: per design, built per run ---------------------
    @property
    def pdk_ext_dir(self) -> Path:
        return self.design.pdk_extension_dir

    @property
    def pdk_ext_build(self) -> Path:
        return self.run_dir / "pdk_extension"

    def pdk_ext_lib(self, corner: str | None = None) -> Path:
        corner = corner or self.pdk.typ_corner
        return (self.pdk_ext_build / "lib"
                / f"{self.pdk.std_cell_library}_ext_{corner}.lib")

    @property
    def pdk_ext_dict(self) -> Path:
        return (self.pdk_ext_build / "tech_dict"
                / f"{self.pdk.std_cell_library}_ext.json")

    def pdk_ext_cells(self) -> list:
        """Cells under designs/<d>/pdk_extension/, one directory each."""
        if not self.pdk_ext_dir.is_dir():
            return []
        return sorted(child.name for child in self.pdk_ext_dir.iterdir()
                      if child.is_dir() and not child.name.startswith((".", "_")))

    # ---- the shared library of mined cells ----------------------------
    @property
    def library(self) -> Path:
        return LIBRARY_DIR / self.pdk.name

    @property
    def cells_dir(self) -> Path:
        """Published views of the mined cells, one directory per cell."""
        return self.library / "cells"

    @property
    def generators_dir(self) -> Path:
        """The one authored artifact per mined cell: its layout generator."""
        return self.library / "generators"

    @property
    def registry(self) -> Path:
        return self.library / "registry.json"

    # ---- convenience ----------------------------------------------
    def netlist(self, key: str) -> Path:
        return self.step(key) / "nl" / f"{self.design.top}.nl.v"


def make(design: Design, pdk: Pdk, tag: str) -> Workspace:
    if not tag or "/" in tag or tag.startswith("."):
        raise SystemExit(f"error: {tag!r} is not a usable run tag")
    return Workspace(design=design, pdk=pdk, tag=tag)
