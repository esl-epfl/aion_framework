# sha256

The [secworks SHA-256](https://github.com/secworks/sha256) core, SHA-224 and
SHA-256, one 64-round block in 66 cycles. The top level is upstream's own
`sha256`: a 32-bit register interface (`cs`, `we`, `address[7:0]`,
`write_data`, `read_data`, `error`) in front of `sha256_core`, so the die has
about 75 pins rather than the core's 512-bit block and 256-bit digest ports.

```
design.toml                  top, sources, testbench, flow knobs
src/sha256/                  upstream RTL, vendored unmodified (VENDOR.md: commit, file list)
tb/test_sha256.py            cocotb: upstream's tb_sha256.v, ported (RTL and every netlist)
constraints/sha256.sdc       timing constraints (20 ns)
librelane/config.json        LibreLane template (+ per-mode overlays)
pdk_extension/               hand-designed cells for this design (git-ignored)
```

The testbench is upstream's `tb_sha256.v` test cases, through the same
register interface: name and version, the NIST SHA-224 and SHA-256 single-
and double-block examples, and the nine-block regression. On top of those,
`random_messages` hashes a few padded random messages in both modes and
checks them against `hashlib`.

```bash
make sim  DESIGN=sha256                 # RTL testbench (Icarus)
make sim  DESIGN=sha256 STAGE=synth     # the same, on 1_synth's netlist
make flow DESIGN=sha256                 # all nine steps
```

`DIE_AREA` (600x600 µm) comes from a host-side Yosys estimate of ~178k µm²
of cells (1807 flip-flops, at `PL_TARGET_DENSITY_PCT` 60). Resize it once
`1_synth` reports the cell area.

Everything the flow produces lands in `runs/sha256/<tag>/`.
