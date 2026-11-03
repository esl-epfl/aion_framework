# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The AION framework command line
#
#  `make <target> DESIGN=<d>` is the front door (see the Makefile); every
#  target is one `python -m aion <command>` call:
#
#    aion run DESIGN [STEP ...]     the flow, or some steps of it
#    aion status DESIGN             is this run coherent?
#    aion config DESIGN             every setting, resolved, and who set it
#    aion sim DESIGN --stage S      the testbench on one view
#    aion baseline DESIGN           harden the PDK-only control
#    aion new-design NAME           a design directory that runs as it stands
#    aion designs | steps | agents | agent NAME | cells
#    aion artifacts DESIGN [--tag]  what the runs produced
#    aion clean DESIGN --tag T [--step N] --yes      aion gc DESIGN --keep N --yes
#    aion ext rank|new|build|lib DESIGN ...          the functional cell extractor
#    aion explore DESIGN sweep|survey ...            the mining design space
# ================================================================

from __future__ import annotations

import argparse
import sys
import time
import traceback
from typing import Optional

import cell_registry

from . import artifacts, config as config_mod, design as design_mod
from . import pdk as pdk_mod, runner as runner_mod, settings as settings_mod
from . import sim, steps, workspace
from .agent import profile as agent_profile
from .coherence import BLOCKED, FAILED, MISSING, OK, STALE, Coherence, Verdict, badge, now
from .runner import Runner, StepFailed, container_running, container_sees_framework, terminate_all
from .style import Style, banner, color, emit, fail, info, note, ok, title, warn


# ---------------------------------------------------------------------
def _common(p: argparse.ArgumentParser, tag: bool = True) -> None:
    p.add_argument("design", help="a directory under designs/")
    if tag:
        p.add_argument("--tag", default=None, help="run tag (default: aion.toml [defaults] tag)")
    p.add_argument("--set", dest="settings", action="append", default=[], metavar="NAME=VALUE",
                   help="override a flow setting (see `aion config DESIGN`)")
    p.add_argument("--agent", default=None, help="agent profile under agents/")
    p.add_argument("--agent-set", dest="agent_settings", action="append", default=[],
                   metavar="KEY=VALUE", help="override one agent.toml key for this run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aion", description="AION netlist-to-layout framework")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the flow, or some of its steps")
    _common(r)
    r.add_argument("steps", nargs="*", metavar="STEP",
                   help="numbers, names or ranges (2..5, 6-); default: all")
    r.add_argument("--draw", choices=("manual", "auto"), help="step 6: DRAW_MODE")
    r.add_argument("-j", "--draw-jobs", type=int, help="step 6: cells drawn at once")
    r.add_argument("--lenient", action="store_true", help="LibreLane checkers -> warnings")
    r.add_argument("--provisional", action="store_true",
                   help="map extension cells on their provisional Liberty if not drawn yet")
    r.add_argument("--force", action="store_true", help="no stale-upstream warning")
    r.add_argument("--dry-run", action="store_true", help="print every command, run nothing")
    r.add_argument("--quiet", action="store_true", help="tool output to the logs only")
    r.add_argument("--timeout", type=int, metavar="SECONDS")

    s = sub.add_parser("status", help="per-step coherence of one run")
    _common(s)

    c = sub.add_parser("config", help="resolved settings and agent of a design")
    _common(c)

    sm = sub.add_parser("sim", help="run the design's testbench on one view")
    _common(sm)
    sm.add_argument("--stage", default="rtl", choices=sim.STAGES)
    sm.add_argument("--waves", action="store_true")

    b = sub.add_parser("baseline", help="harden the PDK-only control into <run>/baseline/")
    _common(b)
    b.add_argument("--lenient", action="store_true")

    n = sub.add_parser("new-design", help="create designs/<NAME>/ from the template")
    n.add_argument("name")
    n.add_argument("--lang", default="verilog", help="vhdl | verilog | systemverilog")
    n.add_argument("--top", default=None)
    n.add_argument("--clock", default="clk", help="clock port")
    n.add_argument("--period", type=float, default=20.0, help="clock period, ns")
    n.add_argument("--die", default="150,150", help="die W,H in um")
    n.add_argument("--agent", default=None)
    n.add_argument("--force", action="store_true")

    sub.add_parser("designs", help="list designs")
    sub.add_parser("steps", help="list the flow steps")
    sub.add_parser("agents", help="list agent profiles")
    a = sub.add_parser("agent", help="show one agent profile, resolved")
    a.add_argument("name")
    a.add_argument("--agent-set", dest="agent_settings", action="append", default=[])
    cl = sub.add_parser("cells", help="the shared library of drawn cells")
    cl.add_argument("--pdk", default=None)

    ar = sub.add_parser("artifacts", help="runs of a design, or one run in detail")
    ar.add_argument("design")
    ar.add_argument("--tag", default=None, help="show this run; omit to list them all")
    cn = sub.add_parser("clean", help="delete a run, or one step of it")
    cn.add_argument("design")
    cn.add_argument("--tag", required=True)
    cn.add_argument("--step", default=None)
    cn.add_argument("--yes", action="store_true")
    g = sub.add_parser("gc", help="delete all but the newest runs of a design")
    g.add_argument("design")
    g.add_argument("--keep", type=int, default=3)
    g.add_argument("--yes", action="store_true")

    e = sub.add_parser("ext", help="the functional cell extractor (PDK extension)")
    esub = e.add_subparsers(dest="ext_command", required=True)
    er = esub.add_parser("rank", help="rank multi-cell Boolean functions of the netlist")
    _common(er)
    er.add_argument("args", nargs=argparse.REMAINDER)
    en = esub.add_parser("new", help="skeleton of a new extension cell")
    _common(en)
    en.add_argument("cell")
    en.add_argument("--force", action="store_true")
    eb = esub.add_parser("build", help="characterize, draw and publish one extension cell")
    _common(eb)
    eb.add_argument("cell")
    eb.add_argument("--from", dest="start", default="characterize",
                    choices=("characterize", "layout", "post-layout"))
    eb.add_argument("--only", default=None,
                    choices=("characterize", "layout", "post-layout", "publish"))
    eb.add_argument("--draw", choices=("manual", "auto"))
    eb.add_argument("--corners", default="all", choices=("all", "typ"))
    eb.add_argument("--driver-cell", default=None)
    eb.add_argument("--pex-mode", type=int, default=2, choices=(1, 2, 3))
    eb.add_argument("--accept-delay-loss", type=float, default=None, metavar="PCT")
    eb.add_argument("--jobs", type=int, default=8)
    eb.add_argument("--dry-run", action="store_true")
    eb.add_argument("--quiet", action="store_true")
    el = esub.add_parser("lib", help="splice the design's extension cells into its libraries")
    _common(el)
    el.add_argument("--provisional", action="store_true")

    x = sub.add_parser("explore", help="sweep the step-2/3 mining knobs (explore_cells)")
    _common(x)
    x.add_argument("args", nargs=argparse.REMAINDER)
    return parser


# ---------------------------------------------------------------------
def load(args, require_ok: bool = False):
    """(workspace, config) for args.design, every layer applied."""
    s = settings_mod.get()
    design = design_mod.load(args.design, s.default_pdk)
    pdk = pdk_mod.load(design.pdk)
    runner_mod.set_pdk(pdk.name)
    ws = workspace.make(design, pdk, getattr(args, "tag", None) or s.default_tag)
    cell_registry.use(ws.registry)
    assignments = list(getattr(args, "settings", []) or [])
    if getattr(args, "agent", None):
        assignments.append(f"AGENT={args.agent}")
    try:
        cfg = config_mod.build(s, design, assignments)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None
    if require_ok:
        problems = design.problems()
        if problems:
            for p in problems:
                fail(f"{design.name}: {p}")
            raise SystemExit(2)
    return ws, cfg


def _container_ready() -> bool:
    s = settings_mod.get()
    if not container_running():
        fail(f"the EDA container {s.container_name} is not running")
        note("start iic-osic-tools; LibreLane, ngspice, magic, netgen, klayout and "
             "kepler-formal live in it")
        return False
    if not container_sees_framework():
        fail(f"{s.container_name} does not mount this checkout at {s.container_mount}")
        note(f"recreate it with  -v {workspace.FRAMEWORK_ROOT}:{s.container_mount}")
        note("or point [container] mount in aion.local.toml at where it is mounted")
        return False
    return True


# ---------------------------------------------------------------------
def print_status(ws) -> int:
    coherence = Coherence(ws.coherence_file)
    title(f" {ws.design.name} / {ws.tag} ")
    emit(color(f"  {'step':<24}{'state':<12}{'finished':<28}notes", Style.BOLD))
    incoherent, stale = 0, set()
    for step in steps.STEPS:
        upstream = list(steps.upstream_of(step))
        verdict = coherence.check(step.key, upstream)
        if verdict.state == OK and stale.intersection(upstream):
            verdict = Verdict(step.key, STALE, [f"depends on {u}, which is stale"
                                               for u in sorted(stale.intersection(upstream))])
        record = coherence.get(step.key) or {}
        notes = "; ".join(record.get("notes", [])[:2])
        emit(f"  {step.key:<24}{badge(verdict.state)}  {record.get('finished', '—'):<26}"
             + color(notes[:40], Style.DIM))
        for reason in verdict.reasons[:6]:
            emit(color(f"      · {reason}", Style.YELLOW))
        if verdict.state in (STALE, FAILED, MISSING):
            stale.add(step.key)
        if verdict.state in (STALE, FAILED):
            incoherent += 1
    emit()
    if incoherent:
        warn(f"{incoherent} step(s) no longer describe what is on disk")
    else:
        ok("every recorded step is consistent with its inputs")
    return 1 if incoherent else 0


def print_steps() -> int:
    title(" aion flow ")
    for step in steps.STEPS:
        number = step.key.split("_", 1)[0]
        emit(f"  {color(number.rjust(2), Style.CYAN, Style.BOLD)}  "
             f"{color(step.key, Style.BOLD):<34} {step.summary}")
        if step.upstream:
            note(f"    after: {', '.join(step.upstream)}")
    return 0


def print_config(ws, cfg, agent_settings) -> int:
    d = ws.design
    title(f" {d.name} ")
    note(f"design   {workspace.rel(d.toml)}   top {d.top}   {d.language}   pdk {ws.pdk.name}")
    note(f"sources  {len(d.rtl)} file(s): " + ", ".join(workspace.rel(p) for p in d.rtl[:6])
         + (" …" if len(d.rtl) > 6 else ""))
    note(f"tb       {workspace.rel(d.tb_dir)}/{d.tb_module or '—'}.py   "
         f"rtl {d.rtl_sim}   gates {d.gate_sim}")
    note(f"librelane {workspace.rel(d.librelane_config)}   sdc {workspace.rel(d.sdc)}")
    note(f"run      {workspace.rel(ws.run_dir)}   library {workspace.rel(ws.library)}")
    ext = ws.pdk_ext_cells()
    note(f"pdk ext  {workspace.rel(ws.pdk_ext_dir)}: {', '.join(ext) or 'none'}")
    for problem in d.problems():
        warn(problem)
    emit()
    emit(color(f"  {'setting':<26}{'value':<34}from", Style.BOLD))
    for name in cfg.known():
        origin = cfg.origin(name)
        line = f"  {name:<26}{str(getattr(cfg, name)):<34}{origin}"
        emit(line if origin != "default" else color(line, Style.DIM))
    emit()
    try:
        profile = agent_profile.load(cfg.AGENT, agent_settings)
        info(f"agent {profile.name} ({cfg.origin('AGENT')}): {profile.summary()}")
        note(f"chain {' -> '.join(profile.chain)}   digest {profile.digest()}")
        note(f"skills {', '.join(profile.skills()) or '—'}")
    except SystemExit as exc:
        warn(str(exc))
    return 0


def print_designs() -> int:
    title(" designs ")
    names = design_mod.available()
    if not names:
        note("none yet — make new-design DESIGN=<name> LANG=vhdl|verilog|systemverilog")
    for name in names:
        try:
            d = design_mod.load(name)
        except SystemExit as exc:
            warn(str(exc))
            continue
        runs = artifacts.runs_of(name)
        problems = d.problems()
        emit(f"  {name:<20}{d.language:<15}top {d.top:<20}{len(runs)} run(s)"
             + (color(f"   {len(problems)} problem(s)", Style.YELLOW) if problems else ""))
    return 0


def print_agents() -> int:
    title(" agent profiles ")
    default = settings_mod.get().default_agent
    for name in agent_profile.available():
        try:
            p = agent_profile.load(name)
        except SystemExit as exc:
            warn(str(exc))
            continue
        star = color(" (default)", Style.GREEN) if name == default else ""
        emit(f"  {color(name, Style.BOLD):<30}{star} {p.description}")
        note(f"    {p.summary()}   extends {' -> '.join(p.chain[1:]) or '—'}")
    return 0


def print_agent(name: str, overrides) -> int:
    p = agent_profile.load(name, overrides)
    title(f" agent {p.name} ")
    note(f"chain   {' -> '.join(p.chain)}")
    note(f"digest  {p.digest()}")
    for key, value in p.resolved().items():
        emit(f"  {key:<20}{value}")
    emit()
    info("files")
    for rel_path, source in sorted(p.files.items()):
        note(f"  {rel_path:<40}{workspace.rel(source)}")
    return 0


# ---------------------------------------------------------------------
def warn_if_stale(coherence, step, selected, done, force) -> None:
    problems = []
    for key in steps.upstream_of(step):
        if key in done:
            continue
        verdict = coherence.check(key, steps.upstream_of(steps.BY_KEY[key]))
        if verdict.state in (STALE, MISSING, FAILED):
            problems.append((key, verdict))
        elif key in selected:
            problems.append((key, Verdict(key, STALE, ["queued to re-run AFTER this step"])))
    if not problems or force:
        return
    emit()
    emit(color(banner(" STALE UPSTREAM ", fill="!"), Style.BG_YELLOW, Style.BOLD))
    for key, verdict in problems:
        warn(f"{key} is {verdict.state.upper()}")
        for reason in verdict.reasons[:4]:
            note(f"  · {reason}")
    note("running anyway — the result will not describe one coherent design")
    emit()


def run_step(step, ctx, coherence, index: int, total: int) -> str:
    emit()
    emit(color(banner(f" STEP {index}/{total}: {step.title.upper()} ", fill="═"),
               Style.CYAN, Style.BOLD))
    note(step.summary)
    note(f"output → {workspace.rel(step.outdir(ctx))}/")
    emit()
    step.dry_run = ctx.runner.dry_run
    step.ensure(ctx)
    started, began = now(), time.monotonic()
    ctx.notes.clear()
    ctx.record.clear()
    ctx.runner.commands.clear()
    ctx.runner.log_dir = step.log_dir(ctx)
    try:
        status = step.run(ctx) or OK
    except StepFailed as exc:
        if not ctx.runner.dry_run:
            coherence.record(step.key, status=FAILED, started=started,
                             inputs=step.inputs(ctx), outputs=step.outputs(ctx),
                             config=ctx.cfg.digest(), notes=ctx.notes + [str(exc)],
                             commands=list(ctx.runner.commands), extra=dict(ctx.record))
        emit()
        emit(color(banner(" FAILED ", fill="═"), Style.BG_RED, Style.WHITE, Style.BOLD))
        fail(f"{step.key}: {exc}")
        if exc.log:
            note(f"log: {workspace.rel(exc.log)}")
        raise
    if ctx.runner.dry_run:
        return status
    coherence.record(step.key, status=status, started=started,
                     inputs=step.inputs(ctx), outputs=step.outputs(ctx),
                     config=ctx.cfg.digest(), notes=list(ctx.notes),
                     commands=list(ctx.runner.commands), extra=dict(ctx.record))
    elapsed = time.monotonic() - began
    emit()
    (warn if status == BLOCKED else ok)(
        f"{step.key} {'is waiting on you' if status == BLOCKED else 'completed'} "
        f"({elapsed:.0f}s)")
    return status


def cmd_run(args, argv) -> int:
    ws, cfg = load(args, require_ok=True)
    if args.draw:
        cfg.set("DRAW_MODE", args.draw, "--draw")
    if args.draw_jobs is not None:
        cfg.set("DRAW_JOBS", args.draw_jobs, "-j")
    if args.lenient:
        cfg.set("LENIENT", True, "--lenient")
    if args.timeout:
        cfg.set("TIMEOUT", args.timeout, "--timeout")
    try:
        chosen = steps.expand(args.steps)
    except KeyError as exc:
        raise SystemExit(f"error: {str(exc).strip(chr(34))}") from None

    profile = None
    if any(s.key == "6_layout_drawing" for s in chosen) and cfg.DRAW_MODE == "auto":
        profile = agent_profile.load(cfg.AGENT, args.agent_settings)
    if cfg.LENIENT:
        warn("LENIENT is on: LibreLane's hard checkers are warnings. Never sign off this run.")
    if any(s.needs_container for s in chosen) and not args.dry_run and not _container_ready():
        return 4

    runner = Runner(log_dir=ws.logs, timeout=cfg.TIMEOUT, dry_run=args.dry_run, quiet=args.quiet)
    coherence = Coherence(ws.coherence_file)
    ctx = steps.Context(cfg=cfg, ws=ws, runner=runner, coherence=coherence, force=args.force,
                        ext_provisional=args.provisional,
                        agent_overrides=list(args.agent_settings))

    title(f" AION · {ws.design.name} · {ws.tag} ")
    info(f"{len(chosen)} step(s): " + ", ".join(s.key for s in chosen))
    note(f"run dir {workspace.rel(ws.run_dir)}")
    if cfg.overrides:
        info("overrides: " + "  ".join(f"{k}={v}" for k, v in cfg.overrides.items()))
    if profile is not None:
        info(f"agent {profile.name}: {profile.summary()}")
    if args.dry_run:
        warn("dry run — nothing will be executed")
    else:
        artifacts.record_invocation(ws, cfg, chosen, profile, argv)

    selected, done, blocked = {s.key for s in chosen}, set(), []
    status = "ok"
    try:
        for index, step in enumerate(chosen, start=1):
            warn_if_stale(coherence, step, selected, done, args.force)
            result = run_step(step, ctx, coherence, index, len(chosen))
            done.add(step.key)
            if result == BLOCKED:
                blocked.append(step.key)
    except StepFailed as exc:
        status = "failed"
        code = exc.returncode or 1
        if code < 0:
            fail(f"killed by signal {-code} (the usual cause is the OOM killer)")
            return 1
        return code if 0 < code < 256 else 1
    except KeyboardInterrupt:
        status = "interrupted"
        emit()
        killed = terminate_all()
        fail(f"interrupted — killed {killed} running command(s)" if killed else "interrupted")
        return 130
    except Exception:                                    # pragma: no cover
        status = "crashed"
        traceback.print_exc()
        fail("unexpected error")
        return 1
    finally:
        if not args.dry_run:
            artifacts.finish_invocation(ws, status if status != "ok" or not blocked else "blocked")

    emit()
    if blocked:
        emit(color(banner(" WAITING ", fill="═"), Style.CYAN, Style.BOLD))
        warn("finished, but these steps need you: " + ", ".join(blocked))
        return 3
    emit(color(banner(" SUCCESS ", fill="═"), Style.GREEN, Style.BOLD))
    ok(f"{len(chosen)} step(s) completed   ({workspace.rel(ws.run_dir)})")
    return 0


def cmd_sim(args) -> int:
    ws, cfg = load(args, require_ok=True)
    runner = Runner(log_dir=ws.logs / "sim", timeout=cfg.TIMEOUT)
    if not ws.design.has_testbench:
        fail(sim.skip_reason(ws, cfg) or "no testbench")
        return 2
    ext_cells = ws.pdk_ext_cells() if cfg.PDK_EXT else []
    try:
        if args.stage == "rtl":
            sim.rtl(ws, runner, waves=args.waves)
        elif args.stage == "synth":
            sim.gate(ws, runner, "synth", ws.netlist("1_synth"),
                     [ws.pdk_ext_dir / c / f"{c}.v" for c in ext_cells], waves=args.waves)
        elif args.stage == "rewrite":
            sim.gate(ws, runner, "rewrite", ws.netlist("3_rewrite"),
                     [ws.step("3_rewrite") / "nl" / "aion_cells.v"]
                     + [ws.pdk_ext_dir / c / f"{c}.v" for c in ext_cells], waves=args.waves)
        else:
            from .steps.s7_pnr import drawn_cells, instantiated_cells, sdf_file, used_cells
            out = ws.step("7_pnr") if args.stage == "pnr" else ws.baseline_dir
            netlist = out / "nl" / f"{ws.design.top}.nl.v"
            mined = instantiated_cells(netlist, cfg.CELL_PREFIX) & drawn_cells(ws.cells_dir)
            ext = used_cells(netlist, ext_cells) & drawn_cells(ws.pdk_ext_dir)
            models = ([ws.cells_dir / c / f"{c}.v" for c in sorted(mined)]
                      + [ws.pdk_ext_dir / c / f"{c}.layout.v" for c in sorted(ext)])
            sim.gate(ws, runner, args.stage, netlist, models,
                     sdf=sdf_file(ws, out, cfg.SDF_CORNER) if cfg.SIM_SDF else None,
                     waves=args.waves)
    except StepFailed as exc:
        fail(str(exc))
        return exc.returncode or 1
    return 0


def cmd_baseline(args) -> int:
    ws, cfg = load(args, require_ok=True)
    if args.lenient:
        cfg.set("LENIENT", True, "--lenient")
    if not _container_ready():
        return 4
    from .steps.s7_pnr import report
    from .steps.s9_report import build_baseline
    runner = Runner(log_dir=ws.logs / "baseline", timeout=cfg.TIMEOUT)
    ctx = steps.Context(cfg=cfg, ws=ws, runner=runner, coherence=Coherence(ws.coherence_file))
    try:
        build_baseline(ctx)
    except StepFailed as exc:
        fail(str(exc))
        return exc.returncode or 1
    report(ctx, ws.baseline_dir)
    ok(f"baseline in {workspace.rel(ws.baseline_dir)}")
    return 0


def cmd_ext(args) -> int:
    ws, cfg = load(args)
    if args.ext_command == "new":
        from .ext import new_cell
        new_cell(ws, args.cell, force=args.force)
        return 0
    if args.ext_command == "lib":
        from . import pdk_ext
        if not ws.pdk_ext_cells():
            note(f"no cells under {workspace.rel(ws.pdk_ext_dir)} — nothing to splice")
            return 0
        try:
            pdk_ext.build(ws, provisional=args.provisional)
        except StepFailed as exc:
            fail(str(exc))
            return 2
        return 0
    if args.ext_command == "rank":
        from .ext import rank_functions as rf
        rf.DEFAULT_NETLIST = ws.netlist("1_synth")
        rf.DEFAULT_CELL_LIB = ws.pdk.tech_dict
        rf.EXT_CELL_LIB = ws.pdk_ext_dict if ws.pdk_ext_cells() else None
        rf.DEFAULT_WORK = ws.explore_dir / "work"
        rf.DEFAULT_TOP = ws.design.top
        if not rf.DEFAULT_NETLIST.exists() and "--netlist" not in args.args:
            fail(f"no synthesized netlist at {workspace.rel(rf.DEFAULT_NETLIST)} — run step 1")
            return 2
        return rf.main([a for a in args.args if a != "--"])
    if args.ext_command == "build":
        from .ext import build as ext_build
        if args.draw:
            cfg.set("DRAW_MODE", args.draw, "--draw")
        if not args.dry_run and args.only != "publish" and not _container_ready():
            return 4
        runner = Runner(log_dir=ws.logs / "pdk_extension", timeout=cfg.TIMEOUT,
                        dry_run=args.dry_run, quiet=args.quiet)
        driver = args.driver_cell or ws.pdk.raw.get("ext", {}).get("driver_cell")
        builder = ext_build.Builder(ws, cfg, runner, agent_overrides=args.agent_settings,
                                    corners=args.corners, jobs=args.jobs, driver=driver,
                                    pex_mode=args.pex_mode,
                                    accept_delay_loss=args.accept_delay_loss)
        try:
            return ext_build.run(builder, args.cell, start=args.start, only=args.only)
        except StepFailed as exc:
            fail(str(exc))
            return exc.returncode or 1
    return 2


def cmd_explore(args) -> int:
    ws, cfg = load(args)
    from .ext import explore_cells as ex
    ex.WS, ex.Config = ws, cfg
    ex.DEFAULT_NETLIST = ws.netlist("1_synth")
    ex.DEFAULT_GATES = ws.pdk.spice
    ex.DEFAULT_WORK = ws.explore_dir / "work"
    ex.DEFAULT_TOP = ws.design.top
    ex.DEFAULT_SURVEY = ws.explore_dir / "survey.json"
    ex.DEFAULT_SWEEP = ws.explore_dir / "sweep.json"
    ws.explore_dir.mkdir(parents=True, exist_ok=True)
    rest = [a for a in args.args if a != "--"] or ["sweep"]
    return ex.main(rest)


# ---------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    command = args.command

    if command == "run":
        return cmd_run(args, argv)
    if command == "status":
        ws, _ = load(args)
        return print_status(ws)
    if command == "config":
        ws, cfg = load(args)
        return print_config(ws, cfg, args.agent_settings)
    if command == "sim":
        return cmd_sim(args)
    if command == "baseline":
        return cmd_baseline(args)
    if command == "new-design":
        try:
            w, h = (float(v) for v in args.die.split(","))
        except ValueError:
            raise SystemExit(f"error: --die {args.die!r}: expected W,H in um") from None
        from .scaffold import new_design
        new_design(args.name, language=args.lang, top=args.top, clock_port=args.clock,
                   period_ns=args.period, die=(w, h), agent=args.agent, force=args.force,
                   pdk=settings_mod.get().default_pdk)
        return 0
    if command == "designs":
        return print_designs()
    if command == "steps":
        return print_steps()
    if command == "agents":
        return print_agents()
    if command == "agent":
        return print_agent(args.name, args.agent_settings)
    if command == "cells":
        return artifacts.list_cells(args.pdk or settings_mod.get().default_pdk)
    if command == "artifacts":
        if args.tag is None:
            return artifacts.list_runs(args.design)
        ws, _ = load(args)
        return artifacts.show_run(ws)
    if command == "clean":
        ws, _ = load(args)
        step = steps.resolve(args.step).key if args.step else None
        return artifacts.clean(ws, step, yes=args.yes)
    if command == "gc":
        return artifacts.gc(args.design, args.keep, yes=args.yes)
    if command == "ext":
        return cmd_ext(args)
    if command == "explore":
        return cmd_explore(args)
    return 2
