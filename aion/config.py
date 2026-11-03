# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Every flow knob, and the layers that set them
#
#  One value per knob, resolved in this order -- later wins:
#
#    1. the defaults below
#    2. aion.toml / aion.local.toml   [flow]
#    3. designs/<d>/design.toml       [flow]
#    4. the command line              --set NAME=VALUE   (make ... SET="...")
#
#  `aion config DESIGN` prints the resolved table and which layer set each
#  value.  Names are case-insensitive in the TOML files; the canonical
#  spelling is the upper-case one below.
#
#  The drawing agent is NOT configured here.  Its model, effort, turn budget
#  and tools live in agents/<name>/agent.toml; this file only says WHICH
#  agent (AGENT) and how the step uses it (DRAW_MODE, DRAW_JOBS).
# ================================================================

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional


def optional(name: str, value) -> list:
    """Forward a Make variable only when it is set."""
    return [] if value is None else [f"{name}={value}"]


def flag(name: str, value: bool) -> list:
    """Forward a boolean Make variable only when it is true."""
    return [f"{name}=1"] if value else []


@dataclass
class Config:
    # ---- general ------------------------------------------------------
    # Downgrade LibreLane's hard checkers to warnings. Never for signoff.
    LENIENT: bool = False
    # Per-command wall clock, seconds. Characterization is the long pole.
    TIMEOUT: int = 4 * 60 * 60
    # Run the design's cocotb testbench after synthesis, rewrite and PnR.
    # Off, or with no testbench in the design, the netlists are only proved
    # by the LEC.
    SIM: bool = True

    # ---- 1_synth --------------------------------------------------------
    # Map against the PDK library PLUS the hand-designed cells under
    # designs/<d>/pdk_extension/.  The baseline never sees them.
    PDK_EXT: bool = True

    # ---- 2_pattern_extraction / 3_rewrite  (aion_opt) -----------------
    MAX_SIZE: int = 2
    MIN_OCCURRENCES: int = 2
    MIN_SELECTED: Optional[int] = 1
    AREA_FACTOR: float = 0.9
    MAX_OUTPUTS: Optional[int] = 1
    MAX_INPUTS: Optional[int] = 6
    JOBS: Optional[int] = None
    CELL_PREFIX: str = "AION_"
    # Every elite cell is a cell step 6 has to draw.
    ELITE_COUNT: Optional[int] = 10
    ELITE_METRIC: str = "occurrences"
    # Comma-separated globs the miner keeps as leaves.  XOR/XNOR/MUX: a drawn
    # pair of them is no narrower than the PDK cells it replaces.
    MINE_EXCLUDE: str = "*xor*,*xnor*,*mux*"

    # ---- 3_rewrite ------------------------------------------------------
    # elite | all | <path to a hand-curated .v>
    REWRITE_CELLS: str = "elite"
    REWRITE_FLAT: bool = False
    # Liberty the LEC reads (None = the extended one when PDK_EXT is on).
    LEC_LIB: Optional[str] = None

    # ---- 5_gate_minimization  (aion_minimizer) ------------------------
    MINIMIZER_MODE: str = "transistor"
    MINIMIZER_WN: str = "0.74u"
    MINIMIZER_WP: str = "1.48u"
    MINIMIZER_L: str = "0.13u"
    MINIMIZER_MAX_INPUTS: int = 6
    MINIMIZER_VERIFY: bool = True
    MINIMIZER_VERIFY_SPICE: bool = True

    # ---- 6_layout_drawing  (aion_layout_claude + the agent) -----------
    # The agent profile under agents/.  None = design.toml [layout] agent,
    # then aion.toml [defaults] agent.
    AGENT: Optional[str] = None
    # manual: scaffold and wait for a human.  auto: drive the agent.
    DRAW_MODE: str = "auto"
    # Cells drawn at once; in auto mode, that many agent sessions.
    DRAW_JOBS: int = 1
    DRAW_PUBLISH_ON_LOSS: bool = True
    # typ | all: one Liberty per cell, or one per timing corner.
    LAYOUT_CORNERS: str = "all"
    LAYOUT_JOBS: int = 8
    LAYOUT_VERIFY_PEX: bool = True
    # Keep the published characterization of a cell whose geometry did not move.
    LAYOUT_REUSE: bool = True

    # ---- 7_pnr ----------------------------------------------------------
    SDF_CORNER: str = "nom_slow_1p08V_125C"
    # Back-annotate the post-PnR simulation with the SDF (Icarus).  Delays
    # only: Icarus implements no timing checks, setup/hold are STA's.
    SIM_SDF: bool = True

    # ---- 8_render -------------------------------------------------------
    RENDER_WIDTH: int = 2400
    RENDER_HEIGHT: Optional[int] = None
    RENDER_ZOOM_SITES: int = 32

    # ---- 9_report -------------------------------------------------------
    REPORT_RUN_BASELINE: bool = True
    REPORT_REBUILD_BASELINE: bool = False

    #: name -> which layer set it ("default", "aion.toml", "design.toml", "--set")
    _origin: dict = field(default_factory=dict, repr=False)

    CHOICES = {
        "DRAW_MODE": ("manual", "auto"),
        "LAYOUT_CORNERS": ("typ", "all"),
        "ELITE_METRIC": ("saved-area", "occurrences", "saved-area-per-cell"),
        "MINIMIZER_MODE": ("transistor", "area", "balance"),
        "SDF_CORNER": ("nom_typ_1p20V_25C", "nom_fast_1p32V_m40C",
                       "nom_slow_1p08V_125C"),
    }

    # -----------------------------------------------------------------
    @classmethod
    def known(cls) -> dict:
        return {f.name: f for f in fields(cls) if not f.name.startswith("_")}

    def set(self, name: str, value, origin: str) -> None:
        known = self.known()
        key = name.strip().upper()
        if key not in known:
            raise ValueError(f"unknown setting {name!r} ({origin}). "
                             f"Known: {', '.join(sorted(known))}")
        coerced = _coerce(known[key].type, value)
        allowed = self.CHOICES.get(key)
        if allowed is not None and coerced not in allowed:
            raise ValueError(f"{key}={coerced!r} ({origin}) is not one of: "
                             f"{', '.join(allowed)}")
        setattr(self, key, coerced)
        self._origin[key] = origin

    def apply_table(self, table: dict, origin: str) -> None:
        for name, value in table.items():
            self.set(name, value, origin)

    def apply_assignments(self, assignments, origin: str = "--set") -> None:
        for raw in assignments:
            if "=" not in raw:
                raise ValueError(f"--set expects NAME=VALUE, got {raw!r}")
            name, _, value = raw.partition("=")
            self.set(name, value.strip(), origin)

    def origin(self, name: str) -> str:
        return self._origin.get(name, "default")

    def digest(self) -> dict:
        return {name: getattr(self, name) for name in self.known()}

    @property
    def overrides(self) -> dict:
        return {k: getattr(self, k) for k, o in self._origin.items() if o == "--set"}


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(annotation, value):
    text = str(annotation)
    is_optional = "Optional" in text or "None" in text
    if isinstance(value, str) and value.strip().lower() in {"none", "null", ""} and is_optional:
        return None
    if "bool" in text:
        if isinstance(value, bool):
            return value
        low = str(value).strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError(f"expected a boolean, got {value!r}")
    if "int" in text:
        if isinstance(value, bool):
            raise ValueError(f"expected an integer, got {value!r}")
        return int(value)
    if "float" in text:
        return float(value)
    return str(value)


def build(settings, design, assignments=()) -> Config:
    cfg = Config()
    cfg.apply_table(settings.flow, "aion.toml")
    cfg.apply_table(design.flow, "design.toml")
    if design.agent and cfg.origin("AGENT") == "default":
        cfg.set("AGENT", design.agent, "design.toml")
    cfg.apply_assignments(assignments)
    if cfg.AGENT is None:
        cfg.AGENT = settings.default_agent
        cfg._origin["AGENT"] = "aion.toml"
    return cfg
