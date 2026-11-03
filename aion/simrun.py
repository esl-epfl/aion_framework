# ================================================================
#  SPDX-FileCopyrightText:    2026 Filippo Quadri
#  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
#  Description:               One cocotb run, from a JSON spec
#
#    python -m aion.simrun <spec.json>
#
#  Run as its own process so the flow's runner can tee it to a log, time it
#  out and kill it like any other tool.  aion/sim.py writes the spec.
#
#  The exit status is the testbench's, not the simulator's: simulators exit
#  0 whether the tests passed or not, so the results file is read back and a
#  single failed (or missing) test fails the run.
# ================================================================

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list) -> int:
    if len(argv) != 1:
        print("usage: python -m aion.simrun <spec.json>", file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[0]).read_text())

    from cocotb_tools.runner import get_results, get_runner

    # The runner hands the simulator PYTHONPATH = our sys.path, and the
    # testbench lives in the design's tb/, not in the build directory.
    sys.path.insert(0, spec["test_dir"])

    runner = get_runner(spec["simulator"])
    build_dir = Path(spec["build_dir"])
    timescale = tuple(spec["timescale"]) if spec.get("timescale") else None

    kwargs = dict(
        hdl_toplevel=spec["toplevel"],
        build_dir=build_dir,
        build_args=spec.get("build_args", []),
        includes=spec.get("includes", []),
        defines=spec.get("defines", {}),
        timescale=timescale,
        waves=spec.get("waves", False),
        always=True,
    )
    # Language from the extension (.vhd/.vhdl -> VHDL, .v/.sv -> Verilog).
    kwargs["sources"] = spec["sources"]
    runner.build(**kwargs)

    results = runner.test(
        test_module=spec["test_module"],
        hdl_toplevel=spec["toplevel"],
        hdl_toplevel_lang=spec["lang"] if spec["lang"] == "vhdl" else "verilog",
        # The build directory, not the testbench's: GHDL's LLVM/GCC backends
        # elaborate an executable there and `ghdl -r` looks for it in cwd.
        # The testbench is found through PYTHONPATH instead.
        test_dir=build_dir,
        build_dir=build_dir,
        test_args=spec.get("test_args", []),
        plusargs=spec.get("plusargs", []),
        extra_env=dict(spec.get("env", {})),
        waves=spec.get("waves", False),
        timescale=timescale,
        results_xml=str(build_dir / "results.xml"),
    )

    try:
        total, failed = get_results(results)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"SIM: ERROR no readable results ({exc})")
        return 1
    if total == 0:
        print("SIM: FAIL no test ran")
        return 1
    verdict = "PASS" if failed == 0 else "FAIL"
    print(f"SIM: {verdict} {total - failed}/{total} test(s) passed  "
          f"[{spec.get('stage', '?')}, {spec['simulator']}]")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
