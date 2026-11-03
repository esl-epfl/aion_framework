# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               agents/<name>/ -- one drawing-agent profile
#
#  A profile is a directory, and everything the agent is given comes out of
#  it:
#
#    agents/<name>/
#      agent.toml        backend, model, effort, turn budget, tools, cost cap
#      prompts/          the task prompts, one per kind of drawing job
#        layout.md         a mined cell (step 6)
#        ext_layout.md     a hand-designed PDK-extension cell
#      skills/<skill>/   Claude Code skills, installed into the session
#      settings.json     Claude Code settings for the session (hooks, ...)
#      CLAUDE.md         standing instructions, loaded as project memory
#
#  `extends = "<other>"` in agent.toml inherits the other profile: its
#  TOML is the base this one's keys override, and any file this profile does
#  not have is taken from it.  So a cheaper variant is a three-line TOML.
#
#  For every run the profile is MATERIALIZED into the run directory -- the
#  resolved agent.toml, every inherited file, the rendered settings -- and
#  that directory is the agent's working directory.  The run's artifacts thus
#  carry exactly what the agent was given, with a digest, whatever happens to
#  agents/ afterwards.
# ================================================================

from __future__ import annotations

import hashlib
import json
import shutil
import string
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..settings import read_toml
from .. import workspace

BACKENDS = ("claude-code",)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
PROMPT_KINDS = ("layout", "ext_layout")
#: Files and directories a profile may carry, beside agent.toml.
CONTENT = ("prompts", "skills", "settings.json", "CLAUDE.md")


@dataclass
class Profile:
    name: str
    description: str = ""
    backend: str = "claude-code"
    model: Optional[str] = None
    effort: Optional[str] = None
    max_turns: int = 12               # agent sessions per cell
    turn_timeout: int = 1800          # seconds per session
    idle_turns_abort: int = 2         # untouched generator this many times = stop
    max_usd_per_turn: Optional[float] = None   # --max-budget-usd
    max_usd_per_cell: Optional[float] = None   # the loop stops past this
    permission_mode: str = "acceptEdits"
    allowed_tools: list = field(default_factory=lambda: [
        "Read", "Edit", "Write", "Glob", "Grep",
        "Bash(make:*)", "Bash(python3:*)", "Bash(sed:*)", "Bash(cat:*)"])
    disallowed_tools: list = field(default_factory=list)
    #: Which Claude Code setting files apply.  "project" alone keeps the
    #: session to the profile's own settings, skills and CLAUDE.md, so a hook
    #: or skill in ~/.claude cannot change what the agent does between runs.
    setting_sources: str = "project"
    stream: bool = True
    extra_args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    #: The profile chain, most specific first, and the files resolved from it.
    chain: list = field(default_factory=list)
    files: dict = field(default_factory=dict)     # relative path -> source file
    overrides: dict = field(default_factory=dict)

    # -----------------------------------------------------------------
    def prompt(self, kind: str) -> str:
        rel = f"prompts/{kind}.md"
        if rel not in self.files:
            raise SystemExit(f"error: agent {self.name!r} has no {rel} "
                             f"(looked in {', '.join(self.chain)})")
        return Path(self.files[rel]).read_text()

    def skills(self) -> list:
        return sorted({Path(rel).parts[1] for rel in self.files
                       if rel.startswith("skills/") and len(Path(rel).parts) > 2})

    def digest(self) -> str:
        """Content hash of everything the agent is given, overrides included."""
        h = hashlib.sha256()
        for rel in sorted(self.files):
            h.update(rel.encode() + b"\0")
            h.update(Path(self.files[rel]).read_bytes())
        h.update(json.dumps(self.resolved(), sort_keys=True).encode())
        return "sha256:" + h.hexdigest()[:16]

    def resolved(self) -> dict:
        data = asdict(self)
        for key in ("files", "chain"):
            data.pop(key)
        return data

    def summary(self) -> str:
        bits = [self.backend, self.model or "cli-default-model",
                f"effort {self.effort or 'cli-default'}",
                f"{self.max_turns} turn(s) x {self.turn_timeout}s"]
        if self.max_usd_per_cell is not None:
            bits.append(f"≤ ${self.max_usd_per_cell:g}/cell")
        return ", ".join(bits)

    # -----------------------------------------------------------------
    def materialize(self, dest: Path, variables: dict) -> Path:
        """Write the session workspace: .claude/{settings.json,skills}, CLAUDE.md.

        `variables` fill $NAME placeholders in settings.json (paths that only
        exist once a run does, like the layout tool's hook script).
        """
        if dest.exists():
            shutil.rmtree(dest)
        claude = dest / ".claude"
        (claude / "skills").mkdir(parents=True)
        for rel, source in sorted(self.files.items()):
            src = Path(source)
            if rel == "settings.json":
                text = string.Template(src.read_text()).safe_substitute(variables)
                json.loads(text)        # a broken settings file fails here, not mid-turn
                (claude / "settings.json").write_text(text)
            elif rel == "CLAUDE.md":
                shutil.copy2(src, dest / "CLAUDE.md")
            elif rel.startswith("skills/"):
                target = claude / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
            else:
                target = dest / "profile" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        (dest / "profile.json").write_text(json.dumps(
            {"name": self.name, "chain": self.chain, "digest": self.digest(),
             "resolved": self.resolved(),
             "files": {rel: workspace.rel(src) for rel, src in sorted(self.files.items())}},
            indent=2) + "\n")
        return dest


# ---------------------------------------------------------------------
def available() -> list:
    root = workspace.AGENTS_DIR
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "agent.toml").is_file())


def _chain(name: str) -> list:
    chain, seen = [], set()
    current: Optional[str] = name
    while current:
        if current in seen:
            raise SystemExit(f"error: agent profiles extend each other in a loop: "
                             f"{' -> '.join(chain + [current])}")
        seen.add(current)
        path = workspace.AGENTS_DIR / current / "agent.toml"
        if not path.is_file():
            known = ", ".join(available()) or "none"
            raise SystemExit(f"error: no agent profile {current!r} ({workspace.rel(path)}). "
                             f"Profiles: {known}")
        chain.append(current)
        current = read_toml(path).get("extends")
    return chain


def load(name: str, overrides=()) -> Profile:
    chain = _chain(name)
    data: dict = {}
    files: dict = {}
    for profile in reversed(chain):            # base first, most specific last
        directory = workspace.AGENTS_DIR / profile
        for key, value in read_toml(directory / "agent.toml").items():
            if key == "extends":
                continue
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key] = {**data[key], **value}
            else:
                data[key] = value
        for item in CONTENT:
            path = directory / item
            if path.is_file():
                files[item] = str(path)
            elif path.is_dir():
                for f in sorted(path.rglob("*")):
                    if f.is_file() and not f.name.startswith(".") and "__pycache__" not in f.parts:
                        files[str(f.relative_to(directory))] = str(f)

    known = {f for f in Profile.__dataclass_fields__} - {"name", "chain", "files", "overrides"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise SystemExit(f"error: agents/{name}/agent.toml: unknown key(s) "
                         f"{', '.join(unknown)}. Known: {', '.join(sorted(known))}")
    profile = Profile(name=name, chain=chain, files=files, **data)
    for raw in overrides:
        _override(profile, raw)
    _validate(profile)
    return profile


def _override(profile: Profile, raw: str) -> None:
    """`key=value` from the command line (AGENT_SET="model=... effort=low")."""
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep or key not in Profile.__dataclass_fields__ or key in ("name", "chain", "files"):
        raise SystemExit(f"error: agent override {raw!r}: expected KEY=VALUE with KEY "
                         f"one of the agent.toml keys")
    current = getattr(profile, key)
    text = value.strip()
    if text.lower() in ("none", "null", ""):
        parsed = None
    elif isinstance(current, bool):
        parsed = text.lower() in ("1", "true", "yes", "on")
    elif isinstance(current, int) and not isinstance(current, bool):
        parsed = int(text)
    elif isinstance(current, float) or key.startswith("max_usd"):
        parsed = float(text)
    elif isinstance(current, list):
        parsed = [s.strip() for s in text.split(",") if s.strip()]
    else:
        parsed = text
    setattr(profile, key, parsed)
    profile.overrides[key] = parsed


def _validate(profile: Profile) -> None:
    if profile.backend not in BACKENDS:
        raise SystemExit(f"error: agent {profile.name!r}: backend {profile.backend!r} "
                         f"is not one of {', '.join(BACKENDS)}")
    if profile.effort is not None and profile.effort not in EFFORTS:
        raise SystemExit(f"error: agent {profile.name!r}: effort {profile.effort!r} "
                         f"is not one of {', '.join(EFFORTS)}")
    if profile.max_turns < 1 or profile.turn_timeout < 60:
        raise SystemExit(f"error: agent {profile.name!r}: max_turns >= 1 and "
                         f"turn_timeout >= 60 s, please")
    missing = [k for k in PROMPT_KINDS if f"prompts/{k}.md" not in profile.files]
    if missing:
        raise SystemExit(f"error: agent {profile.name!r} lacks "
                         f"{', '.join(f'prompts/{k}.md' for k in missing)}")
