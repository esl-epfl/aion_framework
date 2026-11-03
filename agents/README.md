# Drawing-agent profiles

One directory per profile. Everything a session is given comes out of it.

```
<name>/
  agent.toml        how the session runs (see claude-opus/agent.toml for every key)
  prompts/
    layout.md       task prompt for a mined cell (step 6)
    ext_layout.md   task prompt for a hand-designed PDK-extension cell
  skills/<skill>/   Claude Code skills, installed into the session's .claude/skills/
  settings.json     Claude Code settings for the session (the cell-check hook)
  CLAUDE.md         standing instructions, loaded as project memory
```

| profile | what for |
|---|---|
| `claude-opus` | the default: Opus 5.5 at high effort, 12 sessions of 30 min per cell |
| `claude-opus-max` | PDK-extension cells: max effort, 2 h 20 min sessions |
| `claude-sonnet` | cheaper: Sonnet 5.5, 16 sessions |

## Making one

```toml
# agents/my-agent/agent.toml
extends = "claude-opus"
description = "Opus, capped at $15 a cell"
max_usd_per_cell = 15.0
```

Any file the profile does not have comes from the one it extends: add
`prompts/layout.md` to change only the prompt, `skills/<x>/SKILL.md` to add a
skill. Check what it resolves to with `make agent AGENT=my-agent`.

Prompts are `string.Template` text. The draw loop fills `$cell`, `$generator`,
`$netlist`, `$build_dir`, `$tool`, `$verify`, `$evidence` (the exact commands
to run), `$turn`, `$max_turns`, `$sections` (an extension cell's floorplan)
and `$briefing` (the last verdict and evidence packet). `settings.json` gets
`$AION_LAYOUT_TOOL`, `$AION_FRAMEWORK`, `$AION_RUN_DIR` and `$PYTHON`.

## Using one

```bash
make flow DESIGN=<d> STEP=6 AGENT=claude-sonnet
make flow DESIGN=<d> STEP=6 AGENT_SET="effort=max max_turns=20"
```

or `[layout] agent = "<name>"` in the design's `design.toml`. The resolved
profile, with its digest and overrides, is recorded in the run (`run.json`,
`agent/usage.jsonl`, each cell's `agent.json`) and on every published cell
(`provenance.json`).

The `aion-layout` skill here is a copy of
`aion_flow/tools/aion_layout_claude/SKILL.md`, so a profile can change it
without touching the tool. After the tool's skill changes, refresh the copy:

```bash
cp aion_flow/tools/aion_layout_claude/SKILL.md agents/claude-opus/skills/aion-layout/
```
