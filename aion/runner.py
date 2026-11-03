# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Command execution for the AION flow
#
#  Three ways to run a command, and picking the wrong one is silent:
#
#  host() / flow_host()   on the host.  aion_opt mining and rewriting, the
#                         SPICE splitter.  Takes HOST paths.
#
#  container()            inside iic-osic-tools, through `docker exec` with
#                         the mount aion.toml declares.  aion_char,
#                         kepler-formal, the minimizer's SPICE proof and
#                         LibreLane live there.  Takes CONTAINER paths.
#
#  layout()               `make` on the host, but the layout tool opens the
#                         container itself per step; AION_CONTAINER_MOUNT is
#                         pointed at this checkout's aion_flow so it runs
#                         against the submodule and not some other copy.
# ================================================================

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from . import settings, workspace
from .style import Style, color, emit, info, note


class StepFailed(RuntimeError):
    """A command in a step returned non-zero."""

    def __init__(self, message: str, returncode: int, log: Optional[Path] = None):
        super().__init__(message)
        self.returncode = returncode
        self.log = log


@dataclass
class Result:
    command: str
    returncode: int
    output: str
    duration_s: float
    log: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def verdict(self, prefix: str) -> Optional[str]:
        """The aion_layout convention: exactly one line starts at column 0
        and it is the verdict (`RESULT: PASS`, `COMPARE: WIN`, `STEP: x OK`).
        Everything else is indented, so an anchored match is unambiguous.
        """
        for line in self.output.splitlines():
            if line.startswith(prefix):
                return line.strip()
        return None


# Every child this process has started, so one Ctrl-C can take the whole
# tree down. start_new_session=True below puts each child in its own process
# group -- which is what lets a timeout kill a whole `make -> docker -> magic`
# chain rather than only the direct child, but it also means the terminal's
# SIGINT never reaches them. Without this registry an interrupted run leaves
# container steps writing into the directory the flow just gave up on, and,
# in step 6, drawing agents still talking to the API.
_CHILDREN: set = set()
_CHILDREN_LOCK = threading.Lock()
_CANCELLED = threading.Event()


def register(process) -> None:
    """Track a child so terminate_all() can reach it."""
    with _CHILDREN_LOCK:
        _CHILDREN.add(process)


def unregister(process) -> None:
    with _CHILDREN_LOCK:
        _CHILDREN.discard(process)


def cancelled() -> bool:
    """True once terminate_all() ran: no further command may start."""
    return _CANCELLED.is_set()


def terminate_all() -> int:
    """Kill every running child and stop any new one from starting.

    Returns how many were still running, which is the only honest thing to
    print after a Ctrl-C with several cells in flight.
    """
    _CANCELLED.set()
    with _CHILDREN_LOCK:
        running = list(_CHILDREN)
    for process in running:
        terminate(process)
    return len(running)


# Noise every container run prints before the real output.
_CONTAINER_NOISE = (
    "[INFO] Final PATH variable:",
    "[INFO] Final PYTHONPATH variable:",
    "========== AION CONTAINER OUTPUT ==========",
    "[INFO] Sourcing",
)


@dataclass
class Runner:
    """Runs commands, echoes them, and tees every byte into a log file."""

    log_dir: Path
    timeout: int = 4 * 60 * 60
    dry_run: bool = False
    quiet: bool = False
    commands: list = field(default_factory=list)

    # -----------------------------------------------------------------
    def host(self, argv: Sequence[str], *, cwd: Optional[Path] = None,
             name: str = "", env: Optional[dict] = None) -> Result:
        """Any command on the host."""
        return self._run(list(argv), cwd=cwd or settings.FRAMEWORK_ROOT,
                         name=name or Path(argv[0]).name, env=env)

    def flow_host(self, target: str, variables: Sequence[str] = (), *,
                  name: str = "", env: Optional[dict] = None) -> Result:
        """`make <target>` in the aion_flow submodule, on the host.

        cwd is load-bearing: aion_flow's Makefile has REPO_ROOT := $(realpath .)
        and a relative PYTHONPATH, so it only works from its own root.
        """
        argv = ["make", "--no-print-directory", target, *variables]
        return self._run(argv, cwd=workspace.AION_FLOW, name=name or target, env=env)

    def container(self, target: str, variables: Sequence[str] = (), *,
                  name: str = "") -> Result:
        """`make <target>` in the aion_flow submodule, inside the container.

        Every path in `variables` must already be a container path.
        """
        # Re-parsed by `bash -lc` inside the container, so a value with a
        # space or a glob in it would become two arguments -- and make reads
        # the second as a goal.  Quote each assignment, not the whole line.
        inner = " ".join(["make", "--no-print-directory", target,
                          *(_quote_assignment(v) for v in variables)])
        return self.container_shell(inner, cwd=workspace.AION_FLOW,
                                    name=name or target)

    def container_shell(self, command: str, *, cwd: Path,
                        name: str = "container") -> Result:
        """One shell command inside the container, from `cwd` (a host path)."""
        s = settings.get()
        script = (f"export PDK={shlex.quote(_pdk_name())}; "
                  f"export PDK_ROOT={shlex.quote(s.pdk_root)}; "
                  f"export AION_IN_DOCKER=1; "
                  f"cd {shlex.quote(workspace.container(cwd))} && {command}")
        argv = ["docker", "exec", "-i", "-u", f"{os.getuid()}:{os.getgid()}",
                s.container_name, "bash", "-lc", script]
        return self._run(argv, cwd=cwd, name=name, strip_noise=True)

    def layout(self, target: str, variables: Sequence[str] = (), *,
               name: str = "", env: Optional[dict] = None) -> Result:
        """`make aion-layout-<target>` on the host, container mount corrected."""
        return self.flow_host(f"aion-layout-{target}", variables,
                              name=name or f"layout:{target}",
                              env={**layout_env(), **(env or {})})

    # -----------------------------------------------------------------
    def _run(self, argv: Sequence[str], *, cwd: Path, name: str,
             env: Optional[dict] = None, strip_noise: bool = False) -> Result:
        pretty = " ".join(shlex.quote(a) for a in argv)
        if cancelled():
            # Step 6 runs cells in parallel, so a Ctrl-C lands while other
            # threads are between commands. Starting the next one anyway is
            # how an interrupted run keeps going for another ten minutes.
            raise StepFailed(f"{name}: not started, the run was interrupted", 130)
        self.commands.append({"name": name, "cwd": workspace.rel(cwd), "argv": list(argv)})

        emit(color("  ▶ ", Style.DIM) + color(_clip(pretty), Style.WHITE))
        if Path(cwd).resolve() != settings.FRAMEWORK_ROOT:
            note(f"cwd {workspace.rel(cwd)}")

        if self.dry_run:
            return Result(pretty, 0, "", 0.0)

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{_slug(name)}.log"

        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        # An outer `make` (the framework Makefile) must not leak its flags or
        # its level into the tools' own Makefiles.
        full_env.pop("MAKEFLAGS", None)
        full_env.pop("MAKELEVEL", None)

        started = time.monotonic()
        chunks: list = []
        timed_out = threading.Event()
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"# cwd: {cwd}\n# cmd: {pretty}\n\n")
            log.flush()
            # Own process group, so a timeout can take down make, its recipe
            # shell and the tool it spawned rather than only the direct child.
            process = subprocess.Popen(
                argv, cwd=str(cwd), env=full_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", bufsize=1,
                start_new_session=True,
            )
            register(process)

            # A watchdog, not process.wait(timeout=): the read loop below
            # blocks until stdout reaches EOF, which is exactly what a hung
            # command never does, so a deadline checked after it can never
            # fire. This one can.
            def _expire() -> None:
                timed_out.set()
                terminate(process)

            watchdog = threading.Timer(self.timeout, _expire)
            watchdog.daemon = True
            watchdog.start()
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()          # so `tail -f` shows a live step
                    chunks.append(line)
                    if strip_noise and _is_noise(line):
                        continue
                    if not self.quiet:
                        emit("    " + line.rstrip("\n"))
                returncode = process.wait()
            except KeyboardInterrupt:
                # The child is in its own session, so it never saw the
                # SIGINT the terminal sent us.
                terminate(process)
                raise
            finally:
                watchdog.cancel()
                unregister(process)
            if timed_out.is_set():
                returncode = 124
                chunks.append(f"\n[flow] timed out after {self.timeout}s\n")
                log.write(chunks[-1])

        duration = time.monotonic() - started
        output = "".join(chunks)
        result = Result(pretty, returncode, output, duration, log_path)

        if result.ok:
            info(f"done in {duration:.1f}s   log: {workspace.rel(log_path)}")
        return result

    # -----------------------------------------------------------------
    def check(self, result: Result, what: str) -> Result:
        """Raise unless the command succeeded."""
        if not result.ok:
            raise StepFailed(
                f"{what} failed (exit {result.returncode})",
                result.returncode, result.log,
            )
        return result


def _quote_assignment(text: str) -> str:
    """Quote a `NAME=VALUE` so one shell round-trip leaves it intact.

    Only the value is quoted: `make` must still see NAME=VALUE as one word,
    and quoting the whole thing would make it a goal instead.
    """
    name, sep, value = text.partition("=")
    if not sep or not name or any(c in name for c in " \t'\""):
        return shlex.quote(text)
    return f"{name}={shlex.quote(value)}" if value else text


def terminate(process) -> None:
    """Stop a command and everything it started.

    The direct child is usually `make` or docker_run.sh; the work is in its
    children. Killing only the child leaves a container step running and
    still writing into the directory the flow is about to declare failed.
    """
    import signal
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                process.kill()
            except OSError:
                pass
            return
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue


def _is_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return any(marker in stripped for marker in _CONTAINER_NOISE)


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def layout_env() -> dict:
    """What the layout tool needs to open the right container on the right tree."""
    return {"AION_CONTAINER_MOUNT": workspace.container(workspace.AION_FLOW),
            "AION_CONTAINER": settings.get().container_name}


_PDK = {"name": "ihp-sg13g2"}


def set_pdk(name: str) -> None:
    """The PDK the container commands export as $PDK."""
    _PDK["name"] = name


def _pdk_name() -> str:
    return _PDK["name"]


def _clip(text: str, limit: int = 400) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


def container_running() -> bool:
    """True when the EDA container is up."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}",
             settings.get().container_name],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def container_sees_framework() -> bool:
    """True when the container has this checkout at the configured mount.

    A container that is up but does not mount the framework fails every
    containerised step with "No such file or directory" a long way from the
    cause, so the flow asks once, up front.
    """
    s = settings.get()
    marker = f"{s.container_mount}/aion.toml"
    try:
        out = subprocess.run(
            ["docker", "exec", s.container_name, "test", "-f", marker],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0
