# Vendored: secworks SHA-256

* upstream: https://github.com/secworks/sha256
* commit:   837c5cc396f001d18f2c765721c585716eb439ae
* license:  BSD-2-Clause (see LICENSE and each file's header)

Copied unmodified:

| here | upstream |
|------|----------|
| `rtl/sha256.v`, `rtl/sha256_core.v`, `rtl/sha256_w_mem.v`, `rtl/sha256_k_constants.v` | `src/rtl/` |

Not copied: `src/interfaces/` (AXI4 and stream wrappers), `src/model/`
(Python model), `src/tb/` (Verilog testbenches; `tb_sha256.v` is ported to
cocotb in `../../tb/test_sha256.py`), `toolruns/`, `data/`.
