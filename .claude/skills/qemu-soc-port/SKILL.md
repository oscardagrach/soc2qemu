---
name: qemu-soc-port
description: Port a new SoC to QEMU end to end with minimal human input - create the workspace, run intake, scaffold, device models, and the boot bring-up loop. Use when the user wants to start, resume, or check on a QEMU SoC port, or says "port <soc> to QEMU".
---

# Port a SoC to QEMU

Entry point for the kit. The kit root is the directory containing `config.env`; all paths
below are relative to it. Read `CLAUDE.md` there if it isn't loaded.

## Arguments
`<soc>` (lowercase, e.g. `rk3568`), optionally a phase to resume from.

## State
- `socs/<soc>/` - workspace: `inputs/` (untrusted user material), `intake/`, `spec/soc.yaml`
  (canonical SoC description), `run.yaml` (how to boot), `bringup/` (runs + LOG.md),
  `STATUS.md` (single source of truth for progress).
- `worktrees/<soc>/` - QEMU git worktree on branch `soc/<soc>`, build in `build/`.

Always read `socs/<soc>/STATUS.md` first and resume from the first phase not marked done.

## Phases

| # | Phase | How | Done when |
|---|---|---|---|
| 0 | Workspace | `scripts/new-soc.sh <soc>` (clones/configures QEMU on first use, takes a few minutes); add `--base libafl` only if the user wants to fuzz this SoC with libafl_qemu - the base can't be switched later | worktree configured |
| 1 | Intake | workflow `soc-intake` with `args: {soc}`, or skill `qemu-soc-intake` for a small input set | `scripts/spec-check.py <soc>` passes |
| 2 | Scaffold | skill `qemu-soc-scaffold` | machine builds, first `bringup-run.py` produces a summary |
| 3 | Devices | workflow `soc-devices` with `args: {soc}` (parallel, one agent per `strategy: new` peripheral) | build OK, qtests pass |
| 4 | Bring-up | workflow `soc-bringup` with `args: {soc, maxIterations}` | last milestone in `run.yaml` reached |

`soc-port` runs phases 1-4 in sequence as one workflow (`args: {soc, reviewSpec: false}`).
Workflows need the user's opt-in: running this skill on the user's request counts.

If `socs/<soc>/inputs/` is empty, stop and ask the user to drop material there (see
`inputs/README.md`). That is the one required human step.

After bring-up, `scripts/fuzz-profile.py <soc>` exports `socs/<soc>/fuzz/profile.yaml`
(address map, polled MMIO reads, snapshot candidates) for a separate fuzzing harness.

## Minimal-interaction rules
- Decide, don't ask. Pick the conservative option, record it in STATUS.md under a
  `decisions` list with the reason, and continue.
- Ask the human only when every source (DTS, TRM, BSP, firmware via Ghidra, boot logs) has
  been tried for a fact that blocks progress. Put the question in STATUS.md "blocked on a
  human" with what was tried, then keep working on anything not blocked by it.
- Never edit `qemu-src/` directly. Never push branches.
- Commit to the `soc/<soc>` branch in the worktree at the end of each phase
  (`git -C worktrees/<soc> commit`), message `<soc>: <phase>: <summary>`.

## Reporting
At the end, report: phase reached, last milestone and run number, number of peripherals
reused/new/stubbed, and anything in "blocked on a human".
