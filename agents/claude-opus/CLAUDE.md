# Standing instructions for the AION drawing agent

You are drawing one IHP SG13G2 standard-cell layout for the AION flow.

- The `aion-layout` skill is the method. Read it before you place a rectangle.
- You write exactly one file: the generator the task names. Nothing else --
  not the netlist, not the tool, not a report, not anything under the build
  directory.
- The verdict is the `RESULT:` line `make verify` prints at column 0, never
  your own reading of DRC or LVS output, and never make's exit status.
- Verify early. An incomplete generator on disk that has been verified beats a
  complete one that exists only in your reasoning.
- After every edit a hook prints an advisory static check of the generator
  (build, geometry, device count, shorts). It is a hint, not a verdict.
