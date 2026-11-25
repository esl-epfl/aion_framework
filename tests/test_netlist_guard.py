# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               The drawing loop refuses a turn that edits the
#                             LVS reference netlist
# ================================================================

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aion.agent import claude
from aion.agent.draw import Drawer, DrawTask, NetlistGuard, NetlistModified

NETLIST = ".subckt AION_x A B Y VDD VSS\nM0 Y A VSS VSS sg13_lv_nmos w=0.74u l=0.13u\n.ends\n"


def _task(tmp_path):
    netlist = tmp_path / "minimized_spice" / "AION_x_minimized.spice"
    netlist.parent.mkdir()
    netlist.write_text(NETLIST)
    generator = tmp_path / "generators" / "AION_x.py"
    generator.parent.mkdir()
    generator.write_text("# scaffold\n")
    return DrawTask(kind="layout", cell="AION_x", generator=generator,
                    netlist=netlist, build=tmp_path / "build" / "AION_x")


def _drawer(tmp_path, monkeypatch, task, on_turn, passes_after=None):
    """A Drawer whose verify, evidence and agent turn are stand-ins."""
    ws = SimpleNamespace(agent_dir=tmp_path / "agent")
    runner = SimpleNamespace(dry_run=False, log_dir=tmp_path / "logs", quiet=True)
    profile = SimpleNamespace(name="fake", max_turns=3, idle_turns_abort=2,
                              max_usd_per_cell=None, model="m", effort="e",
                              digest=lambda: "sha256:fake")
    drawer = Drawer(ws, runner, profile)
    calls = {"verify": 0}

    def verify(t, turn=None):
        calls["verify"] += 1
        ok = passes_after is not None and calls["verify"] > passes_after
        line = "RESULT: PASS" if ok else "RESULT: FAIL"
        return SimpleNamespace(output=line, verdict=lambda prefix: line)

    def turn(t, verdict, evidence, iteration):
        on_turn(t, iteration)
        return claude.AgentTurn(report="done", cost_usd=0.1)

    monkeypatch.setattr(drawer, "verify", verify)
    monkeypatch.setattr(drawer, "evidence", lambda t, turn=None: "")
    monkeypatch.setattr(drawer, "_turn", turn)
    return drawer


def _usage(tmp_path):
    return [json.loads(l) for l in (tmp_path / "agent" / "usage.jsonl").read_text().splitlines()]


def test_guard_detects_restores_and_keeps_the_edit(tmp_path):
    task = _task(tmp_path)
    guard = NetlistGuard.take(task.netlist)
    assert not guard.changed()
    guard.enforce("AION_x", task.build, "nobody")          # unchanged: no-op

    task.netlist.write_text(NETLIST.replace("w=0.74u", "w=2u"))
    assert guard.changed()
    with pytest.raises(NetlistModified):
        guard.enforce("AION_x", task.build, "a test")
    assert task.netlist.read_text() == NETLIST
    assert "w=2u" in (task.build / "AION_x_minimized.spice.tampered").read_text()


def test_guard_restores_a_deleted_netlist(tmp_path):
    task = _task(tmp_path)
    guard = NetlistGuard.take(task.netlist)
    task.netlist.unlink()
    with pytest.raises(NetlistModified):
        guard.enforce("AION_x", task.build, "a test")
    assert task.netlist.read_text() == NETLIST
    assert not (task.build / "AION_x_minimized.spice.tampered").exists()


def test_a_turn_that_edits_the_netlist_fails_the_cell(tmp_path, monkeypatch):
    task = _task(tmp_path)

    def cheat(t, iteration):
        t.generator.write_text("# drawn\n")
        t.netlist.write_text(NETLIST + "* simplified\n")

    drawer = _drawer(tmp_path, monkeypatch, task, cheat, passes_after=1)
    with pytest.raises(NetlistModified):
        drawer.draw(task)

    assert task.netlist.read_text() == NETLIST
    assert (task.build / "AION_x_minimized.spice.tampered").is_file()
    [entry] = _usage(tmp_path)
    assert entry["netlist_changed"] is True
    record = json.loads((task.build / "agent.json").read_text())
    assert record["netlist_modified"] is True
    assert record["verdict"] != "RESULT: PASS"


def test_a_turn_that_only_edits_the_generator_is_unaffected(tmp_path, monkeypatch):
    task = _task(tmp_path)
    original = NetlistGuard.take(task.netlist).sha256

    def draw(t, iteration):
        t.generator.write_text(f"# drawn, turn {iteration}\n")

    drawer = _drawer(tmp_path, monkeypatch, task, draw, passes_after=1)
    assert drawer.draw(task) == "RESULT: PASS"

    [entry] = _usage(tmp_path)
    assert entry["netlist_changed"] is False
    record = json.loads((task.build / "agent.json").read_text())
    assert record["netlist_modified"] is False
    assert record["netlist_sha256"] == original
    assert not (task.build / "AION_x_minimized.spice.tampered").exists()
