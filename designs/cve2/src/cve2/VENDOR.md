# Vendored: CVE2 (CV32E20)

* upstream: https://github.com/openhwfoundation/cve2
* commit:   d7f21568028a1114be663599bd42145dccf28928
* license:  Apache-2.0 / Solderpad (see LICENSE and each file's header)

Copied unmodified:

| here | upstream |
|------|----------|
| `rtl/*.sv`, `rtl/cve2_pmp_reset_default.svh` | `rtl/` (all but the files below) |
| `vendor/prim/prim_assert*.sv[h]`, `prim_ram_1p_pkg.sv` | `vendor/lowrisc_ip/ip/prim/rtl/` |
| `vendor/dv_utils/dv_fcov_macros.svh` | `vendor/lowrisc_ip/dv/sv/dv_utils/` |

Not copied: `cve2_clock_gate.sv` (simulation only, replaced by
`../rtl/cve2_clock_gate.sv`), `cve2_top_tracing.sv`, `cve2_tracer*.sv`.
