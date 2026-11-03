# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               Host-only checks of the framework itself
#
#  No container, no agent, no LibreLane: what is checked here is the part
#  that is ours -- settings layering, agent profiles, the design template,
#  the LibreLane config each language gets, step selection, the publish gate.
#  `make test`.
# ================================================================

from __future__ import annotations

import json
import shutil
import uuid

import pytest

from aion import config as config_mod
from aion import design as design_mod
from aion import librelane, pdk as pdk_mod, pdk_ext, scaffold, settings, steps, workspace
from aion.agent import profile as agent_profile
from aion.ext import build as ext_build
from aion.ext import new_cell
from aion.runner import StepFailed


@pytest.fixture
def sandbox(monkeypatch):
    """designs/ and runs/ inside the framework (the container sees only that tree)."""
    root = workspace.FRAMEWORK_ROOT / "runs" / f".pytest-{uuid.uuid4().hex[:8]}"
    designs, runs = root / "designs", root / "runs"
    designs.mkdir(parents=True)
    runs.mkdir()
    monkeypatch.setattr(design_mod, "DESIGNS_DIR", designs)
    monkeypatch.setattr(workspace, "RUNS_DIR", runs)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def make_ws(name: str, tag: str = "t"):
    d = design_mod.load(name)
    return workspace.make(d, pdk_mod.load(d.pdk), tag), d


# ---------------------------------------------------------------------
def test_config_layers_and_origin(sandbox):
    scaffold.new_design("lay", language="verilog")
    path = design_mod.DESIGNS_DIR / "lay" / "design.toml"
    path.write_text(path.read_text().replace("# ELITE_COUNT = 10", "elite_count = 4"))
    s = settings.Settings(flow={"MAX_SIZE": 3})
    d = design_mod.load("lay")
    cfg = config_mod.build(s, d, ["ELITE_COUNT=6", "DRAW_MODE=manual"])
    assert cfg.MAX_SIZE == 3 and cfg.origin("MAX_SIZE") == "aion.toml"
    assert cfg.ELITE_COUNT == 6 and cfg.origin("ELITE_COUNT") == "--set"
    assert cfg.DRAW_MODE == "manual"
    assert cfg.AGENT == s.default_agent and cfg.origin("AGENT") == "aion.toml"
    cfg2 = config_mod.build(s, d)
    assert cfg2.ELITE_COUNT == 4 and cfg2.origin("ELITE_COUNT") == "design.toml"


def test_config_refuses_typos_and_bad_choices():
    cfg = config_mod.Config()
    with pytest.raises(ValueError, match="unknown setting"):
        cfg.apply_assignments(["ELITE_COUTN=3"])
    with pytest.raises(ValueError, match="not one of"):
        cfg.apply_assignments(["DRAW_MODE=Auto"])
    cfg.apply_assignments(["ELITE_COUNT=none", "LENIENT=yes"])
    assert cfg.ELITE_COUNT is None and cfg.LENIENT is True


# ---------------------------------------------------------------------
def test_agent_profiles_load_and_inherit():
    names = agent_profile.available()
    assert settings.get().default_agent in names
    for name in names:
        p = agent_profile.load(name)
        assert {"prompts/layout.md", "prompts/ext_layout.md"} <= set(p.files)
        assert "aion-layout" in p.skills()
    child = agent_profile.load("claude-sonnet")
    assert child.chain == ["claude-sonnet", "claude-opus"]
    assert child.model == "claude-sonnet-5-5"
    assert child.permission_mode == agent_profile.load("claude-opus").permission_mode


def test_agent_overrides_change_the_digest():
    base = agent_profile.load("claude-opus")
    tuned = agent_profile.load("claude-opus", ["effort=max", "max_usd_per_cell=12.5",
                                               "max_turns=3"])
    assert (tuned.effort, tuned.max_usd_per_cell, tuned.max_turns) == ("max", 12.5, 3)
    assert tuned.overrides == {"effort": "max", "max_usd_per_cell": 12.5, "max_turns": 3}
    assert tuned.digest() != base.digest()
    with pytest.raises(SystemExit):
        agent_profile.load("claude-opus", ["effort=ludicrous"])
    with pytest.raises(SystemExit):
        agent_profile.load("claude-opus", ["nonsense=1"])


def test_agent_materializes_a_session_workspace(tmp_path):
    p = agent_profile.load("claude-opus")
    home = p.materialize(tmp_path / "ws", {"AION_LAYOUT_TOOL": "/tool", "PYTHON": "py",
                                           "AION_FRAMEWORK": "/fw", "AION_RUN_DIR": "/run"})
    settings_json = json.loads((home / ".claude" / "settings.json").read_text())
    command = settings_json["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert command.startswith("py ") and "/tool/scripts/hook_cell_check.py" in command
    assert (home / ".claude" / "skills" / "aion-layout" / "SKILL.md").is_file()
    assert (home / "CLAUDE.md").is_file()
    record = json.loads((home / "profile.json").read_text())
    assert record["digest"] == p.digest() and record["chain"] == ["claude-opus"]


# ---------------------------------------------------------------------
@pytest.mark.parametrize("language,key,flow", [
    ("vhdl", "VHDL_FILES", "VHDLClassic"),
    ("verilog", "VERILOG_FILES", "Classic"),
    ("systemverilog", "VERILOG_FILES", "Classic"),
])
def test_new_design_and_its_librelane_configs(sandbox, language, key, flow):
    name = f"d_{language}"
    scaffold.new_design(name, language=language, period_ns=12.5, die=(90, 80))
    ws, d = make_ws(name)
    assert d.problems() == []
    assert d.top == name and d.sdc.name == f"{name}.sdc" and d.has_testbench
    assert "12.5" in d.sdc.read_text()
    cfg = config_mod.build(settings.get(), d)

    synth = json.loads((librelane.prepare(ws, cfg, "synth") / "config.json").read_text())
    assert synth["DESIGN_NAME"] == name
    assert isinstance(synth["meta"]["flow"], list)
    assert synth["meta"]["flow"][0] == ("Yosys.VHDLSynthesis" if language == "vhdl"
                                        else "Yosys.JsonHeader")
    assert all(v.startswith("dir::") for v in synth[key])
    assert synth["PNR_SDC_FILE"] == synth["SIGNOFF_SDC_FILE"]
    assert synth.get("USE_SLANG", False) == (language == "systemverilog")
    assert not any(k.startswith("//") or k == "_modes" for k in synth)

    base = json.loads((librelane.prepare(ws, cfg, "baseline") / "config.json").read_text())
    assert base["meta"]["flow"] == flow and base["DIE_AREA"] == [0, 0, 90, 80]

    cfg.set("LENIENT", True, "test")
    lenient = json.loads((librelane.prepare(ws, cfg, "pnr") / "config.json").read_text())
    assert lenient["ERROR_ON_LVS_ERROR"] is False


def test_new_design_refuses_to_clobber(sandbox):
    scaffold.new_design("twice")
    with pytest.raises(SystemExit):
        scaffold.new_design("twice")
    with pytest.raises(SystemExit):
        scaffold.new_design("9bad")


def test_paths_outside_the_framework_are_refused(tmp_path):
    with pytest.raises(workspace.PathError):
        workspace.container(tmp_path / "x")
    mount = settings.get().container_mount
    assert workspace.container(workspace.AION_FLOW) == f"{mount}/aion_flow"
    assert workspace.host(f"{mount}/aion_flow") == workspace.AION_FLOW


# ---------------------------------------------------------------------
def test_pdk_extension_splices_into_every_corner(sandbox):
    scaffold.new_design("ext")
    ws, _ = make_ws("ext")
    new_cell(ws, "AION_t_1")
    with pytest.raises(StepFailed, match="no characterized Liberty"):
        pdk_ext.build(ws, quiet=True)
    views = pdk_ext.build(ws, provisional=True, quiet=True)
    assert views.cells == ("AION_t_1",) and set(views.libraries) == set(ws.pdk.corners)
    for lib in views.libraries.values():
        assert "cell (AION_t_1)" in lib.read_text()
    entry = json.loads(views.tech_dict.read_text())["cells"]["AION_t_1"]
    assert entry["mineable"] is False and entry["pins"]["Y"] == "output"
    assert entry["function"] == "!(A&B)"


# ---------------------------------------------------------------------
def test_step_selection():
    keys = lambda tokens: [s.key.split("_")[0] for s in steps.expand(tokens)]  # noqa: E731
    assert keys([]) == [str(i) for i in range(1, 10)]
    assert keys(["2..4"]) == ["2", "3", "4"]
    assert keys(["7-"]) == ["7", "8", "9"]
    assert keys(["rew", "1"]) == ["3", "1"]
    with pytest.raises(KeyError):
        steps.expand(["nope"])
    assert steps.upstream_of(steps.BY_KEY["7_pnr"])[0] == "1_synth"


@pytest.mark.parametrize("line,loss,allowed", [
    ("COMPARE: WIN  area -30.0%  (x)  worst delay -5.0%", None, True),
    ("COMPARE: LOSS  area -30.0%  (x)  worst delay +4.0%", None, False),
    ("COMPARE: LOSS  area -30.0%  (x)  worst delay +4.0%", 5.0, True),
    ("COMPARE: LOSS  area -30.0%  (x)  worst delay +6.0%", 5.0, False),
    ("COMPARE: LOSS  area +2.0%  (x)  worst delay -9.0%", 50.0, False),
])
def test_extension_publish_gate(line, loss, allowed):
    assert ext_build.may_publish("RESULT: PASS\n" + line + "\n", loss)[0] is allowed
    assert ext_build.may_publish("RESULT: FAIL\n" + line + "\n", 100.0)[0] is False


def test_a_package_left_in_include_dirs_is_a_problem(sandbox):
    scaffold.new_design("pkg", language="systemverilog")
    d = design_mod.DESIGNS_DIR / "pkg"
    (d / "src" / "include").mkdir()
    (d / "src" / "include" / "pkg_p.sv").write_text("package pkg_p;\n  parameter int W = 8;\nendpackage\n")
    toml = d / "design.toml"
    toml.write_text(toml.read_text().replace('# include_dirs = ["src/include"]', 'include_dirs = ["src/include"]'))
    assert any("declares a package" in p for p in design_mod.load("pkg").problems())
    toml.write_text(toml.read_text().replace('rtl = ["src/rtl/*.sv"]',
                                             'rtl = ["src/include/pkg_p.sv", "src/rtl/*.sv"]'))
    assert design_mod.load("pkg").problems() == []
