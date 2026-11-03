# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Framework-wide settings: aion.toml (+ aion.local.toml)
#
#  aion.toml is tracked and holds what is true for every checkout: the default
#  PDK, the default agent, the container's name and where this repository is
#  mounted inside it.  aion.local.toml is git-ignored and overrides any of it
#  for one machine -- a different container name, a different mount point.
# ================================================================

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parent.parent


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"error: {path} is not valid TOML: {exc}") from exc


@dataclass
class Settings:
    container_name: str = "iic-osic-tools_shell_uid_1000"
    #: Where FRAMEWORK_ROOT is visible inside the container.  Every
    #: containerised step names its files under this prefix.
    container_mount: str = "/foss/designs/aion_framework"
    pdk_root: str = "/foss/pdks"
    default_pdk: str = "ihp-sg13g2"
    default_agent: str = "claude-opus"
    default_tag: str = "main"
    #: Flow knobs every design starts from, before its own design.toml.
    flow: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)


def load(root: Path = FRAMEWORK_ROOT) -> Settings:
    data: dict = {}
    sources = []
    for name in ("aion.toml", "aion.local.toml"):
        path = root / name
        if path.exists():
            data = _merge(data, read_toml(path))
            sources.append(name)
    container = data.get("container", {})
    defaults = data.get("defaults", {})
    s = Settings()
    s.container_name = container.get("name", s.container_name)
    s.container_mount = container.get("mount", s.container_mount).rstrip("/")
    s.pdk_root = container.get("pdk_root", s.pdk_root)
    s.default_pdk = defaults.get("pdk", s.default_pdk)
    s.default_agent = defaults.get("agent", s.default_agent)
    s.default_tag = defaults.get("tag", s.default_tag)
    s.flow = dict(data.get("flow", {}))
    s.sources = sources
    return s


_SETTINGS: Settings | None = None


def get() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load()
    return _SETTINGS
