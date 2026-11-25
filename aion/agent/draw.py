# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The drawing loop: verify -> evidence -> agent
#                             turn, until the layout verifies
#
#  One loop for every cell that needs drawing -- a mined cell in step 6, a
#  hand-designed PDK-extension cell in `aion ext build` -- so the two cannot
#  drift into grading or briefing the agent differently.
#
#  The agent only ever writes the generator.  Every graded step runs here, on
#  the host, and what the agent says it did is never the verdict: the verdict
#  is the RESULT: line of `make verify`.  LVS grades the layout against the
#  target netlist, so a turn that edits that netlist is caught here: the
#  netlist is hashed before the first turn and after every turn, and a turn
#  that changed it fails the cell, with the original restored.  The file on disk is the only thing
#  that counts -- a turn that timed out after writing a complete generator has
#  done the work, one that exited cleanly without touching it has not.
#
#  Every turn is recorded: <build>/agent.json for the cell, and one line per
#  turn in <run>/agent/usage.jsonl for the run -- profile, digest, model,
#  session, cost, duration -- so a published cell can always say which agent
#  drew it and what that cost.
# ================================================================

from __future__ import annotations

import hashlib
import json
import os
import shutil
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import runner as runner_mod
from .. import workspace
from ..coherence import now
from ..runner import Result, Runner, StepFailed
from ..style import Style, color, emit, fail, info, note, ok, warn
from . import claude
from .profile import Profile


@dataclass
class DrawTask:
    kind: str                 # "layout" (mined) | "ext_layout" (PDK extension)
    cell: str
    generator: Path
    netlist: Path
    build: Path
    jobs: int = 8
    #: Extra prompt text, e.g. the hand-authored floorplan of an extension cell.
    sections: str = ""
    turns: list = field(default_factory=list)
    #: The netlist as it was before anything was drawn against it.
    guard: Optional["NetlistGuard"] = None

    @property
    def final(self) -> Path:
        return self.build / "final"

    @property
    def report(self) -> Path:
        return self.build / f"{self.cell}.report.md"

    @property
    def evidence_file(self) -> Path:
        return self.build / f"{self.cell}.evidence.md"

    @property
    def record_file(self) -> Path:
        return self.build / "agent.json"

    def make_vars(self) -> list:
        """What the aion_flow `aion-layout-*` wrapper targets take."""
        return [f"CELL={self.cell}", f"CELL_MODULE={self.generator}",
                f"LAYOUT_NETLIST={self.netlist}", f"BUILD_DIR_LAYOUT={self.build}",
                f"FINAL_DIR={self.final}", f"JOBS={self.jobs}"]

    def tool_command(self, target: str) -> str:
        """The same call, spelled against the tool's own Makefile, for the agent."""
        return (f"make -C {workspace.LAYOUT_TOOL} {target} CELL={self.cell} "
                f"CELL_MODULE={self.generator} NETLIST={self.netlist} "
                f"BUILD_DIR={self.build}")


def fingerprint(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class NetlistModified(StepFailed):
    """A drawing turn changed the netlist LVS grades the layout against."""


@dataclass
class NetlistGuard:
    """The LVS reference netlist, frozen before a cell is drawn against it.

    LVS passes whenever the layout matches the netlist file, so an agent that
    edits the netlist can make any layout "verify".  The guard keeps the
    original bytes, so a change can be undone as well as detected.
    """

    path: Path
    original: bytes
    sha256: str

    @classmethod
    def take(cls, path: Path) -> "NetlistGuard":
        data = Path(path).read_bytes()
        return cls(Path(path), data, hashlib.sha256(data).hexdigest())

    def changed(self) -> bool:
        return fingerprint(self.path) != self.sha256

    def restore(self, keep_dir: Path) -> Optional[Path]:
        """Put the original back; keep the edited file, if any, for inspection."""
        kept = None
        if self.path.is_file():
            keep_dir.mkdir(parents=True, exist_ok=True)
            kept = keep_dir / f"{self.path.name}.tampered"
            shutil.copy2(self.path, kept)
        self.path.write_bytes(self.original)
        return kept

    def enforce(self, cell: str, keep_dir: Path, by: str) -> None:
        """Raise NetlistModified, original restored, if the netlist changed."""
        if not self.changed():
            return
        kept = self.restore(keep_dir)
        fail(f"{cell}: {by} modified the LVS reference netlist "
             f"{workspace.rel(self.path)} — original restored")
        if kept is not None:
            note(f"  the edited copy is kept as {workspace.rel(kept)}")
        raise NetlistModified(
            f"{by} modified the LVS reference netlist {workspace.rel(self.path)}; "
            f"the original is restored"
            + (f" and the edited copy kept as {workspace.rel(kept)}" if kept else ""), 1)


def turn_log(cell: str, target: str, turn: Optional[int]) -> str:
    return f"{cell}.{target}" if turn is None else f"{cell}.{turn:02d}.{target}"


# ---------------------------------------------------------------------
class Drawer:
    """Drives one agent profile over any number of cells in one run."""

    def __init__(self, ws, runner: Runner, profile: Profile):
        self.ws = ws
        self.runner = runner
        self.profile = profile
        self.home = ws.agent_dir / profile.name
        self._claude: Optional[str] = None

    # -----------------------------------------------------------------
    def prepare(self) -> None:
        """Materialize the profile into the run and find the CLI, once."""
        if self.profile.backend == "claude-code":
            self._claude = shutil.which("claude")
            if self._claude is None and not self.runner.dry_run:
                raise StepFailed("DRAW_MODE=auto needs the `claude` CLI on PATH. "
                                 "Use DRAW_MODE=manual and draw the generator yourself.", 2)
        self.profile.materialize(self.home, {
            "AION_FRAMEWORK": str(workspace.FRAMEWORK_ROOT),
            "AION_LAYOUT_TOOL": str(workspace.LAYOUT_TOOL),
            "AION_RUN_DIR": str(self.ws.run_dir),
            "PYTHON": "python3",
        })
        info(f"agent {self.profile.name}  ({self.profile.summary()})")
        note(f"profile {self.profile.digest()}  workspace {workspace.rel(self.home)}")
        if self.profile.overrides:
            note("overrides: " + "  ".join(f"{k}={v}" for k, v in self.profile.overrides.items()))

    # -----------------------------------------------------------------
    def verify(self, task: DrawTask, turn: Optional[int] = None) -> Result:
        return self.runner.layout("verify", task.make_vars(),
                                  name=turn_log(task.cell, "verify", turn))

    def evidence(self, task: DrawTask, turn: Optional[int] = None) -> str:
        self.runner.layout("evidence", task.make_vars(),
                           name=turn_log(task.cell, "evidence", turn))
        try:
            return task.evidence_file.read_text()
        except OSError:
            return ""

    # -----------------------------------------------------------------
    def draw(self, task: DrawTask) -> Optional[str]:
        """Loop until RESULT: PASS, the turn budget, the cost cap or two idle turns."""
        profile = self.profile
        if self.runner.dry_run:
            info(f"{task.cell}: would run up to {profile.max_turns} agent turn(s) "
                 f"with {profile.name}")
            return None
        if task.guard is None:
            task.guard = NetlistGuard.take(task.netlist)
        verdict = None
        idle = 0
        spent = 0.0
        for iteration in range(1, profile.max_turns + 1):
            # One left by an earlier turn must never be passed off as this one's.
            task.report.unlink(missing_ok=True)
            result = self.verify(task, turn=iteration)
            verdict = result.verdict("RESULT:")
            if verdict == "RESULT: PASS":
                ok(f"{task.cell}: verified after {iteration - 1} agent turn(s)")
                self._save(task, verdict)
                return verdict

            info(f"{task.cell}: turn {iteration}/{profile.max_turns} "
                 f"({verdict or 'no verdict'})")
            evidence = self.evidence(task, turn=iteration)
            before = fingerprint(task.generator)
            turn = self._turn(task, claude.verdict_text(task.report, result.output),
                              evidence, iteration)
            spent += turn.cost_usd or 0.0
            changed = fingerprint(task.generator) != before
            netlist_changed = task.guard.changed()
            self._log_turn(task, iteration, turn, verdict, changed, netlist_changed)
            if netlist_changed:
                self._save(task, "RESULT: FAIL (netlist modified)", netlist_modified=True)
                task.guard.enforce(task.cell, task.build, f"agent turn {iteration}")

            if not changed:
                idle += 1
                warn(f"{task.cell}: turn {iteration} left the generator unchanged")
                if turn.report:
                    note(f"  agent said: {turn.report}")
                if idle >= profile.idle_turns_abort:
                    fail(f"{task.cell}: the agent made no change {idle} time(s) in a "
                         f"row — it is not running, not that the layout is hard")
                    note(f"  check {workspace.rel(self.runner.log_dir)}/{task.cell}.agent.*.log")
                    break
            else:
                idle = 0
            if profile.max_usd_per_cell is not None and spent >= profile.max_usd_per_cell:
                warn(f"{task.cell}: ${spent:.2f} spent, the profile caps a cell at "
                     f"${profile.max_usd_per_cell:g} — stopping")
                break

        verdict = self.verify(task, turn=len(task.turns) + 1).verdict("RESULT:")
        if verdict != "RESULT: PASS":
            warn(f"{task.cell}: still {verdict or 'unverified'} after "
                 f"{len(task.turns)} turn(s)")
        self._save(task, verdict)
        return verdict

    # -----------------------------------------------------------------
    def _turn(self, task: DrawTask, verdict: str, evidence: str,
              iteration: int) -> claude.AgentTurn:
        profile = self.profile
        prompt = string.Template(profile.prompt(task.kind)).safe_substitute({
            "cell": task.cell,
            "generator": str(task.generator),
            "netlist": str(task.netlist),
            "build_dir": str(task.build),
            "tool": str(workspace.LAYOUT_TOOL),
            "verify": task.tool_command("verify"),
            "evidence": task.tool_command("evidence"),
            "turn": str(iteration),
            "max_turns": str(profile.max_turns),
            "sections": task.sections,
            "briefing": "\n".join(claude.briefing(verdict, evidence)),
        })

        argv = [self._claude or "claude", "-p", prompt,
                "--permission-mode", profile.permission_mode,
                "--setting-sources", profile.setting_sources]
        if profile.allowed_tools:
            argv += ["--allowedTools", ",".join(profile.allowed_tools)]
        if profile.disallowed_tools:
            argv += ["--disallowedTools", ",".join(profile.disallowed_tools)]
        for directory in sorted({str(workspace.LAYOUT_TOOL), str(task.build),
                                 str(task.generator.parent), str(task.netlist.parent)}):
            argv += ["--add-dir", directory]
        if profile.model:
            argv += ["--model", profile.model]
        if profile.effort:
            argv += ["--effort", profile.effort]
        if profile.max_usd_per_turn is not None:
            argv += ["--max-budget-usd", f"{profile.max_usd_per_turn:g}"]
        argv += list(profile.extra_args)

        env = {**os.environ, **runner_mod.layout_env(),
               "AION_LAYOUT_CELL": task.cell,
               "AION_LAYOUT_GENERATOR": str(task.generator),
               "AION_LAYOUT_NETLIST": str(task.netlist),
               **{k: str(v) for k, v in profile.env.items()}}

        log = self.runner.log_dir / f"{task.cell}.agent.{iteration}.log"
        emit(color(f"  ▶ {profile.name}  (turn {iteration}/{profile.max_turns}, "
                   f"{task.cell})", Style.WHITE))
        note(f"live log {workspace.rel(log)}")
        turn = claude.run(argv, cwd=self.home, env=env, log=log,
                          timeout=profile.turn_timeout, stream=profile.stream,
                          echo=not self.runner.quiet)
        note(f"agent exit {turn.returncode} in {turn.duration_s:.0f}s   "
             f"log {workspace.rel(log)}")
        if not turn.ok and not turn.report:
            turn.report = f"exit {turn.returncode}, no output"
        return turn

    def _log_turn(self, task: DrawTask, iteration: int, turn: claude.AgentTurn,
                  verdict_before: Optional[str], changed: bool,
                  netlist_changed: bool = False) -> None:
        entry = {"cell": task.cell, "kind": task.kind, "turn": iteration,
                 "at": now(), "profile": self.profile.name,
                 "digest": self.profile.digest(), "verdict_before": verdict_before,
                 "generator_changed": changed, "netlist_changed": netlist_changed,
                 **turn.record()}
        task.turns.append(entry)
        usage = self.ws.agent_dir / "usage.jsonl"
        usage.parent.mkdir(parents=True, exist_ok=True)
        with open(usage, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _save(self, task: DrawTask, verdict: Optional[str],
              netlist_modified: bool = False) -> None:
        previous = []
        try:
            previous = json.loads(task.record_file.read_text()).get("history", [])
        except (OSError, ValueError):
            pass
        cost = sum(t.get("cost_usd") or 0.0 for t in task.turns)
        record = {
            "cell": task.cell, "kind": task.kind, "verdict": verdict,
            "profile": self.profile.name, "digest": self.profile.digest(),
            "model": self.profile.model, "effort": self.profile.effort,
            "generator": workspace.rel(task.generator),
            "generator_sha256": fingerprint(task.generator),
            "netlist": workspace.rel(task.netlist),
            "netlist_sha256": task.guard.sha256 if task.guard else fingerprint(task.netlist),
            "netlist_modified": netlist_modified,
            "turns": len(task.turns), "cost_usd": round(cost, 4),
            "finished": now(), "sessions": task.turns,
            "history": previous[-20:] + ([{"finished": now(), "verdict": verdict,
                                           "profile": self.profile.name,
                                           "turns": len(task.turns),
                                           "cost_usd": round(cost, 4)}]
                                         if task.turns else []),
        }
        task.build.mkdir(parents=True, exist_ok=True)
        task.record_file.write_text(json.dumps(record, indent=2) + "\n")


def provenance(task_build: Path) -> dict:
    """The agent record of a drawn cell, for publishing beside its views."""
    try:
        record = json.loads((task_build / "agent.json").read_text())
    except (OSError, ValueError):
        return {"drawn_by": "unknown (no agent record — drawn by hand?)"}
    return {key: record.get(key) for key in
            ("profile", "digest", "model", "effort", "turns", "cost_usd",
             "generator_sha256", "netlist_sha256", "finished", "history")}


__all__ = ["DrawTask", "Drawer", "NetlistGuard", "NetlistModified", "fingerprint",
           "provenance", "turn_log"]
