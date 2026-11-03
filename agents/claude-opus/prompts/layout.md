Use the aion-layout skill. Draw the standard-cell layout for $cell.

The ONLY file you may create or edit is:
    $generator

Its target netlist is $netlist.
NEVER modify the netlist: it is the source of truth for both the connectivity
and the device widths that LVS grades against. Never hand-edit a GDS, a
report, or anything under $build_dir — they are evidence.

Check your work, and read what to fix, with:
    $verify
    $evidence
Work until `verify` prints `RESULT: PASS` at the start of a line. Exactly one
line of that output starts at column 0 and it is the verdict; everything else
is indented. Do not read make's exit status as the verdict — make reports its
own failure as 2 whatever the tool returned.

The layout tool lives in $tool; its worked example is
examples/cells/AION_inv_nand2_nor2_1.py there.

This is turn $turn of $max_turns. Stop as soon as the generator is written and
verifying; do not report success you have not seen a RESULT: PASS for.

$sections
$briefing
