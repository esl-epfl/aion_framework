Use the aion-layout skill. Draw the standard-cell layout for $cell.

The ONLY file you may create or edit is:
    $generator

Its target netlist is $netlist.
That netlist is a HAND-DESIGNED PDK-extension cell: it is the source of truth
for both the connectivity and the device widths that LVS grades against, it
has already been proven in SPICE against the PDK reference, and you must
NEVER modify it. Never hand-edit a GDS, a report, or anything under
$build_dir — they are evidence.

Check your work, and read what to fix, with:
    $verify
    $evidence
Run them EARLY and often. They take about three minutes and they answer
questions about geometry that reasoning answers slowly and worse. Write an
incomplete generator and verify it rather than thinking a complete one
through: your first verify should happen before the cell is finished.
Work until `verify` prints `RESULT: PASS` at the start of a line. Exactly one
line of that output starts at column 0 and it is the verdict; everything else
is indented. Do not read make's exit status as the verdict.

This is turn $turn of $max_turns. Stop as soon as the generator is written and
verifying; do not report success you have not seen a RESULT: PASS for.

$sections
$briefing
