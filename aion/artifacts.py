# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               What a run produced, and keeping it under control
#
#  Every run is one directory, runs/<design>/<tag>/, and run.json at its top
#  says what it is: the design, the framework and aion_flow revisions it ran
#  with, the resolved flow settings, the agent profile (name, digest, model)
#  and every invocation that touched it.  coherence.json beside it has the
#  per-step record (inputs, outputs, digests, commands).
#
#    aion artifacts DESIGN              every run of the design, one line each
#    aion artifacts DESIGN --tag T      one run: steps, sizes, agent usage
#    aion clean DESIGN --tag T [--step N]   delete a run, or one step of it
#    aion gc DESIGN --keep N            delete all but the N newest runs
#
#  Nothing here touches library/ (the shared drawn cells) or a design's
#  pdk_extension/: those are published results, not run output.
# ================================================================

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import workspace
from .coherence import Coherence, now
from .style import Style, color, emit, note, ok, title, warn


def _git(path: Path, *args) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def revision(path: Path) -> dict:
    rev = _git(path, "rev-parse", "--short=12", "HEAD")
    dirty = bool(_git(path, "status", "--porcelain", "--untracked-files=no"))
    return {"rev": rev or None, "dirty": dirty}


def du(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def human(size: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


# ---------------------------------------------------------------------
def record_invocation(ws, cfg, steps: list, profile=None, argv=None) -> dict:
    """Create or update run.json for this run."""
    path = ws.manifest_file
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {"design": ws.design.name, "tag": ws.tag, "created": now(),
                "invocations": []}
    data.update({
        "design": ws.design.name,
        "top": ws.design.top,
        "language": ws.design.language,
        "pdk": ws.pdk.name,
        "updated": now(),
        "framework": revision(workspace.FRAMEWORK_ROOT),
        "aion_flow": revision(workspace.AION_FLOW),
        "config": cfg.digest(),
        "config_origin": {k: cfg.origin(k) for k in cfg.known() if cfg.origin(k) != "default"},
    })
    if profile is not None:
        data["agent"] = {"profile": profile.name, "chain": profile.chain,
                         "digest": profile.digest(), "model": profile.model,
                         "effort": profile.effort, "overrides": profile.overrides}
    data.setdefault("invocations", []).append({
        "at": now(), "steps": [s.key for s in steps], "argv": argv or [],
        "agent": profile.name if profile is not None else cfg.AGENT,
    })
    data["invocations"] = data["invocations"][-50:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


def finish_invocation(ws, status: str) -> None:
    path = ws.manifest_file
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if data.get("invocations"):
        data["invocations"][-1]["finished"] = now()
        data["invocations"][-1]["status"] = status
    data["updated"] = now()
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------
def runs_of(design: str) -> list:
    root = workspace.RUNS_DIR / design
    if not root.is_dir():
        return []
    out = []
    for run in root.iterdir():
        if not run.is_dir():
            continue
        try:
            meta = json.loads((run / "run.json").read_text())
        except (OSError, ValueError):
            meta = {}
        stamp = meta.get("updated") or datetime.fromtimestamp(run.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        out.append((stamp, run, meta))
    return sorted(out, key=lambda item: item[0], reverse=True)


def usage(run: Path) -> dict:
    """Agent turns and cost recorded under a run."""
    total = {"turns": 0, "cost_usd": 0.0, "by_profile": {}}
    path = run / "agent" / "usage.jsonl"
    if not path.is_file():
        return total
    for line in path.read_text().splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        total["turns"] += 1
        total["cost_usd"] += entry.get("cost_usd") or 0.0
        prof = total["by_profile"].setdefault(entry.get("profile", "?"),
                                              {"turns": 0, "cost_usd": 0.0, "models": set()})
        prof["turns"] += 1
        prof["cost_usd"] += entry.get("cost_usd") or 0.0
        if entry.get("model"):
            prof["models"].add(entry["model"])
    return total


def list_runs(design: str) -> int:
    title(f" runs of {design} ")
    runs = runs_of(design)
    if not runs:
        note(f"no runs under {workspace.rel(workspace.RUNS_DIR / design)}")
        return 0
    emit(color(f"  {'tag':<16}{'updated':<27}{'size':>8}  {'steps ok':<10}{'agent':<22}{'turns':>6}{'cost':>9}", Style.BOLD))
    for stamp, run, meta in runs:
        coh = Coherence(run / "coherence.json")
        done = sum(1 for r in coh.data["steps"].values() if r.get("status") == "ok")
        u = usage(run)
        agent = (meta.get("agent") or {}).get("profile", "—")
        emit(f"  {run.name:<16}{stamp:<27}{human(du(run)):>8}  {done:<10}{agent:<22}"
             f"{u['turns']:>6}{'$%.2f' % u['cost_usd']:>9}")
    return 0


def show_run(ws) -> int:
    run = ws.run_dir
    title(f" {ws.design.name} / {ws.tag} ")
    if not run.is_dir():
        note(f"{workspace.rel(run)} does not exist yet")
        return 0
    try:
        meta = json.loads(ws.manifest_file.read_text())
    except (OSError, ValueError):
        meta = {}
    fw, fl = meta.get("framework", {}), meta.get("aion_flow", {})
    note(f"run       {workspace.rel(run)}   {human(du(run))}")
    note(f"created   {meta.get('created', '?')}   updated {meta.get('updated', '?')}")
    note(f"framework {fw.get('rev')}{' (dirty)' if fw.get('dirty') else ''}   "
         f"aion_flow {fl.get('rev')}{' (dirty)' if fl.get('dirty') else ''}")
    agent = meta.get("agent")
    if agent:
        note(f"agent     {agent['profile']}  {agent.get('model') or 'cli-default'}  "
             f"effort {agent.get('effort') or 'cli-default'}  {agent['digest']}"
             + (f"  overrides {agent['overrides']}" if agent.get("overrides") else ""))
    emit()
    coh = Coherence(ws.coherence_file)
    emit(color(f"  {'step':<24}{'status':<10}{'finished':<28}{'size':>8}", Style.BOLD))
    for key in workspace.STEP_KEYS:
        record = coh.get(key) or {}
        path = ws.step(key)
        size = human(du(path)) if path.is_dir() else "—"
        emit(f"  {key:<24}{record.get('status', '—'):<10}{record.get('finished', '—'):<28}{size:>8}")
    for extra in ("baseline", "librelane", "sim", "pdk_extension", "agent", "logs"):
        path = run / extra
        if path.is_dir():
            emit(color(f"  {extra + '/':<62}{human(du(path)):>8}", Style.DIM))
    u = usage(run)
    if u["turns"]:
        emit()
        note(f"agent usage: {u['turns']} turn(s), ${u['cost_usd']:.2f}")
        for name, prof in u["by_profile"].items():
            note(f"  {name:<20}{prof['turns']:>4} turn(s)  ${prof['cost_usd']:.2f}  "
                 f"{', '.join(sorted(prof['models'])) or ''}")
    return 0


def clean(ws, step: Optional[str] = None, yes: bool = False) -> int:
    target = ws.step(step) if step else ws.run_dir
    if not target.exists():
        note(f"nothing at {workspace.rel(target)}")
        return 0
    if workspace.RUNS_DIR.resolve() not in target.resolve().parents:
        warn(f"refusing to delete {target}: not under {workspace.rel(workspace.RUNS_DIR)}")
        return 1
    size = human(du(target))
    if not yes:
        warn(f"would delete {workspace.rel(target)} ({size}); pass --yes (make ... YES=1)")
        return 1
    shutil.rmtree(target)
    if step:
        coh = Coherence(ws.coherence_file)
        coh.forget(step)
    ok(f"deleted {workspace.rel(target)} ({size})")
    return 0


def gc(design: str, keep: int, yes: bool = False) -> int:
    runs = runs_of(design)
    doomed = runs[keep:]
    if not doomed:
        note(f"{len(runs)} run(s), keeping {keep}: nothing to delete")
        return 0
    for _, run, _ in doomed:
        size = human(du(run))
        if yes:
            shutil.rmtree(run)
            ok(f"deleted {workspace.rel(run)} ({size})")
        else:
            warn(f"would delete {workspace.rel(run)} ({size})")
    if not yes:
        note("pass --yes (make gc ... YES=1) to delete them")
        return 1
    return 0


# ---------------------------------------------------------------------
def list_cells(pdk_name: str) -> int:
    root = workspace.LIBRARY_DIR / pdk_name
    title(f" shared cell library: {pdk_name} ")
    cells = sorted((root / "cells").glob("*/provenance.json")) if (root / "cells").is_dir() else []
    generators = sorted((root / "generators").glob("*.py")) if (root / "generators").is_dir() else []
    if not cells and not generators:
        note(f"empty: {workspace.rel(root)}")
        return 0
    emit(color(f"  {'cell':<30}{'design':<16}{'agent':<18}{'model':<22}{'turns':>6}{'cost':>9}  compare", Style.BOLD))
    published = set()
    for prov in cells:
        data = json.loads(prov.read_text())
        agent = data.get("agent") or {}
        published.add(data["cell"])
        compare = (data.get("compare") or "").replace("COMPARE: ", "")[:40]
        cost = agent.get("cost_usd")
        emit(f"  {data['cell']:<30}{data.get('design', '?'):<16}"
             f"{str(agent.get('profile') or '—'):<18}{str(agent.get('model') or '—'):<22}"
             f"{str(agent.get('turns') if agent.get('turns') is not None else '—'):>6}"
             f"{('$%.2f' % cost) if cost is not None else '—':>9}  {compare}")
    pending = [g.stem for g in generators if g.stem not in published]
    if pending:
        emit()
        note(f"generators not (yet) published: {', '.join(pending)}")
    return 0
