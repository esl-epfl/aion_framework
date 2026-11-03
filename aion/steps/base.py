# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The shape of one flow step
# ================================================================

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import workspace
from ..coherence import BLOCKED, OK, Coherence
from ..config import Config
from ..runner import Runner, StepFailed


@dataclass
class Context:
    cfg: Config
    ws: workspace.Workspace
    runner: Runner
    coherence: Coherence
    force: bool = False
    notes: list = field(default_factory=list)
    #: The extension views for this run, resolved on first use (pdk_ext.py).
    pdk_ext: Optional[object] = None
    ext_provisional: bool = False
    #: Agent overrides from the command line (AGENT_SET), applied on load.
    agent_overrides: list = field(default_factory=list)
    #: Extra fields the running step wants in its coherence record.
    record: dict = field(default_factory=dict)

    def note(self, message: str) -> None:
        self.notes.append(message)


class Step:
    """One numbered stage of the flow.

    `run()` reads only what `inputs()` names, writes only under its own step
    directory (step 6 also publishes into library/<pdk>/), and never assumes
    an earlier step ran in the same process.
    """

    key: str = ""
    title: str = ""
    summary: str = ""
    upstream: tuple = ()
    needs_container: bool = False
    dry_run: bool = False

    def outdir(self, ctx: Context) -> Path:
        return ctx.ws.step(self.key)

    def log_dir(self, ctx: Context) -> Path:
        return ctx.ws.logs / self.key

    def inputs(self, ctx: Context) -> list:
        return []

    def outputs(self, ctx: Context) -> list:
        return []

    def run(self, ctx: Context) -> str:
        raise NotImplementedError

    # -----------------------------------------------------------------
    def require(self, *candidates: Path) -> None:
        if self.dry_run:
            return
        for path in candidates:
            if not Path(path).exists():
                owner = _owner_of(path)
                hint = f"\n  run step {owner} first" if owner else ""
                raise StepFailed(f"missing input: {workspace.rel(path)}{hint}", 2)

    def fresh(self, ctx: Context, *subdirs: str) -> Path:
        target = self.outdir(ctx).joinpath(*subdirs)
        if self.dry_run:
            return target
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def ensure(self, ctx: Context, *subdirs: str) -> Path:
        target = self.outdir(ctx).joinpath(*subdirs)
        if not self.dry_run:
            target.mkdir(parents=True, exist_ok=True)
        return target


def _owner_of(path) -> Optional[str]:
    parts = Path(path).resolve().parts
    for key in workspace.STEP_KEYS:
        if key in parts:
            return key.split("_", 1)[0]
    return None


__all__ = ["Step", "Context", "StepFailed", "BLOCKED", "OK"]
